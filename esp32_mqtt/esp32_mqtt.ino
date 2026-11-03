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
const char* DEVICE_ID     = "esp32-room1";

#define DHTPIN 4        // GPIO the data pin is connected to
#define DHTTYPE DHT11   // change to DHT22 if you have the white one
// -----------------------------------

const unsigned long READ_INTERVAL_MS = 5000;

DHT dht(DHTPIN, DHTTYPE);
WiFiClient wifiClient;
PubSubClient mqtt(wifiClient);

char topic[64];
unsigned long lastRead = 0;
unsigned long readingCount = 0;

const unsigned long WIFI_TIMEOUT_MS = 20000;  // give up on one attempt after 20 s, then scan and retry

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
  WiFi.mode(WIFI_STA);
  while (WiFi.status() != WL_CONNECTED) {
    scanNetworks();
    Serial.printf("Connecting to WiFi '%s' ", WIFI_SSID);
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    unsigned long start = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - start < WIFI_TIMEOUT_MS) {
      delay(500);
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
  while (!mqtt.connected()) {
    Serial.printf("Connecting to MQTT broker %s:%d ... ", MQTT_HOST, MQTT_PORT);
    if (mqtt.connect(DEVICE_ID)) {
      Serial.println("connected");
    } else {
      Serial.printf("failed (state %d), retrying in 5 s\n", mqtt.state());
      delay(5000);
    }
  }
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  dht.begin();

  Serial.println();
  Serial.println("========================================");
  Serial.println("   ESP32 Temperature & Humidity -> MQTT ");
  Serial.println("========================================");

  snprintf(topic, sizeof(topic), "sensors/%s/data", DEVICE_ID);
  Serial.printf("Publishing to topic: %s\n\n", topic);

  connectWiFi();
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) connectWiFi();
  if (!mqtt.connected()) connectMQTT();
  mqtt.loop();

  if (millis() - lastRead < READ_INTERVAL_MS) return;
  lastRead = millis();

  float humidity = dht.readHumidity();
  float tempC = dht.readTemperature();

  if (isnan(humidity) || isnan(tempC)) {
    Serial.println("ERROR: Failed to read from sensor. Check wiring and DHTTYPE.");
    return;
  }

  float heatIndexC = dht.computeHeatIndex(tempC, humidity, false);

  char payload[128];
  snprintf(payload, sizeof(payload),
           "{\"temperature\": %.1f, \"humidity\": %.1f, \"heat_index\": %.1f}",
           tempC, humidity, heatIndexC);

  readingCount++;
  Serial.printf("[#%lu] %.1f °C, %.1f %%, feels like %.1f °C", readingCount, tempC, humidity, heatIndexC);
  if (mqtt.publish(topic, payload)) {
    Serial.println("  -> sent");
  } else {
    Serial.println("  -> SEND FAILED");
  }
}
