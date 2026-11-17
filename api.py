"""Read-only HTTP API for the dashboard. Uses only Python's standard library plus psycopg
(already needed by subscriber.py), so it adds no new dependency.

The dashboard runs in a browser, and a browser cannot talk to PostgreSQL directly.
This small program sits in between: the browser asks it a question over HTTP, it asks
the database, and it answers in JSON.

It only READS the `readings` table. Saving readings is still subscriber.py's job, so
the data keeps being saved even if this program is stopped.

It also serves the dashboard itself, so the page and its data come from one address.

Run (same PGPASSWORD as for subscriber.py):
    python api.py
Open the dashboard:
    http://localhost:8000              (on this computer)
    http://<this-computer's-ip>:8000   (from a phone or laptop on the same network)
Try the API:
    http://localhost:8000/api/devices
    http://localhost:8000/api/history?device=esp32-room1&range=24h
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import psycopg

# Same database settings as subscriber.py. The password is not here: psycopg reads
# the PGPASSWORD environment variable by itself.
DB_DSN = os.environ.get("IOT_DB_DSN", "host=localhost port=5432 dbname=iot user=postgres")
# 0.0.0.0 = other devices on the network can open the dashboard too (Windows still asks the
# firewall: port 8000 must be in the "Mosquitto" rule, see README step 2). Set IOT_API_HOST=127.0.0.1
# to allow only this computer.
HOST = os.environ.get("IOT_API_HOST", "0.0.0.0")
PORT = int(os.environ.get("IOT_API_PORT", "8000"))
DASHBOARD_DIR = Path(__file__).resolve().parent / "dashboard"
FILE_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css"}

# range -> (how far back to look, size of one bucket). Each gives a few hundred points:
# 60, 288 and 168, which is a good size to draw as a line.
RANGES = {
    "1h":  ("1 hour",   "1 minute"),
    "24h": ("24 hours", "5 minutes"),
    "7d":  ("7 days",   "1 hour"),
}


class BadRequest(Exception):
    """The request itself is wrong (missing or unknown parameter): answered with HTTP 400."""


def get_devices(params):
    """Every device id that ever saved a reading, with the time of its last reading."""
    with psycopg.connect(DB_DSN) as conn:
        rows = conn.execute(
            """
            SELECT device_id, max(recorded_at) AS last_seen
            FROM readings
            GROUP BY device_id
            ORDER BY device_id
            """
        ).fetchall()
    # isoformat() turns the timestamp into text such as 2026-11-04T12:30:00+00:00,
    # which JSON can carry and the browser's Date() can read back.
    return [{"device": device_id, "last_seen": last_seen.isoformat()} for device_id, last_seen in rows]


def get_history(params):
    """Averaged readings of one device: one point per time bucket, with min and max.

    A bucket with no readings (the ESP32 was off) is simply left out, so a gap in the
    data shows up as a gap between two points.
    """
    device = params.get("device", [""])[0]
    rng = params.get("range", [""])[0]
    if not device:
        raise BadRequest("missing parameter: device")
    if rng not in RANGES:
        raise BadRequest(f"range must be one of: {', '.join(RANGES)}")
    look_back, bucket = RANGES[rng]

    # look_back and bucket come from the RANGES table above, never from the request,
    # and device is passed as a parameter (%s), so nothing the browser sends is ever
    # pasted into the SQL text.
    with psycopg.connect(DB_DSN) as conn:
        rows = conn.execute(
            """
            SELECT date_bin(%(bucket)s::interval, recorded_at, TIMESTAMPTZ '2000-01-01') AS t,
                   count(*),
                   avg(temperature), min(temperature), max(temperature),
                   avg(humidity),    min(humidity),    max(humidity)
            FROM readings
            WHERE device_id = %(device)s
              AND recorded_at > now() - %(look_back)s::interval
            GROUP BY t
            ORDER BY t
            """,
            {"bucket": bucket, "look_back": look_back, "device": device},
        ).fetchall()

    def stats(avg, lo, hi):
        # REAL columns come back as 24.299999..., so round to the one decimal the sensor has
        return {"avg": round(avg, 1), "min": round(lo, 1), "max": round(hi, 1)}

    return {
        "device": device,
        "range": rng,
        "bucket": bucket,
        "points": [
            {"t": t.isoformat(), "n": n,
             "temperature": stats(*temp), "humidity": stats(*hum)}
            for t, n, *rest in rows
            for temp, hum in [(rest[0:3], rest[3:6])]
        ],
    }


# URL path -> function that produces the answer. New endpoints are added here.
ROUTES = {
    "/api/devices": get_devices,
    "/api/history": get_history,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        route = ROUTES.get(url.path)
        if route is None:
            return self.send_file(url.path)
        try:
            self.send_json(200, route(parse_qs(url.query)))
        except BadRequest as e:
            self.send_json(400, {"error": str(e)})
        except psycopg.OperationalError as e:
            # Database down or wrong password: say so instead of crashing.
            self.send_json(503, {"error": f"database unavailable: {str(e).strip()}"})
        except psycopg.Error as e:
            # Any other database problem (e.g. the readings table does not exist yet)
            self.send_json(500, {"error": f"database error: {str(e).strip()}"})

    def send_file(self, path):
        """The dashboard's own files. Only files directly inside dashboard/ are served,
        so a request like /../api.py cannot read anything else on this computer."""
        if path == "/favicon.ico":           # browsers ask for a tab icon; there is none
            self.send_response(204)
            self.end_headers()
            return
        name = "index.html" if path in ("", "/") else path.lstrip("/")
        file = (DASHBOARD_DIR / name).resolve()
        if file.parent != DASHBOARD_DIR or not file.is_file():
            return self.send_json(404, {"error": "not found"})
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", FILE_TYPES.get(file.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # Opened through this server, the dashboard reads the API from its own address. If it
        # is opened as a file instead (file:///.../index.html), browsers refuse to let it read
        # answers from another address unless the answer says it is allowed. "*" = any page
        # may read it; fine because the API is read-only.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    try:
        server = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as e:
        raise SystemExit(f"Could not listen on port {PORT}: {e}\nIs api.py already running in another window?")
    print(f"Dashboard: http://localhost:{PORT}   API: http://localhost:{PORT}/api/devices  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
