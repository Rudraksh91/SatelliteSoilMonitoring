/*
 * ══════════════════════════════════════════════════════════════════════
 * TERRA-CORE AgriSat — ESP32 Cloud Firmware
 * Connects to HiveMQ public MQTT broker for worldwide remote access
 *
 * Libraries required (install via Arduino Library Manager):
 *   - PubSubClient  by Nick O'Leary   (v2.8+)
 *   - ArduinoJson   by Benoit Blanchon (v6.x)
 *   - WebSocketsServer (only needed if also using LOCAL ws:// mode)
 *
 * Board: "ESP32 Dev Module" in Arduino IDE
 *
 * MQTT Topics:
 *   Publish  →  agrisat/{FARM_ID}/data   (sensor readings every 2s)
 *   Subscribe → agrisat/{FARM_ID}/cmd    (valve commands from dashboard)
 *
 * HiveMQ public broker: broker.hivemq.com:1883 (no auth needed)
 *
 * Wiring: same as esp32_agrisat.ino — see WIRING_GUIDE.txt
 * ══════════════════════════════════════════════════════════════════════
 */

#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <HardwareSerial.h>
#include <WebServer.h>
#include <WebSocketsServer.h>

// ── WiFi credentials ──────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ── Farm Identity ─────────────────────────────────────────────────────
// MUST match the Farm ID you enter in the dashboard CLOUD tab
const char* FARM_ID = "dediapada-farm-01";

// ── MQTT Broker ───────────────────────────────────────────────────────
const char* MQTT_HOST     = "broker.hivemq.com";
const int   MQTT_PORT     = 1883;  // plain TCP (no TLS) for ESP32

// ── MQTT Topics (auto-built from FARM_ID) ────────────────────────────
char TOPIC_DATA[64];   // agrisat/{FARM_ID}/data
char TOPIC_CMD[64];    // agrisat/{FARM_ID}/cmd

// ── Hardware pins ─────────────────────────────────────────────────────
// Active-LOW relay: LOW = valve OPEN, HIGH = valve CLOSED
const int RELAY_ZONE[3] = {25, 26, 27};  // Z1, Z2, Z3
const int LED_PIN       = 2;             // Built-in LED (status)

// ── UART2 for Raspberry Pi Pico ───────────────────────────────────────
HardwareSerial PicoSerial(2);
const int PICO_RX = 16;
const int PICO_TX = 17;
const int PICO_BAUD = 9600;

// ── Local WebSocket (still available on same WiFi) ───────────────────
WebServer     http(80);
WebSocketsServer wsServer(81);

// ── WiFi + MQTT clients ───────────────────────────────────────────────
WiFiClient   wifiNet;
PubSubClient mqtt(wifiNet);

// ── Sensor data ───────────────────────────────────────────────────────
struct SensorData {
  float m[3]        = {0,0,0};  // moisture %
  float temperature = 0;
  float humidity    = 0;
  float soil_temp   = 0;
  int   light       = 0;
  bool  pico_online = false;
};
SensorData sensors;

// ── Valve states ─────────────────────────────────────────────────────
bool valveOpen[3]  = {false, false, false};
bool autoMode      = false;
unsigned long picoLastSeen = 0;

// ── Timing ────────────────────────────────────────────────────────────
unsigned long lastPublish   = 0;
unsigned long lastMqttRetry = 0;
unsigned long lastLedToggle = 0;
bool ledState = false;
const unsigned long PUBLISH_INTERVAL = 2000;   // send sensor data every 2s
const unsigned long MQTT_RETRY_MS    = 5000;   // retry MQTT connect every 5s

