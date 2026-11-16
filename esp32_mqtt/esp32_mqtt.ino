#include <WiFi.h>
#include <PubSubClient.h>
#include <DHT.h>

// Wi-Fi name, Wi-Fi password and the broker's IP live in config.h, which is not committed.
// If this fails to compile: copy config.example.h to config.h and fill it in.
#if __has_include("config.h")
#include "config.h"
#else
#error "Missing config.h: copy esp32_mqtt/config.example.h to esp32_mqtt/config.h and fill in your Wi-Fi name, password and the broker IP."
#endif

// ---------- Change these ----------
const int   MQTT_PORT     = 1883;

#define DHTPIN 4        // GPIO the data pin is connected to
#define DHTTYPE DHT11   // change to DHT22 if you have the white one
// -----------------------------------

// Every board makes its own id from its hardware (MAC) address, e.g. esp32-a1b2c3, so the same
// sketch can be uploaded to every ESP32 and the dashboard can tell them apart.
// To choose the id yourself, add this line to config.h:   #define DEVICE_ID "esp32-room1"
char deviceId[32];

// ---------- Status LED ----------
// The board's own LED shows how the room is, with the same rules as the dashboard:
// green = comfortable, yellow = a little outside the range, red = far outside (or the sensor fails),
// blinking blue = connecting to Wi-Fi / the broker.
// Boards with a color dot (ESP32-S3, C3, C6 DevKits) show these colors. The classic ESP32 DevKit only
// has a blue LED on GPIO 2, so there: steady = fine, slow blink = meh, fast blink = warning.
// If the dot stays dark on an ESP32-S3 DevKitC-1 v1.1, add  #define STATUS_RGB_PIN 38  to config.h.
#if defined(STATUS_RGB_PIN)
  #define LED_IS_RGB 1
  const uint8_t LED_PIN = STATUS_RGB_PIN;
#elif defined(RGB_BUILTIN)
  #define LED_IS_RGB 1
  const uint8_t LED_PIN = RGB_BUILTIN;
#else
  #define LED_IS_RGB 0
  #ifdef LED_BUILTIN
  const uint8_t LED_PIN = LED_BUILTIN;
  #else
  const uint8_t LED_PIN = 2;   // the blue LED on the classic ESP32 DevKit
  #endif
#endif
const uint8_t LED_LEVEL = 40;  // color brightness, 0-255: the dot is blinding at full power

// Comfort ranges, the same as CFG.comfort in dashboard/index.html
const float T_MIN = 20, T_MAX = 26, H_MIN = 30, H_MAX = 60;

enum Status { ST_CONNECTING, ST_OK, ST_MEH, ST_WARNING };
Status status = ST_CONNECTING;

const unsigned long READ_INTERVAL_MS = 5000;

DHT dht(DHTPIN, DHTTYPE);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

char topic[64];
unsigned long lastRead = 0;
unsigned long readingCount = 0;

const unsigned long WIFI_TIMEOUT_MS = 20000;  // give up on one attempt after 20 s, then scan and retry

void makeDeviceId() {
#ifdef DEVICE_ID
  snprintf(deviceId, sizeof(deviceId), "%s", DEVICE_ID);
#else
  uint8_t mac[6];
  WiFi.macAddress(mac);
  snprintf(deviceId, sizeof(deviceId), "esp32-%02x%02x%02x", mac[3], mac[4], mac[5]);
#endif
}

// Same rules as tempStatus() / humStatus() in the dashboard
Status roomStatus(float t, float h) {
  if (t > T_MAX + 2 || t < T_MIN - 2 || h > H_MAX + 10 || h < H_MIN - 5) return ST_WARNING;
  if (t > T_MAX || t < T_MIN || h > H_MAX || h < H_MIN) return ST_MEH;
  return ST_OK;
}

// Call often (every loop and while waiting); it only writes to the LED when something changes.
void updateLed() {
  unsigned long t = millis();
#if LED_IS_RGB
  uint8_t r = 0, g = 0, b = 0;
  switch (status) {
    case ST_OK:         g = LED_LEVEL; break;
    case ST_MEH:        r = LED_LEVEL; g = LED_LEVEL * 6 / 10; break;   // yellow
    case ST_WARNING:    r = LED_LEVEL; break;
    case ST_CONNECTING: if ((t / 500) % 2 == 0) b = LED_LEVEL; break;
  }
  static uint32_t shown = 0xFFFFFFFF;
  uint32_t c = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
  if (c != shown) { rgbLedWrite(LED_PIN, r, g, b); shown = c; }
#else
  bool on;
  switch (status) {
    case ST_OK:      on = true; break;                     // steady
    case ST_MEH:     on = (t / 1000) % 2 == 0; break;      // slow blink
    case ST_WARNING: on = (t / 150) % 2 == 0; break;       // fast blink
    default:         on = (t / 100) % 10 == 0; break;      // connecting: a short flash every second
  }
  static int shown = -1;
  if (on != shown) { digitalWrite(LED_PIN, on ? HIGH : LOW); shown = on; }
#endif
}

