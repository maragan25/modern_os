# ESP32 Temperature & Humidity – MQTT + Database + Dashboard

```
                                  ┌─ (WebSockets, port 9001) ──► dashboard/index.html  (live values)
ESP32 + DHT11 ──MQTT (port 1883)──► Mosquitto ─┤
                                  └─ subscriber.py ──► PostgreSQL (iot.readings)  (history)
```

Topic format: `sensors/<device_id>/data`, payload
`{"temperature": 23.4, "humidity": 45.0, "heat_index": 23.1}`.
Each board sends a reading every 5 seconds (that is what the live dashboard shows), but only **one reading per
device every 15 minutes** is stored in the database. After each stored reading `subscriber.py` publishes
`sensors/<device_id>/saved`, and the board blinks its LED once. Readings older than **7 days** are deleted
once a day, so the database stays small.

## Setting up on a new computer

Install: **Mosquitto**, **PostgreSQL**, **Python 3** and the **Arduino IDE** (with the ESP32 board package).
Then follow these steps in order. Commands are for Windows (cmd); macOS notes are at the end.

### 1. Mosquitto configuration

By default Mosquitto only accepts connections from the same computer, and has no WebSocket port.
Open `C:\Program Files\Mosquitto\mosquitto.conf` in Notepad **as administrator -- importnant** and add these
lines at the end:

```
listener 1883
allow_anonymous true

listener 9001
protocol websockets
```

- `listener 1883` — plain MQTT, used by the ESP32 and `subscriber.py`.
- `allow_anonymous true` — no username/password (fine on a home network; do not use on a public one).
- `listener 9001` + `protocol websockets` — the dashboard runs in a browser, and browsers can only
  speak MQTT over WebSockets. `protocol` must be on its own line, directly under its `listener`.

### 2. Firewall rule (allow the ESP32 to reach the broker)

**Why:** the ESP32 is a different device that connects *into* this computer. Windows blocks incoming
connections to ports it has not been told to allow, so without this rule the ESP32 prints
`failed (state -2)` and never connects. Port 1883 is needed for the ESP32. Ports 9001 (live data) and
8000 (the dashboard and its history, from `api.py`) are only needed if the dashboard is opened from
*another* device; the same computer reaches them through `localhost`, which the firewall does not block. The rule is limited to **Private** networks so the ports stay
closed on public Wi-Fi.

1. Make sure the Wi-Fi network is set to **Private**: *Settings → Network & internet → Wi-Fi →
   your network → Network profile type → Private*.
2. Add the rule, either:
   - **PowerShell as administrator, one command:**
     ```
     New-NetFirewallRule -DisplayName "Mosquitto" -Direction Inbound -Protocol TCP -LocalPort 1883,9001,8000 -Action Allow -Profile Private
     ```
   - **or in the GUI:** *Windows Defender Firewall with Advanced Security → Inbound Rules → New Rule…
     → Port → TCP → Specific local ports: `1883, 9001, 8000` → Allow the connection →* tick **only Private**
     *→ name it "Mosquitto"*.

### 3. Start the broker

In an administrator cmd window, either:

- run it as a service (no window needed, starts with Windows): `net start mosquitto`
- or run it in a window to watch every connection:
  `net stop mosquitto`, then `cd "C:\Program Files\Mosquitto"` and `mosquitto -c mosquitto.conf -v`
  (if it says "address already in use", the service is still running)

Starting correctly prints `Opening ipv4 listen socket on port 1883` and `... port 9001`.
Watch the messages: `mosquitto_sub -h localhost -t "sensors/#" -v`

### 4. Create the database

Start PostgreSQL (administrator cmd): `net start postgresql-x64-18` (use your version number).
Then create the database and the table, once per computer (each asks for the `postgres` password):

```
cd "C:\Program Files\PostgreSQL\18\bin"
psql -U postgres -c "CREATE DATABASE iot;"
psql -U postgres -d iot -f "<this project folder>\schema.sql"
```

Check the stored readings:
`psql -U postgres -d iot -c "SELECT * FROM readings ORDER BY recorded_at DESC LIMIT 10;"`

