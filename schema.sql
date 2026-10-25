-- Table for sensor readings. Run once, against the "iot" database:
--   psql -U postgres -d iot -f schema.sql
-- Safe to run again: nothing is created twice.

CREATE TABLE IF NOT EXISTS readings (
    id           BIGSERIAL    PRIMARY KEY,
    device_id    TEXT         NOT NULL,              -- from the MQTT topic: sensors/<device_id>/data
    temperature  REAL         NOT NULL,              -- °C
    humidity     REAL         NOT NULL,              -- % relative humidity
    heat_index   REAL,                               -- "feels like" °C, optional in the payload
    recorded_at  TIMESTAMPTZ  NOT NULL DEFAULT now() -- set by the database when the row is inserted
);

-- The dashboard will ask "readings for device X in the last N hours", so index exactly that.
CREATE INDEX IF NOT EXISTS readings_device_time
    ON readings (device_id, recorded_at DESC);
