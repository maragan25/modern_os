"""Starts the whole project and checks that it can work.

    start.bat            (Windows: uses the project's .venv)
    python start.py      (any system, inside the venv)

    --check   only look, never start or change anything
    --stop    stop subscriber.py and api.py (the services are left running)
    -y        answer "yes" to the questions (fix config.h, install packages)
    --every N store one reading per device every N seconds (default: see subscriber.py; 0 = every reading)

Order: Python packages -> broker IP in esp32_mqtt/config.h -> services (Mosquitto, PostgreSQL)
-> ports -> Mosquitto settings and firewall -> database password and tables -> broker test
-> start subscriber.py and api.py in their own Windows Terminal tabs -> which boards are online.

Every check is advisory except the ones marked FAIL, which stop the start: starting anyway
would only produce a confusing error later.
"""

import argparse
import getpass
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

SELF = Path(__file__).resolve()
ROOT = SELF.parent
IS_WIN = os.name == "nt"
CONFIG_H = ROOT / "esp32_mqtt" / "config.h"
MOSQ_CONF = Path(os.environ.get("MOSQUITTO_CONF", r"C:\Program Files\Mosquitto\mosquitto.conf"))
DB_DSN = os.environ.get("IOT_DB_DSN", "host=localhost port=5432 dbname=iot user=postgres")
MQTT_PORT, WS_PORT = 1883, 9001
API_PORT = int(os.environ.get("IOT_API_PORT", 8000))
SERVICES = [("Mosquitto", r"^mosquitto$"), ("PostgreSQL", r"^postgresql")]
CHILDREN = [("subscriber", "subscriber.py", "subscriber (MQTT -> database)"),
            ("api", "api.py", "api + dashboard")]

COLOR = sys.stdout.isatty()
if IS_WIN and COLOR:
    os.system("")   # switches on colour codes in the Windows console
LEVELS = {"ok": ("[ OK ]", "32"), "warn": ("[WARN]", "33"), "fail": ("[FAIL]", "31"), "info": ("[ .. ]", "36")}
problems = {"warn": 0, "fail": 0}


def say(level, msg, detail=""):
    tag, col = LEVELS[level]
    if level in problems:
        problems[level] += 1
    print(f"\033[{col}m{tag}\033[0m {msg}" if COLOR else f"{tag} {msg}")
    for line in detail.splitlines():
        print("       " + line)


def heading(text):
    print(f"\n--- {text}")


def ask(question, default_yes, assume_yes):
    if assume_yes:
        print(f"       {question} -> yes (-y)")
        return True
    try:
        answer = input(f"       {question} [{'Y/n' if default_yes else 'y/N'}] ").strip().lower()
    except EOFError:
        return default_yes and False
    return default_yes if not answer else answer.startswith("y")


# ---------------------------------------------------------------- system helpers

def run(cmd, timeout=30):
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def powershell(script, timeout=30):
    """Runs a PowerShell snippet and returns what it printed (UTF-8), or None."""
    script = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script
    rc, out = run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], timeout)
    return out if rc == 0 and out.strip() else None


