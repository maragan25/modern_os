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

void connectWiFi() {
  Serial.printf("Connecting to WiFi '%s'", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nWiFi connected, ESP32 IP: %s\n", WiFi.localIP().toString().c_str());
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
