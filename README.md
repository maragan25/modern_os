# ESP32 Temperature & Humidity – MQTT + Database + Dashboard

```
                                  ┌─ (WebSockets, port 9001) ──► dashboard/index.html  (live values)
ESP32 + DHT11 ──MQTT (port 1883)──► Mosquitto ─┤
                                  └─ subscriber.py ──► PostgreSQL (iot.readings)  (history)
```

Topic format: `sensors/<device_id>/data`, payload
`{"temperature": 23.4, "humidity": 45.0, "heat_index": 23.1}`

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
`failed (state -2)` and never connects. Port 1883 is needed for the ESP32. Port 9001 is only needed if
the dashboard is opened from *another* device; the same computer reaches it through `localhost`,
which the firewall does not block. The rule is limited to **Private** networks so the ports stay
closed on public Wi-Fi.

1. Make sure the Wi-Fi network is set to **Private**: *Settings → Network & internet → Wi-Fi →
   your network → Network profile type → Private*.
2. Add the rule, either:
   - **PowerShell as administrator, one command:**
     ```
     New-NetFirewallRule -DisplayName "Mosquitto" -Direction Inbound -Protocol TCP -LocalPort 1883,9001 -Action Allow -Profile Private
     ```
   - **or in the GUI:** *Windows Defender Firewall with Advanced Security → Inbound Rules → New Rule…
     → Port → TCP → Specific local ports: `1883, 9001` → Allow the connection →* tick **only Private**
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

The subscriber copies every MQTT reading into the database. In cmd, inside this project folder:

```
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set PGPASSWORD=<postgres password>
python subscriber.py
```

`requirements.txt` lists `paho-mqtt` and `psycopg[binary]`. Expect `Connected to PostgreSQL`,
`Connected to MQTT broker`, and then a `-> saved` line for every reading.
The password is never stored in a file. Do not copy or commit `.venv`: it contains paths to the
computer that created it, so create a new one on each computer.

By default the subscriber connects to `host=localhost port=5432 dbname=iot user=postgres`.
Override it with the `IOT_DB_DSN` environment variable (for example when PostgreSQL uses port 5433).

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
At the top of the sketch itself set:

- `DEVICE_ID` — must be unique for every ESP32
- `DHTPIN` (4) and `DHTTYPE` (DHT11)

Then **Upload** (hold the **IO0** button while it says "Connecting…"). The values are stored inside the
board's program, so after changing them the sketch must be uploaded again. The EN button only restarts the
board. Never commit `config.h` or share it: it contains the real Wi-Fi password.

Wiring (DHT11 module): `S` → GPIO 4, middle pin → 3V3, other end → GND. Never use GPIO 0 for the data pin.

### 7. The dashboard

Open `dashboard/index.html` on the computer running Mosquitto. The banner at the top should say
**connected**. From another device, add the broker address: `index.html?broker=ws://<computer-ip>:9001`.
It shows one device (`esp32-room1`); use `?device=<DEVICE_ID>` for another. History is only kept while the page
is open; reading stored history from the database needs a small HTTP API that is not built yet.
`index.mockup.html` is the original mockup with simulated data.

## Checking that everything is running

| Check | Command | Good result |
|---|---|---|
| PostgreSQL | `sc query postgresql-x64-18` | `STATE : 4 RUNNING` |
| Broker | `netstat -ano \| findstr ":1883 :9001"` | two `LISTENING` lines |
| Messages arriving | `mosquitto_sub -h localhost -t "sensors/#" -v` | a reading every 5 s |
| Rows being saved | the `SELECT` in step 4 | the newest row is seconds old |

Test without hardware:
`mosquitto_pub -h localhost -t sensors/test/data -m "{\"temperature\": 24.5, \"humidity\": 50}"`

## macOS notes

- Mosquitto config: `/opt/homebrew/etc/mosquitto/mosquitto.conf` (same lines as step 1);
  start/stop with `brew services start mosquitto` / `brew services stop mosquitto`.
- Python: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, then run
  `IOT_DB_DSN="dbname=iot" .venv/bin/python subscriber.py`.