def as_list(text):
    """PowerShell's ConvertTo-Json gives an object for one result and a list for several."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return data if isinstance(data, list) else [data]


def port_open(port, host="127.0.0.1"):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def listeners():
    """Every listening TCP port: [{addr, port, pid, name}]. Windows only (None elsewhere)."""
    if not IS_WIN:
        return None
    out = powershell(
        "Get-NetTCPConnection -State Listen | ForEach-Object { [pscustomobject]@{ addr = $_.LocalAddress; "
        "port = $_.LocalPort; pid = $_.OwningProcess; name = (Get-Process -Id $_.OwningProcess "
        "-ErrorAction SilentlyContinue).ProcessName } } | ConvertTo-Json -Compress")
    return as_list(out)


def find_procs(script):
    """Running python processes started on `script` (e.g. 'subscriber.py'): [(pid, command line)]."""
    found = []
    if IS_WIN:
        out = powershell("Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' } | "
                         "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress")
        rows = [(r.get("ProcessId"), r.get("CommandLine") or "") for r in as_list(out) or []]
    else:
        rc, out = run(["ps", "-eo", "pid=,args="])
        rows = []
        for line in out.splitlines():
            pid, _, args = line.strip().partition(" ")
            if pid.isdigit():
                rows.append((int(pid), args))
    pattern = re.compile(r"(^|[\\/ \"])" + re.escape(script) + r"(\"|\s|$)")
    for pid, cmdline in rows:
        if pid != os.getpid() and pattern.search(cmdline) and "start.py" not in cmdline:
            found.append((pid, cmdline))
    return found


# ---------------------------------------------------------------- checks

def check_packages(assume_yes, check_only):
    heading("Python packages")
    missing = []
    for module, pip_name in (("paho.mqtt.client", "paho-mqtt"), ("psycopg", "psycopg[binary]")):
        try:
            __import__(module)
        except ImportError:
            missing.append(pip_name)
    if not missing:
        say("ok", f"paho-mqtt and psycopg are installed ({sys.executable})")
        return True
    say("fail", f"missing packages: {', '.join(missing)}",
        "Run this start script inside the project's .venv (start.bat does that).")
    if not check_only and (ROOT / "requirements.txt").exists() and ask("Install them from requirements.txt now?", True, assume_yes):
        rc = subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
        return rc == 0
    return False


def pc_ipv4s():
    main, ips = None, set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))   # sends nothing: only asks the OS which address it would use
            main = s.getsockname()[0]
    except OSError:
        pass
    try:
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    if main:
        ips.add(main)
    return main, {i for i in ips if not i.startswith("127.") and not i.startswith("169.254.")}


HOST_LINE = re.compile(r'^(\s*const\s+char\s*\*\s*MQTT_HOST\s*=\s*")([^"]*)(")', re.M)


def check_config_ip(assume_yes, check_only):
    heading("Broker address in esp32_mqtt/config.h")
    if not CONFIG_H.exists():
        say("warn", "esp32_mqtt/config.h does not exist: copy config.example.h to config.h before uploading a board")
        return
    m = HOST_LINE.search(CONFIG_H.read_bytes().decode("utf-8", errors="replace"))
    if not m:
        say("warn", "found no MQTT_HOST line in config.h")
        return
    host = m.group(2)
    main, ips = pc_ipv4s()
    if not ips:
        say("warn", "could not find this computer's IPv4 address (not on a network?)", f"config.h says MQTT_HOST = {host}")
        return
    try:
        resolved = socket.gethostbyname(host)
    except OSError:
        resolved = host
    if resolved in ips:
        say("ok", f"MQTT_HOST {host} is an address of this computer" + (f" ({main} is the one used for the internet)" if main and main != resolved else ""))
        return
    say("fail", f"MQTT_HOST in config.h is {host}, but this computer is {main or ', '.join(sorted(ips))}",
        "The boards would connect to a different address and never reach this broker.\n"
        "A router can hand out a new address after a restart; reserving one for this computer prevents that.")
    if check_only or not main:
        return
    if ask(f"Change MQTT_HOST in config.h to {main}?", True, assume_yes):
        text = CONFIG_H.read_bytes().decode("utf-8", errors="replace")   # bytes: keeps the file's line endings
        CONFIG_H.write_bytes(HOST_LINE.sub(lambda mm: mm.group(1) + main + mm.group(3), text, count=1).encode("utf-8"))
        problems["fail"] -= 1
        say("ok", f"config.h now says {main} (was {host})",
            "Every board must be uploaded again: the address is stored inside its program.")


def service_states():
    out = powershell("Get-Service | Where-Object { $_.Name -match '^(mosquitto|postgresql)' } | "
                     "Select-Object Name, @{n='state';e={$_.Status.ToString()}} | ConvertTo-Json -Compress")
    return {r["Name"]: r["state"] for r in as_list(out) or []}


def start_service(name):
    rc, _ = run(["net", "start", name], 60)
    if rc != 0:
        say("info", f"starting {name} needs administrator rights: confirm the Windows prompt ...")
        run(["powershell", "-NoProfile", "-Command",
             f"Start-Process -FilePath net -ArgumentList 'start','{name}' -Verb RunAs -Wait -WindowStyle Hidden"], 90)
    for _ in range(20):
        if service_states().get(name, "").lower() == "running":
            return True
        time.sleep(1)
    return False


def check_services(check_only):
    heading("Services")
    if not IS_WIN:
        say("info", "service check skipped (not Windows). macOS: brew services start mosquitto postgresql")
        return
    states = service_states()
    if not states:
        say("warn", "could not read the Windows services")
        return
    for label, pattern in SERVICES:
        names = sorted((n for n in states if re.search(pattern, n, re.I)), reverse=True)
        if not names:
            say("fail", f"no {label} service found: is it installed?")
            continue
        running = [n for n in names if states[n].lower() == "running"]
        if running:
            say("ok", f"{label} is running ({running[0]})")
            continue
        name = names[0]
        if check_only:
            say("warn", f"{label} ({name}) is {states[name]}: start.py would start it")
        elif start_service(name):
            say("ok", f"{label} ({name}) started")
        else:
            say("fail", f"{label} ({name}) did not start", f"Try it by hand in an administrator cmd: net start {name}")


def check_ports():
    heading("Ports (is anything else in the way?)")
    rows = listeners()
    if rows is None:
        for port, what in ((MQTT_PORT, "MQTT"), (WS_PORT, "MQTT over WebSocket")):
            say("ok" if port_open(port) else "warn", f"port {port} ({what}) " + ("answers" if port_open(port) else "does not answer"))
        say("info", "who owns the ports could not be checked" + ("" if IS_WIN else " (only on Windows)"))
        return
    for _ in range(5):   # a service that has just started needs a moment before its port opens
        if any(int(r["port"]) == MQTT_PORT for r in rows):
            break
        time.sleep(1.5)
        rows = listeners() or rows
    by_port = {}
    for r in rows:
        by_port.setdefault(int(r["port"]), []).append(r)
    mosq_ports = sorted({int(r["port"]) for r in rows if (r.get("name") or "").lower().startswith("mosquitto")})
    mosq_procs = len({r["pid"] for r in rows if (r.get("name") or "").lower().startswith("mosquitto")})
    for port, what in ((MQTT_PORT, "MQTT"), (WS_PORT, "MQTT over WebSocket")):
        owners = by_port.get(port, [])
        if not owners:
            say("fail", f"nothing listens on port {port} ({what})", "Is Mosquitto running, and does mosquitto.conf have the listeners from the README?")
            continue
        names = {(o.get("name") or "?") for o in owners}
        if not all(n.lower().startswith("mosquitto") for n in names):
            say("fail", f"port {port} ({what}) is held by {', '.join(sorted(names))}, not Mosquitto",
                "Stop that program, or the boards and the dashboard will talk to the wrong thing.")
        elif all(o["addr"] in ("127.0.0.1", "::1") for o in owners):
            say("fail", f"Mosquitto on port {port} only listens on this computer (127.0.0.1)",
                "The ESP32 could never connect (state -2). In mosquitto.conf use 'listener 1883' without an address.")
        else:
            say("ok", f"port {port} ({what}) is held by Mosquitto on all addresses")
    extra = [p for p in mosq_ports if p not in (MQTT_PORT, WS_PORT)]
    if extra:
        say("warn", f"Mosquitto also listens on port(s) {', '.join(map(str, extra))}",
            "That can be a second broker or an old config; boards or clients on those ports will not see our data.")
    if mosq_procs > 1:
        say("warn", f"{mosq_procs} Mosquitto processes are running", "Probably the service plus one started by hand; stop one of them.")


def check_mosquitto_conf():
    if not IS_WIN:
        return
    heading("Mosquitto settings")
    try:
        text = MOSQ_CONF.read_text(encoding="utf-8", errors="replace")
    except OSError:
        say("warn", f"cannot read {MOSQ_CONF}", "Check by hand that it has the lines from README step 1.")
        return
    lines = [re.sub(r"\s+", " ", l.split("#")[0]).strip().lower() for l in text.splitlines()]
    needed = {"listener 1883": "ESP32 and subscriber.py", "listener 9001": "the dashboard", "protocol websockets": "the dashboard",
              "allow_anonymous true": "no password"}
    missing = [f"{k}  ({v})" for k, v in needed.items() if k not in lines]
    if missing:
        say("warn", "mosquitto.conf lacks: " + "; ".join(missing), "See README step 1.")
    else:
        say("ok", "mosquitto.conf has the listeners for 1883 and 9001 (WebSocket) and anonymous access")


def check_firewall():
    if not IS_WIN:
        return
    heading("Firewall (can the ESP32 reach this computer?)")
    out = powershell(
        "$rules = Get-NetFirewallPortFilter -Protocol TCP | Where-Object { $_.LocalPort -contains '1883' } | "
        "Get-NetFirewallRule | Where-Object { $_.Enabled -eq 'True' -and $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } | "
        "ForEach-Object { [pscustomobject]@{ name = $_.DisplayName; profile = $_.Profile.ToString() } }; "
        "$nets = Get-NetConnectionProfile | ForEach-Object { $_.NetworkCategory.ToString() }; "
        "[pscustomobject]@{ rules = @($rules); nets = @($nets) } | ConvertTo-Json -Compress -Depth 4", 60)
    try:
        data = json.loads(out) if out else None
    except ValueError:
        data = None
    if not data:
        say("warn", "could not read the firewall rules", "Check by hand that port 1883 is allowed inbound (README step 2).")
        return
    rules, nets = data.get("rules") or [], data.get("nets") or []
    if isinstance(rules, dict):
        rules = [rules]
    if isinstance(nets, str):
        nets = [nets]
    if not rules:
        say("fail", "no inbound firewall rule allows TCP port 1883", "Boards will fail with state -2. README step 2 has the one-line command.")
        return
    covered = [r for r in rules if any(n.lower() in r["profile"].lower() for n in nets) or r["profile"].lower() in ("any", "all")]
    if covered:
        say("ok", f"firewall rule '{covered[0]['name']}' allows port 1883 on this network ({', '.join(nets) or 'unknown'})")
    else:
        say("fail", f"the firewall rule only covers {rules[0]['profile']} networks, but this Wi-Fi is {', '.join(nets) or 'unknown'}",
            "Set the Wi-Fi network profile to Private (Settings > Network > Wi-Fi > your network), as in README step 2.")


def connect_db(assume_yes):
    """Returns the password that works (or None). Takes PGPASSWORD if set, otherwise asks."""
    heading("Database")
    import psycopg
    password = os.environ.get("PGPASSWORD")
    for attempt in range(3):
        if password is None:
            password = getpass.getpass("       PostgreSQL password (not shown, not saved): ")
        try:
            with psycopg.connect(DB_DSN, password=password, connect_timeout=5) as db:
                n, last = db.execute("SELECT count(*), max(recorded_at) FROM readings").fetchone()
            say("ok", f"database connected, readings table has {n} rows" + (f", newest {last:%Y-%m-%d %H:%M}" if last else ""))
            return password
        except psycopg.errors.UndefinedTable:
            say("fail", "the database exists but has no readings table", 'Create it: psql -U postgres -d iot -f schema.sql  (README step 4)')
            return None
        except psycopg.OperationalError as e:
            msg = str(e).strip()
            if "password authentication failed" in msg or "no password supplied" in msg:
                say("warn", "wrong PostgreSQL password" + (f" (attempt {attempt + 1} of 3)" if attempt < 2 else ""))
                password = None
                continue
            say("fail", "cannot connect to PostgreSQL", msg)
            return None
    say("fail", "three wrong passwords: giving up")
    return None


def mqtt_probe(transport, port):
    import paho.mqtt.client as mqtt
    done, result = threading.Event(), {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"start-check-{transport}", transport=transport)

    def on_connect(cl, ud, flags, rc, props):
        result["rc"] = rc
        done.set()
    c.on_connect = on_connect
    try:
        c.connect("127.0.0.1", port, 5)
        c.loop_start()
    except Exception as e:   # noqa: BLE001 - any failure just means "not reachable"
        return False, str(e)
    ok = done.wait(4)
    c.loop_stop()
    try:
        c.disconnect()
    except Exception:   # noqa: BLE001
        pass
    return bool(ok and not result["rc"].is_failure), "" if ok else "no answer within 4 s"


def check_broker():
    heading("Broker test")
    for transport, port, what in (("tcp", MQTT_PORT, "MQTT (boards, subscriber)"), ("websockets", WS_PORT, "MQTT over WebSocket (dashboard)")):
        ok, why = mqtt_probe(transport, port)
        say("ok" if ok else "fail", f"{what} on port {port}: " + ("connected" if ok else f"not reachable ({why})"))


# ---------------------------------------------------------------- launching

def launch(items, password, check_only):
    heading("Starting")
    todo = []
    for name, script, title in items:
        procs = find_procs(script)
        if name == "subscriber" and procs:
            say("warn", f"subscriber.py is already running (PID {procs[0][0]}): not starting a second one",
                "Two subscribers would save every reading twice. Use start.py --stop first if you want a restart.")
            continue
        if name == "api":
            if procs:
                say("ok", f"api.py is already running (PID {procs[0][0]}) on port {API_PORT}")
                continue
            if port_open(API_PORT):
                say("fail", f"port {API_PORT} is used by something else, so api.py cannot start",
                    "Stop that program, or set IOT_API_PORT to another port.")
                continue
        todo.append((name, script, title))
    if check_only:
        for name, script, title in todo:
            say("info", f"would start {script} in its own tab")
        return
    if not todo:
        return
    handoffs = []
    for name, script, title in todo:
        fd, path = tempfile.mkstemp(prefix="iot-pw-")
        with os.fdopen(fd, "w") as f:
            f.write(password or "")
        handoffs.append(path)   # the child reads it once and deletes it: the password never sits in a command line
    tab = lambda n, t, p: ["new-tab", "--title", t, "-d", str(ROOT), sys.executable, str(SELF), "--child", n, "--handoff", p]
    if IS_WIN and shutil.which("wt"):
        cmd = ["wt", "-w", "0"]
        for i, ((n, s, t), p) in enumerate(zip(todo, handoffs)):
            if i:
                cmd.append(";")
            cmd += tab(n, t, p)
        subprocess.Popen(cmd)
        say("ok", "opened Windows Terminal tabs: " + ", ".join(s for _, s, _ in todo))
    else:
        for (n, s, t), p in zip(todo, handoffs):
            child = [sys.executable, str(SELF), "--child", n, "--handoff", p]
            if IS_WIN:
                subprocess.Popen(child, creationflags=subprocess.CREATE_NEW_CONSOLE, cwd=ROOT)
            else:
                (ROOT / "logs").mkdir(exist_ok=True)
                subprocess.Popen(child, cwd=ROOT, stdin=subprocess.DEVNULL, start_new_session=True,
                                 stdout=open(ROOT / "logs" / f"{n}.log", "w"), stderr=subprocess.STDOUT)
        say("ok", "started in their own windows: " + ", ".join(s for _, s, _ in todo)
            + ("" if IS_WIN else "  (logs/ folder)") + ("" if not IS_WIN else "  (Windows Terminal not found)"))


def run_child(name, handoff):
    script = ROOT / dict((n, s) for n, s, _ in CHILDREN)[name]
    env = os.environ.copy()
    if handoff:
        try:
            pw = Path(handoff).read_text()
            Path(handoff).unlink()
            if pw:
                env["PGPASSWORD"] = pw
        except OSError:
            pass
    print(f"== {script.name}   (Ctrl+C stops it)\n", flush=True)
    try:
        rc = subprocess.call([sys.executable, str(script)], env=env, cwd=ROOT)
    except KeyboardInterrupt:
        rc = 0
    print(f"\n{script.name} stopped (exit code {rc}).")
    if sys.stdin.isatty():
        try:
            input("Press Enter to close this tab ... ")
        except EOFError:
            pass


def summary(ip, seconds=12):
    heading(f"Boards (listening {seconds} s)")
    import paho.mqtt.client as mqtt
    status, data = {}, {}
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="start-summary")

    def on_connect(cl, ud, flags, rc, props):
        cl.subscribe([("sensors/+/status", 1), ("sensors/+/data", 0)])

    def on_message(cl, ud, msg):
        dev = msg.topic.split("/")[1]
        if msg.topic.endswith("/status"):
            status[dev] = msg.payload.decode(errors="replace")
        else:
            data[dev] = data.get(dev, 0) + 1
    c.on_connect, c.on_message = on_connect, on_message
    try:
        c.connect("127.0.0.1", MQTT_PORT, 5)
        c.loop_start()
        time.sleep(seconds)
        c.loop_stop()
        c.disconnect()
    except Exception as e:   # noqa: BLE001
        say("warn", f"could not listen: {e}")
        return
    devices = sorted(set(status) | set(data))
    if not devices:
        say("warn", "no board has sent anything", "Boards publish every 5 s. Power one up and watch for the 3-second flash on its LED.")
    for d in devices:
        live = data.get(d, 0)
        say("ok" if live else "warn", f"{d}: " + (f"{live} readings in {seconds} s" if live else f"no readings now (last status: {status.get(d, 'none')})"))
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{API_PORT}/api/devices", timeout=3) as r:
            say("ok", f"api answers on port {API_PORT} ({len(json.load(r))} devices in the database)")
    except Exception as e:   # noqa: BLE001
        say("fail", f"api.py does not answer on port {API_PORT}", f"{e}\nLook at its tab for the error message.")
    print(f"\n   Dashboard:  http://localhost:{API_PORT}" + (f"   or from another device  http://{ip}:{API_PORT}" if ip else ""))


def stop():
    heading("Stopping")
    killed = 0
    for _, script, _ in CHILDREN:
        for pid, cmdline in find_procs(script):
            if IS_WIN:
                run(["taskkill", "/PID", str(pid), "/T", "/F"])
            else:
                os.kill(pid, signal.SIGTERM)
            say("ok", f"stopped {script} (PID {pid})")
            killed += 1
    if not killed:
        say("info", "subscriber.py and api.py were not running")
    print("       (Mosquitto and PostgreSQL keep running: stop them with 'net stop <name>' if you want.)")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Start and check the ESP32 -> MQTT -> database -> dashboard project")
    ap.add_argument("--check", action="store_true", help="only look, never start or change anything")
    ap.add_argument("--stop", action="store_true", help="stop subscriber.py and api.py")
    ap.add_argument("-y", "--yes", action="store_true", help="answer yes to the questions")
    ap.add_argument("--every", type=int, metavar="SECONDS", help="database save interval for subscriber.py (0 = every reading)")
    ap.add_argument("--child", choices=[n for n, _, _ in CHILDREN], help=argparse.SUPPRESS)
    ap.add_argument("--handoff", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.child:
        return run_child(args.child, args.handoff)
    if args.stop:
        return stop()

    if args.every is not None:
        if args.every < 0:
            return print("--every must be 0 or more seconds") or 1
        os.environ["IOT_SAVE_EVERY_S"] = str(args.every)   # the subscriber tab inherits it
    print("ESP32 -> MQTT -> database -> dashboard" + ("   (check only: nothing will be started)" if args.check else ""))
    if not check_packages(args.yes, args.check):
        return 1
    check_config_ip(args.yes, args.check)
    check_services(args.check)
    check_ports()
    check_mosquitto_conf()
    check_firewall()
    password = connect_db(args.yes)
    if password is None and not problems["fail"]:
        problems["fail"] += 1
    check_broker()

    if problems["fail"]:
        print(f"\n{problems['fail']} problem(s) marked FAIL: fix them first; nothing was started.")
        return 1
    launch(CHILDREN, password, args.check)
    if not args.check:
        main_ip, _ = pc_ipv4s()
        time.sleep(3)   # let the two programs come up before looking
        summary(main_ip)
    print(f"\nDone: {problems['warn']} warning(s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
