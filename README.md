# ESP32 Temperature & Humidity – MQTT + Database

```
ESP32 + DHT ──MQTT──► Mosquitto (port 1883) ──► subscriber.py ──► PostgreSQL (iot.readings)
```

## MQTT server (Mosquitto)

- Config: `/opt/homebrew/etc/mosquitto/mosquitto.conf` (listens on all interfaces, port 1883)
- Start / stop: `brew services start mosquitto` / `brew services stop mosquitto`
- Watch all sensor messages: `mosquitto_sub -h localhost -t 'sensors/#' -v`
- Topic format: `sensors/<device_id>/data`, payload:
  `{"temperature": 23.4, "humidity": 45.0, "heat_index": 23.1}`

## Database server (PostgreSQL 14)

- Database `iot`, table `readings`
  (`id, device_id, temperature, humidity, heat_index, recorded_at`)
- Show latest readings: `psql -d iot -c "SELECT * FROM readings ORDER BY recorded_at DESC LIMIT 10;"`

## Subscriber (MQTT → database)

```
cd ~/esp32-iot
.venv/bin/python subscriber.py
```

Test without hardware:

```
mosquitto_pub -h localhost -t sensors/test/data -m '{"temperature": 24.5, "humidity": 50}'
```

## ESP32

Sketch: `esp32_mqtt/esp32_mqtt.ino`. Needs libraries **PubSubClient** (Nick O'Leary) and
**DHT sensor library** (Adafruit). Set WiFi name/password, `MQTT_HOST`, `DHTTYPE` and `DHTPIN`
at the top. The ESP32 must be on the same WiFi network as the computer running Mosquitto.