// ═════════════════════════════════════════════════════════════════════
// SETUP
// ═════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("\n[TERRA-CORE] ESP32 Cloud Firmware booting…"));

  // Build MQTT topic strings
  snprintf(TOPIC_DATA, sizeof(TOPIC_DATA), "agrisat/%s/data", FARM_ID);
  snprintf(TOPIC_CMD,  sizeof(TOPIC_CMD),  "agrisat/%s/cmd",  FARM_ID);
  Serial.print(F("[MQTT] Topics: "));
  Serial.print(TOPIC_DATA); Serial.print(F(" / ")); Serial.println(TOPIC_CMD);

  // Relay pins — start closed (HIGH = valve OFF)
  for (int i = 0; i < 3; i++) {
    pinMode(RELAY_ZONE[i], OUTPUT);
    digitalWrite(RELAY_ZONE[i], HIGH);
  }
  pinMode(LED_PIN, OUTPUT);

  // Pico UART
  PicoSerial.begin(PICO_BAUD, SERIAL_8N1, PICO_RX, PICO_TX);
  Serial.println(F("[UART] Pico serial initialised (UART2)"));

  // Connect WiFi
  connectWiFi();

  // MQTT setup
  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  connectMQTT();

  // Local HTTP API (same as local firmware — for fallback)
  setupHTTP();
  wsServer.begin();
  wsServer.onEvent(wsEvent);

  Serial.println(F("[TERRA-CORE] Boot complete ✓"));
}

// ═════════════════════════════════════════════════════════════════════
// LOOP
// ═════════════════════════════════════════════════════════════════════
void loop() {
  // Keep MQTT connected
  if (!mqtt.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > MQTT_RETRY_MS) {
      lastMqttRetry = now;
      connectMQTT();
    }
  }
  mqtt.loop();

  // Local WS / HTTP
  wsServer.loop();
  http.handleClient();

  // Read Pico UART
  readPicoUART();

  // Auto-irrigation check
  if (autoMode) checkAutoIrrig();

  // Publish sensor data
  unsigned long ms = millis();
  if (ms - lastPublish > PUBLISH_INTERVAL) {
    lastPublish = ms;
    publishSensorData();
  }

  // Status LED blink
  updateLED();
}

// ═════════════════════════════════════════════════════════════════════
// WiFi
// ═════════════════════════════════════════════════════════════════════
void connectWiFi() {
  Serial.print(F("[WiFi] Connecting to "));
  Serial.print(WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500); Serial.print('.'); tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.print(F("\n[WiFi] Connected · IP: "));
    Serial.println(WiFi.localIP());
  } else {
    Serial.println(F("\n[WiFi] Failed — starting SoftAP 'TERRA-CORE-Setup'"));
    WiFi.softAP("TERRA-CORE-Setup", "agrisat123");
  }
}

// ═════════════════════════════════════════════════════════════════════
// MQTT — connect
// ═════════════════════════════════════════════════════════════════════
void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  Serial.print(F("[MQTT] Connecting to broker.hivemq.com…"));

  // Unique client ID per device
  char clientId[32];
  snprintf(clientId, sizeof(clientId), "esp32-%s-%04X",
           FARM_ID, (uint16_t)(ESP.getEfuseMac() & 0xFFFF));

  if (mqtt.connect(clientId)) {
    Serial.println(F(" OK"));
    mqtt.subscribe(TOPIC_CMD);
    Serial.print(F("[MQTT] Subscribed to "));
    Serial.println(TOPIC_CMD);
    // Publish online announcement
    StaticJsonDocument<64> ann;
    ann["online"] = true;
    ann["farm"]   = FARM_ID;
    char buf[64];
    serializeJson(ann, buf);
    mqtt.publish(TOPIC_DATA, buf);
  } else {
    Serial.print(F(" FAILED rc="));
    Serial.println(mqtt.state());
  }
}

// ═════════════════════════════════════════════════════════════════════
// MQTT — incoming command callback
// ═════════════════════════════════════════════════════════════════════
void mqttCallback(char* topic, byte* payload, unsigned int len) {
  // Only handle commands from our cmd topic
  if (strcmp(topic, TOPIC_CMD) != 0) return;

  StaticJsonDocument<256> doc;
  DeserializationError err = deserializeJson(doc, payload, len);
  if (err) { Serial.println(F("[MQTT] JSON parse error")); return; }

  handleCommand(doc);
}

