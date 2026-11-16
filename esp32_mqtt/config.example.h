    #pragma once
// Template for config.h. Copy this file to config.h (same folder) and fill in your values.
// config.h is listed in .gitignore, so your Wi-Fi password and IP address are never committed.

const char* WIFI_SSID     = "YOUR_WIFI_NAME";        // 2.4 GHz network only
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* MQTT_HOST     = "192.168.x.x";           // IPv4 of the computer running Mosquitto (run ipconfig)

// Optional. Each board makes its own device id from its hardware address (shown on the serial monitor
// at start-up, e.g. esp32-a1b2c3). Uncomment to choose it yourself; every board needs a different one.
// #define DEVICE_ID "esp32-room1"

// Optional. Only if the color dot stays dark on an ESP32-S3 DevKitC-1 v1.1 (its LED is on GPIO 38).
// #define STATUS_RGB_PIN 38
