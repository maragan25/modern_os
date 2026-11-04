"""Read-only HTTP API for the dashboard. Uses only Python's standard library plus psycopg
(already needed by subscriber.py), so it adds no new dependency.

The dashboard runs in a browser, and a browser cannot talk to PostgreSQL directly.
This small program sits in between: the browser asks it a question over HTTP, it asks
the database, and it answers in JSON.

It only READS the `readings` table. Saving readings is still subscriber.py's job, so
the data keeps being saved even if this program is stopped.

Run (same PGPASSWORD as for subscriber.py):
    python api.py
Try it:
    http://localhost:8000/api/devices
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import psycopg

# Same database settings as subscriber.py. The password is not here: psycopg reads
# the PGPASSWORD environment variable by itself.
DB_DSN = os.environ.get("IOT_DB_DSN", "host=localhost port=5432 dbname=iot user=postgres")
HOST, PORT = "127.0.0.1", 8000   # 127.0.0.1 = only this computer can reach the API


def get_devices():
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


# URL path -> function that produces the answer. New endpoints are added here.
ROUTES = {
    "/api/devices": get_devices,
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = ROUTES.get(urlparse(self.path).path)
        if route is None:
            return self.send_json(404, {"error": "not found"})
        try:
            self.send_json(200, route())
        except psycopg.OperationalError as e:
            # Database down or wrong password: say so instead of crashing.
            self.send_json(503, {"error": f"database unavailable: {str(e).strip()}"})

    def send_json(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        # The dashboard is opened as a file (file:///.../index.html). Browsers refuse to let
        # such a page read answers from another address (here localhost:8000) unless the
        # answer says it is allowed. "*" = any page may read it; fine because the API is
        # read-only and only reachable from this computer.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    print(f"API listening on http://{HOST}:{PORT}/api/devices  (Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