// ═════════════════════════════════════════════════════════════════════
// Command handler (shared between MQTT and local WebSocket)
// ═════════════════════════════════════════════════════════════════════
void handleCommand(JsonDocument& doc) {
  // Emergency stop
  if (doc["emergency_stop"].as<bool>()) {
    for (int i = 0; i < 3; i++) setValve(i, false);
    Serial.println(F("[CMD] EMERGENCY STOP — all valves closed"));
    return;
  }

  // Auto mode toggle
  if (!doc["auto_mode"].isNull()) {
    autoMode = doc["auto_mode"].as<bool>();
    Serial.print(F("[CMD] Auto mode: ")); Serial.println(autoMode ? "ON" : "OFF");
    return;
  }

  // Valve control: {zone:1, state:true/false}
  if (!doc["zone"].isNull()) {
    int  z     = doc["zone"].as<int>() - 1;   // 1-based → 0-based
    bool state = doc["state"].as<bool>();
    if (z >= 0 && z < 3) {
      setValve(z, state);
      Serial.print(F("[CMD] Valve Z")); Serial.print(z+1);
      Serial.println(state ? " OPEN" : " CLOSE");
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// Valve control
// ═════════════════════════════════════════════════════════════════════
void setValve(int idx, bool open) {
  if (idx < 0 || idx > 2) return;
  valveOpen[idx] = open;
  digitalWrite(RELAY_ZONE[idx], open ? LOW : HIGH);  // active-LOW relay
}

// ═════════════════════════════════════════════════════════════════════
// Publish sensor + valve state to MQTT
// ═════════════════════════════════════════════════════════════════════
void publishSensorData() {
  if (!mqtt.connected()) return;

  StaticJsonDocument<384> doc;
  JsonArray m = doc.createNestedArray("moisture");
  m.add(sensors.m[0]); m.add(sensors.m[1]); m.add(sensors.m[2]);
  doc["temperature"] = sensors.temperature;
  doc["humidity"]    = sensors.humidity;
  doc["soil_temp"]   = sensors.soil_temp;
  doc["light"]       = sensors.light;
  doc["pico_online"] = sensors.pico_online;

  JsonArray v = doc.createNestedArray("valve");
  v.add(valveOpen[0]); v.add(valveOpen[1]); v.add(valveOpen[2]);
  doc["auto_mode"] = autoMode;

  char buf[384];
  size_t len = serializeJson(doc, buf);
  mqtt.publish(TOPIC_DATA, (const uint8_t*)buf, len, false);

  // Also broadcast on local WS for any device on same network
  wsServer.broadcastTXT(buf, len);
}

// ═════════════════════════════════════════════════════════════════════
// Read Raspberry Pi Pico UART
// Format: M1:45.2,M2:38.7,M3:52.1,T:28.4,H:65.3,ST:24.1,L:1200\n
// ═════════════════════════════════════════════════════════════════════
void readPicoUART() {
  static String buf = "";
  while (PicoSerial.available()) {
    char c = PicoSerial.read();
    if (c == '\n') {
      parsePicoLine(buf);
      buf = "";
    } else if (buf.length() < 128) {
      buf += c;
    }
  }
  // Pico offline if silent for 10s
  if (sensors.pico_online && millis() - picoLastSeen > 10000) {
    sensors.pico_online = false;
    for (int i = 0; i < 3; i++) sensors.m[i] = 0;
    sensors.temperature = 0; sensors.humidity = 0;
    Serial.println(F("[UART] Pico OFFLINE — timeout 10s"));
  }
}

void parsePicoLine(const String& s) {
  auto extract = [&](const char* key) -> float {
    int i = s.indexOf(key);
    if (i < 0) return 0;
    i += strlen(key);
    int e = s.indexOf(',', i);
    return (e < 0 ? s.substring(i) : s.substring(i, e)).toFloat();
  };
  sensors.m[0]        = extract("M1:");
  sensors.m[1]        = extract("M2:");
  sensors.m[2]        = extract("M3:");
  sensors.temperature = extract("T:");
  sensors.humidity    = extract("H:");
  sensors.soil_temp   = extract("ST:");
  sensors.light       = (int)extract("L:");
  sensors.pico_online = true;
  picoLastSeen        = millis();
}

// ═════════════════════════════════════════════════════════════════════
// Auto-irrigation (threshold-based)
// ═════════════════════════════════════════════════════════════════════
void checkAutoIrrig() {
  if (!sensors.pico_online) return;
  for (int i = 0; i < 3; i++) {
    if (!valveOpen[i] && sensors.m[i] > 0 && sensors.m[i] < 30.0f) {
      setValve(i, true);
      Serial.print(F("[AUTO] Zone ")); Serial.print(i+1);
      Serial.print(F(" OPEN — moisture ")); Serial.print(sensors.m[i]); Serial.println(F("%"));
    } else if (valveOpen[i] && sensors.m[i] > 65.0f) {
      setValve(i, false);
      Serial.print(F("[AUTO] Zone ")); Serial.print(i+1);
      Serial.print(F(" CLOSE — moisture ")); Serial.print(sensors.m[i]); Serial.println(F("%"));
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// Local HTTP API (fallback when on same WiFi)
// ═════════════════════════════════════════════════════════════════════
void addCORS() {
  http.sendHeader("Access-Control-Allow-Origin",  "*");
  http.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  http.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}
void setupHTTP() {
  http.on("/", HTTP_GET, []() {
    addCORS();
    char buf[64]; snprintf(buf, sizeof(buf), "{\"farm\":\"%s\",\"mode\":\"cloud\"}", FARM_ID);
    http.send(200, "application/json", buf);
  });
  http.on("/api/sensors", HTTP_GET, []() {
    addCORS();
    StaticJsonDocument<256> doc;
    JsonArray m = doc.createNestedArray("moisture");
    m.add(sensors.m[0]); m.add(sensors.m[1]); m.add(sensors.m[2]);
    doc["temperature"] = sensors.temperature;
    doc["humidity"]    = sensors.humidity;
    doc["pico_online"] = sensors.pico_online;
    char buf[256]; serializeJson(doc, buf);
    http.send(200, "application/json", buf);
  });
  http.on("/api/valve", HTTP_OPTIONS, []() { addCORS(); http.send(204); });
  http.on("/api/valve", HTTP_POST, []() {
    addCORS();
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, http.arg("plain")) == DeserializationError::Ok) {
      handleCommand(doc);
      http.send(200, "application/json", "{\"ok\":true}");
    } else http.send(400, "application/json", "{\"error\":\"bad json\"}");
  });
  http.begin();
  Serial.println(F("[HTTP] Local API on port 80 ready"));
}

// ═════════════════════════════════════════════════════════════════════
// Local WebSocket (port 81)
// ═════════════════════════════════════════════════════════════════════
void wsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t length) {
  if (type == WStype_TEXT) {
    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, payload, length) == DeserializationError::Ok) {
      handleCommand(doc);
    }
  }
}

// ═════════════════════════════════════════════════════════════════════
// Status LED
//   No WiFi     → fast blink  (200ms)
//   WiFi, no MQTT → medium blink (600ms)
//   All good    → slow pulse  (2s)
// ═════════════════════════════════════════════════════════════════════
void updateLED() {
  unsigned long now = millis();
  unsigned long interval;
  if (WiFi.status() != WL_CONNECTED) interval = 200;
  else if (!mqtt.connected())        interval = 600;
  else                               interval = 2000;
  if (now - lastLedToggle > interval) {
    lastLedToggle = now;
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
