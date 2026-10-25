"""MQTT -> PostgreSQL bridge.

Subscribes to sensors/<device_id>/data and stores every reading in the
`readings` table of the `iot` database.

Expected payload (JSON):
    {"temperature": 23.4, "humidity": 45.1, "heat_index": 23.1}
"""

import json
import os
from datetime import datetime

import paho.mqtt.client as mqtt
import psycopg

MQTT_HOST = "localhost"
MQTT_PORT = 1883
MQTT_TOPIC = "sensors/+/data"

# Windows PostgreSQL listens on localhost and logs in as the "postgres" user.
# The password is NOT stored here: set it in the terminal first, e.g.
#   cmd:         set PGPASSWORD=your-password
#   PowerShell:  $env:PGPASSWORD="your-password"
# (On Erin's Mac the old value "dbname=iot" works: set IOT_DB_DSN=dbname=iot)
DB_DSN = os.environ.get("IOT_DB_DSN", "host=localhost port=5432 dbname=iot user=postgres")

try:
    db = psycopg.connect(DB_DSN, autocommit=True)
except psycopg.OperationalError as e:
    raise SystemExit(f"Could not connect to PostgreSQL ({DB_DSN}):\n{e}\n"
                     "Is the PostgreSQL service running, and is PGPASSWORD set in this terminal?")
print(f"Connected to PostgreSQL ({DB_DSN})")


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code.is_failure:
        print(f"Could not connect to MQTT broker: {reason_code}")
        return
    print(f"Connected to MQTT broker at {MQTT_HOST}:{MQTT_PORT}")
    client.subscribe(MQTT_TOPIC)
    print(f"Listening on '{MQTT_TOPIC}' ... (Ctrl+C to stop)\n")


def on_message(client, userdata, msg):
    device_id = msg.topic.split("/")[1]
    try:
        data = json.loads(msg.payload)
        temperature = float(data["temperature"])
        humidity = float(data["humidity"])
        heat_index = data.get("heat_index")
    except (ValueError, KeyError, TypeError) as e:
        print(f"Skipped bad message on {msg.topic}: {msg.payload!r} ({e})")
        return

    db.execute(
        "INSERT INTO readings (device_id, temperature, humidity, heat_index) "
        "VALUES (%s, %s, %s, %s)",
        (device_id, temperature, humidity, heat_index),
    )
    now = datetime.now().strftime("%H:%M:%S")
    print(f"[{now}] {device_id}: {temperature:.1f} °C, {humidity:.1f} % -> saved")


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="db-subscriber")
client.on_connect = on_connect
client.on_message = on_message
client.connect(MQTT_HOST, MQTT_PORT)

try:
    client.loop_forever()
except KeyboardInterrupt:
    print("\nStopped.")
finally:
    db.close()
