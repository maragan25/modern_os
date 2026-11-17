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

/* ---------- Status LED ----------
   The board's own LED. The red light that is always on is the power LED: it is wired to the power
   supply, so no code can change it.

   Every board shows the connection and the database saves the same way:
     connecting to Wi-Fi / MQTT ...... blinking (blue on a color dot, the blue LED on a plain board)
     connection failed ............... solid on (red on a color dot); the sketch keeps retrying
     connected (Wi-Fi and MQTT) ...... fast flashing for 3 seconds (green on a color dot)
     reading saved in the database ... one short blink (white on a color dot), from the "saved"
                                       message that subscriber.py sends after every saved row

   After the 3 seconds the two kinds of board differ:
     plain board (classic ESP32 DevKit, blue LED on GPIO 2) ... LED off
     color dot (ESP32-S3, C3, C6 DevKits) ... shows how the room is, with the same rules as the dashboard:
                                              green = comfortable, yellow = a little outside the range,
                                              red = far outside (or the sensor fails)

   If the dot stays dark on an ESP32-S3 DevKitC-1 v1.1, add  #define STATUS_RGB_PIN 38  to config.h. */
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
const uint8_t LED_LEVEL = 40;                  // color brightness, 0-255: the dot is blinding at full power
const unsigned long BLINK_MS = 250;            // connecting: 250 ms on, 250 ms off
const unsigned long FLASH_MS = 100;            // connected: 100 ms on, 100 ms off ...
const unsigned long CONNECTED_FLASH_MS = 3000; // ... for 3 seconds
const unsigned long SAVED_PULSE_MS = 300;      // one blink per saved reading

// Comfort ranges, the same as CFG.comfort in dashboard/index.html
const float T_MIN = 20, T_MAX = 26, H_MIN = 30, H_MAX = 60;

// How the room is (only shown on color dot boards). ST_CONNECTING = no reading yet.
enum Status { ST_CONNECTING, ST_OK, ST_MEH, ST_WARNING };
Status status = ST_CONNECTING;

// How the connection is. One "round" of connecting starts as CONNECTING; an attempt that fails
// makes it FAILED (solid light) until a retry succeeds and it becomes UP.
enum Link { LINK_CONNECTING, LINK_FAILED, LINK_UP };
Link linkState = LINK_CONNECTING;
unsigned long linkSince = 0;     // when the link became UP (for the 3 second flash)
unsigned long pulseUntil = 0;    // the "saved" blink is on until this time

const unsigned long READ_INTERVAL_MS = 5000;

DHT dht(DHTPIN, DHTTYPE);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

char topic[64];        // sensors/<id>/data   we publish readings here
char topicSaved[64];   // sensors/<id>/saved  subscriber.py answers here after it has written a row to the database
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

void setLink(Link l) { linkState = l; if (l == LINK_UP) linkSince = millis(); }
void ledPulse() { pulseUntil = millis() + SAVED_PULSE_MS; }

// Call often (every loop and while waiting); it only writes to the LED when something changes.
void updateLed() {
  unsigned long t = millis();
  bool flashing = (linkState == LINK_UP) && (t - linkSince < CONNECTED_FLASH_MS);
  bool flashOn = ((t - linkSince) / FLASH_MS) % 2 == 0;
  bool blinkOn = (t / BLINK_MS) % 2 == 0;
  bool pulse = (long)(pulseUntil - t) > 0;
#if LED_IS_RGB
  uint8_t r = 0, g = 0, b = 0;
  if (linkState == LINK_CONNECTING)      { if (blinkOn) b = LED_LEVEL; }
  else if (linkState == LINK_FAILED)     { r = LED_LEVEL; }
  else if (flashing)                { if (flashOn) g = LED_LEVEL; }
  else if (pulse)                   { r = g = b = LED_LEVEL; }          // saved: white
  else switch (status) {
    case ST_OK:      g = LED_LEVEL; break;
    case ST_MEH:     r = LED_LEVEL; g = LED_LEVEL * 6 / 10; break;        // yellow
    case ST_WARNING: r = LED_LEVEL; break;
    default: break;                                                      // no reading yet: dark
  }
  static uint32_t shown = 0xFFFFFFFF;
  uint32_t c = ((uint32_t)r << 16) | ((uint32_t)g << 8) | b;
  if (c != shown) { rgbLedWrite(LED_PIN, r, g, b); shown = c; }
#else
  bool on;
  if (linkState == LINK_CONNECTING)  on = blinkOn;
  else if (linkState == LINK_FAILED) on = true;
  else if (flashing)            on = flashOn;
  else                          on = pulse;
  static int shown = -1;
  if (on != shown) { digitalWrite(LED_PIN, on ? HIGH : LOW); shown = on; }
#endif
}

// Like delay(), but keeps the LED pattern running while it waits.
void waitWithLed(unsigned long ms) {
  unsigned long start = millis();
  while (millis() - start < ms) { updateLed(); delay(10); }
}

// A message arrived on a topic we subscribed to.
void onMessage(char* t, byte* payload, unsigned int length) {
  if (strcmp(t, topicSaved) == 0) ledPulse();
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
  int n = WiFi.scanNetworks(true);   // true = scan in the background, so the LED keeps its pattern meanwhile
  while (n == WIFI_SCAN_RUNNING) { waitWithLed(100); n = WiFi.scanComplete(); }
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
      setLink(LINK_FAILED);   // the attempt failed: solid light until a retry succeeds
      Serial.printf("  failed after %lu s: %s (status %d). Retrying ...\n\n", WIFI_TIMEOUT_MS / 1000, wifiStatusText(s), s);
    }
  }
  Serial.printf("WiFi connected, ESP32 IP: %s, signal %d dBm\n", WiFi.localIP().toString().c_str(), WiFi.RSSI());
}

void connectMQTT() {
  while (!mqtt.connected()) {
    Serial.printf("Connecting to MQTT broker %s:%d ... ", MQTT_HOST, MQTT_PORT);
    if (mqtt.connect(deviceId)) {
      Serial.println("connected");
      // A fresh connection starts without subscriptions, so this is repeated after every reconnect.
      mqtt.subscribe(topicSaved);
      Serial.printf("Listening for save confirmations on: %s\n", topicSaved);
    } else {
      setLink(LINK_FAILED);   // the attempt failed: solid light until a retry succeeds
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
  snprintf(topicSaved, sizeof(topicSaved), "sensors/%s/saved", deviceId);
  Serial.printf("Device ID: %s\n", deviceId);
  Serial.printf("Publishing to topic: %s\n\n", topic);

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(onMessage);
}

void loop() {
  updateLed();

  if (WiFi.status() != WL_CONNECTED || !mqtt.connected()) {
    // One round of connecting: blink while trying, solid if an attempt fails, and when both
    // Wi-Fi and MQTT are up, flash for 3 seconds.
    setLink(LINK_CONNECTING);
    if (WiFi.status() != WL_CONNECTED) connectWiFi();
    if (!mqtt.connected()) connectMQTT();
    setLink(LINK_UP);
  }
  mqtt.loop();

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
  const char* label[] = { "no reading yet", "fine", "meh", "WARNING" };
  Serial.printf("[#%lu] %.1f °C, %.1f %%, feels like %.1f °C, room: %s", readingCount, tempC, humidity, heatIndexC, label[status]);
  if (mqtt.publish(topic, payload)) {
    Serial.println("  -> sent");
  } else {
    Serial.println("  -> SEND FAILED");
  }
}