The data lives in PostgreSQL's own data folder, not in this project folder, so a new computer
starts with an empty database.

<details>
<summary>If <code>psql</code> says "Connection refused" and the service does not exist</summary>

The installer sometimes fails to create the database folder (seen on Turkish-language Windows).
Create it by hand in an administrator cmd, from `C:\Program Files\PostgreSQL\18\bin`:

```
mkdir C:\pgdata
initdb -D C:\pgdata -U postgres -W -E UTF8 --locale=C --auth=scram-sha-256
icacls C:\pgdata /grant "NT AUTHORITY\NetworkService":(OI)(CI)F
pg_ctl register -N postgresql-x64-18 -D C:\pgdata -S auto
net start postgresql-x64-18
```
</details>

### 5. Python libraries and the subscriber

The subscriber copies one MQTT reading per device every 15 minutes into the database. In cmd, inside this project folder:

```
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PGPASSWORD=<postgres password>
python subscriber.py
```

`requirements.txt` lists `paho-mqtt` and `psycopg[binary]`. Expect `Connected to PostgreSQL`,
`Connected to MQTT broker`, and then a `-> saved` line for each device: the first reading after the
subscriber starts is saved at once, then one every 15 minutes. To test quickly, set a shorter interval
before starting it: `set IOT_SAVE_EVERY_S=30`.
When it starts, and then once a day, it also deletes readings older than 7 days and prints
`clean-up: deleted N reading(s) older than 7 days`. To keep them longer, e.g. 30 days: `set IOT_KEEP_DAYS=30`.
The password is never stored in a file. Do not copy or commit `.venv`: it contains paths to the
computer that created it, so create a new one on each computer.

By default the subscriber connects to `host=localhost port=5432 dbname=iot user=postgres`.
Override it with the `IOT_DB_DSN` environment variable (for example when PostgreSQL uses port 5433).

**The history API.** A browser cannot talk to PostgreSQL, so `api.py` sits in between: it reads the
`readings` table and answers the dashboard over HTTP. It also serves the dashboard itself. Start it in a
**second** cmd window (same folder, same steps: activate `.venv`, `set PGPASSWORD=...`):

```
python api.py
```

It only reads, so readings keep being saved by `subscriber.py` even if `api.py` is stopped. Try it:
`http://localhost:8000/api/devices` and `http://localhost:8000/api/history?device=<id>&range=24h`
(`range` is `1h`, `24h` or `7d`: averages per 1 minute, 5 minutes or 1 hour, with min and max).

### 6. The ESP32

Sketch: `esp32_mqtt/esp32_mqtt.ino`. In the Arduino IDE install the libraries **PubSubClient**
(Nick O'Leary), **DHT sensor library** (Adafruit) and **Adafruit Unified Sensor**.
Board: *ESP32 Dev Module*.

**Create `config.h` first.** The Wi-Fi name, Wi-Fi password and broker IP are not in the sketch: they are
read from `esp32_mqtt/config.h`, which is not in the repository (it is listed in `.gitignore`). Without it
the sketch stops with `Missing config.h`. Copy the template in the same folder:

```
cd esp32_mqtt
copy config.example.h config.h
```

(macOS: `cp config.example.h config.h`). Open `config.h` and fill in:

```cpp
const char* WIFI_SSID     = "YOUR_WIFI_NAME";        // 2.4 GHz network only
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST     = "192.168.x.x";           // IPv4 of the computer running Mosquitto (run ipconfig)
```

- `WIFI_SSID` / `WIFI_PASSWORD` — a **2.4 GHz** network
- `MQTT_HOST` — the IPv4 address of the computer running Mosquitto (`ipconfig`), on that same network

Reopen the sketch in the Arduino IDE: `config.h` should appear as a second tab next to `esp32_mqtt.ino`.
At the top of the sketch itself set `DHTPIN` (4) and `DHTTYPE` (DHT11). In the Arduino IDE pick the board you
actually have (e.g. *ESP32S3 Dev Module* for an ESP32-S3), otherwise the status LED below stays dark.

**Device ID:** every board makes its own id from its hardware address, e.g. `esp32-a1b2c3`, so the same
sketch can be uploaded to every ESP32. The serial monitor prints it at start-up (`Device ID: ...`). To choose
it yourself, add `#define DEVICE_ID "esp32-room1"` to `config.h` (every board needs a different one).
The board that already saved readings as `A7-27` keeps that name with `#define DEVICE_ID "A7-27"`, so its
history stays together.

**Status LED:** the board's own LED. (The red light that is always on is the power LED; the sketch cannot
change it.) Connection and database saves look the same on every board:

| LED (plain blue LED / color dot) | Meaning |
|---|---|
| slow blink / blue blink | connecting to Wi-Fi and the broker |
| solid on / solid red | an attempt failed; it keeps retrying (the Serial Monitor says why) |
| fast flashing for 3 s / green flashing | Wi-Fi and MQTT connected |
| one short blink / one white blink | `subscriber.py` saved a reading in the database (every 15 minutes) |

After the 3 seconds the two kinds of board differ. The classic ESP32 DevKit only has a blue LED, so it goes
**off**. Boards with a color dot (ESP32-S3 / C3 / C6 DevKits) show how the room is, with the same ranges as the
dashboard: green = comfortable, yellow = a little outside, red = far outside or the sensor failed. If the dot on
an ESP32-S3 DevKitC-1 v1.1 stays dark, add `#define STATUS_RGB_PIN 38` to `config.h`. The save blink only
appears while `subscriber.py` is running.

Then **Upload** (hold the **IO0** button while it says "Connecting…"). The values are stored inside the
board's program, so after changing them the sketch must be uploaded again. The EN button only restarts the
board. Never commit `config.h` or share it: it contains the real Wi-Fi password.

Wiring (DHT11 module): `S` → GPIO 4, middle pin → 3V3, other end → GND. Never use GPIO 0 for the data pin.

### 7. The dashboard

With `api.py` running, open **http://localhost:8000** on the computer running Mosquitto, or
**http://&lt;computer-ip&gt;:8000** from a phone or laptop on the same network. The bar at the top should say
**connected** and "History: from the database". (Opening `dashboard/index.html` as a file also works on
that computer; then the history comes from `http://localhost:8000`.)
At the top is a rotatable 3D floor plan (3 floors, 16 rooms, each with a unique id such as `G01`).
The dashboard subscribes to `sensors/+/data` and shows each reading in the room whose `device` matches
the ESP32's device id. For now, a board whose id is not in the `ROOMS` list in `dashboard/index.html` is
put in a random free room; the browser remembers it, and the bar at the top shows where each board went.
To give a board a fixed room, set that room's `device` to the board's id.
Every device in the database gets a room, also boards that are unplugged now (shown as Offline with
their past readings). The charts and the Today card come from the database (one stored point per 15 minutes),
and live readings, one every 5 seconds, are added on top as they arrive.
`index.mockup.html` is the original mockup with simulated data.

## Checking that everything is running

| Check | Command | Good result |
|---|---|---|
| PostgreSQL | `sc query postgresql-x64-18` | `STATE : 4 RUNNING` |
| Broker | `netstat -ano \| findstr ":1883 :9001"` | two `LISTENING` lines |
| Messages arriving | `mosquitto_sub -h localhost -t "sensors/#" -v` | a reading every 5 s |
| Rows being saved | the `SELECT` in step 4 | a new row per device every 15 minutes (the board blinks) |
| History API | open `http://localhost:8000/api/devices` | a list with your device ids |

Test without hardware:
`mosquitto_pub -h localhost -t sensors/test/data -m "{\"temperature\": 24.5, \"humidity\": 50}"`

## macOS notes

- Mosquitto config: `/opt/homebrew/etc/mosquitto/mosquitto.conf` (same lines as step 1);
  start/stop with `brew services start mosquitto` / `brew services stop mosquitto`.
- Python: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then run
  `IOT_DB_DSN="dbname=iot" .venv/bin/python subscriber.py`.