void waitWithLed(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) { updateLed(); delay(20); }
}

const char* wifiStatusText(int s) {
  switch (s) {
    case WL_IDLE_STATUS:     return "still trying";
    case WL_NO_SSID_AVAIL:   return "network not found (wrong name, 5 GHz only, hidden, or out of range)";
    case WL_CONNECT_FAILED:  return "connection refused (wrong password, WPA3-only, or the access point rejected it)";
    case WL_CONNECTION_LOST: return "connection lost";
    case WL_DISCONNECTED:    return "not connected (no answer from the access point)";
    default:                 return "unknown";
  }
}

// Lists every network the ESP32 can see. If yours is missing, it is a band/visibility problem.
void scanNetworks() {
  Serial.println("Scanning for WiFi networks ...");
  int n = WiFi.scanNetworks();
  if (n <= 0) {
    Serial.println("  no networks found at all (antenna/power problem, or nothing on 2.4 GHz nearby)");
    return;
  }
  bool found = false;
  for (int i = 0; i < n; i++) {
    bool match = (WiFi.SSID(i) == WIFI_SSID);
    if (match) found = true;
    Serial.printf("  %s %-32s ch %2d  %4d dBm\n", match ? "*" : " ", WiFi.SSID(i).c_str(), WiFi.channel(i), WiFi.RSSI(i));
  }
  if (found) {
    Serial.println("  (* = the network from config.h)");
  } else {
    Serial.printf("  '%s' is NOT in this list: the ESP32 cannot see it. Check 2.4 GHz, the name, and that it is not hidden.\n", WIFI_SSID);
  }
  WiFi.scanDelete();
}

void connectWiFi() {
  status = ST_CONNECTING;
  WiFi.mode(WIFI_STA);
  while (WiFi.status() != WL_CONNECTED) {
    scanNetworks();
    Serial.printf("Connecting to WiFi '%s' ", WIFI_SSID);
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
      waitWithLed(500);
      Serial.print(".");
    }
    Serial.println();
    if (WiFi.status() != WL_CONNECTED) {
      int s = WiFi.status();
      Serial.printf("  failed after %lu s: %s (status %d). Retrying ...\n\n", WIFI_TIMEOUT_MS / 1000, wifiStatusText(s), s);
    }
  }
  Serial.printf("WiFi connected, ESP32 IP: %s, signal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

void connectMQTT() {
  status = ST_CONNECTING;
  while (!mqtt.connected()) {
    Serial.printf("Connecting to MQTT broker %s:%d ... ", MQTT_HOST, MQTT_PORT);
    if (mqtt.connect(deviceId)) {
      Serial.println("connected");
    } else {
      Serial.printf("failed (state %d), retrying in 5 s\n", mqtt.state());
      waitWithLed(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  dht.begin();
#if !LED_IS_RGB
  pinMode(LED_PIN, OUTPUT);
#endif
  updateLed();
  WiFi.mode(WIFI_STA);
  makeDeviceId();

  Serial.println();
  Serial.println("========================================");
  Serial.println("   ESP32 Temperature & Humidity -> MQTT ");
  Serial.println("========================================");

  snprintf(topic, sizeof(topic), "sensors/%s/data", deviceId);
  Serial.printf("Device ID: %s\n", deviceId);
  Serial.printf("Publishing to topic: %s\n\n", topic);

  connectWiFi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();
  updateLed();

  if (millis() - lastRead < READ_INTERVAL_MS) return;
  lastRead = millis();

  float humidity = dht.readHumidity();
  float tempC = dht.readTemperature();

  if (isnan(humidity) || isnan(tempC)) {
    status = ST_WARNING;
    Serial.println("ERROR: Failed to read from sensor. Check wiring and DHTTYPE.");
    return;
  }
  status = roomStatus(tempC, humidity);

  float heatIndexC = dht.computeHeatIndex(tempC, humidity, false);

  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"temperature\": %.1f, \"humidity\": %.1f, \"heat_index\": %.1f}",
           tempC, humidity, heatIndexC);

  readingCount++;
  const char* label[] = { "connecting", "fine", "meh", "WARNING" };
  Serial.printf("[#%lu] %.1f °C, %.1f %%, feels like %.1f °C, LED: %s", readingCount, tempC, humidity, heatIndexC, label[status]);
  if (mqtt.publish(topic, payload)) {
    Serial.println("  -> sent");
  } else {
    Serial.println("  -> SEND FAILED");
  }
}
