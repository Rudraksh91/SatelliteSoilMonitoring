/*
 * ══════════════════════════════════════════════════════════════════════
 * TERRA-CORE AgriSat — ESP32 Hub (Main Gateway)
 * ══════════════════════════════════════════════════════════════════════
 *
 * Role: Sits in the middle between the Raspberry Pi brain and
 *       the 3 field ESP32 nodes. Acts as a LoRa relay + WiFi bridge.
 *
 * ┌────────────────────────────────────────────────────────────────┐
 * │   Raspberry Pi                                                 │
 * │   (NDVI brain)  ──LoRa──►  ESP32 HUB  ──LoRa──►  Field Node 1│
 * │                                        ──LoRa──►  Field Node 2│
 * │                                        ──LoRa──►  Field Node 3│
 * │                             │                                  │
 * │                             ▼WiFi                              │
 * │                         Dashboard (anywhere via MQTT)          │
 * └────────────────────────────────────────────────────────────────┘
 *
 * Hardware:
 *   - ESP32 DevKit V1
 *   - SX1276 / RA-02 LoRa module (SPI)
 *
 * LoRa wiring (ESP32 ↔ SX1276):
 *   SX1276 VCC  → 3.3V
 *   SX1276 GND  → GND
 *   SX1276 SCK  → GPIO18
 *   SX1276 MISO → GPIO19
 *   SX1276 MOSI → GPIO23
 *   SX1276 NSS  → GPIO5
 *   SX1276 DIO0 → GPIO26   (interrupt)
 *   SX1276 RST  → GPIO14
 *
 * Libraries (Arduino Library Manager):
 *   - LoRa by Sandeep Mistry (v0.8.0+)
 *   - PubSubClient by Nick O'Leary
 *   - ArduinoJson by Benoit Blanchon (v6.x)
 *   - WebSockets by Markus Sattler
 *
 * LoRa message protocol:
 *   Pi → Hub (CMD):     CMD:Z1:OPEN  / CMD:Z2:CLOSE / CMD:ALL:STOP
 *   Hub → Node (CMD):   same format relayed verbatim
 *   Node → Hub (STS):   STS:Z1:M:45.2:V:1:T:28.4:H:63.1
 *   Hub → Dashboard:    JSON via MQTT/WebSocket
 * ══════════════════════════════════════════════════════════════════════
 */

#include <SPI.h>
#include <LoRa.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <WebServer.h>
#include <WebSocketsServer.h>

// ── WiFi ──────────────────────────────────────────────────────────────
const char* WIFI_SSID     = "YOUR_WIFI_SSID";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ── Farm ID (must match Pi brain + dashboard) ─────────────────────────
const char* FARM_ID = "dediapada-farm-01";

// ── MQTT ──────────────────────────────────────────────────────────────
const char* MQTT_HOST = "broker.hivemq.com";
const int   MQTT_PORT = 1883;
char TOPIC_DATA[64];
char TOPIC_CMD[64];

// ── LoRa SX1276 pins ─────────────────────────────────────────────────
#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23
#define LORA_NSS   5
#define LORA_DIO0 26
#define LORA_RST  14
#define LORA_FREQ 433E6   // 433 MHz — change to 868E6 for Europe

// ── LED ───────────────────────────────────────────────────────────────
#define LED_PIN 2

// ── Addresses ─────────────────────────────────────────────────────────
// Hub listens to anything. Pi sends: CMD:Z{n}:...
// Hub adds addressing header when relaying to nodes.
//   Header byte 0: destination node ID (0x11, 0x12, 0x13)
//   Header byte 1: source (0x01 = hub)
const uint8_t ADDR_HUB  = 0x01;
const uint8_t ADDR_PI   = 0x00;
const uint8_t NODE_ADDR[3] = {0x11, 0x12, 0x13};  // Field nodes Z1, Z2, Z3

// ── State per zone ────────────────────────────────────────────────────
struct ZoneState {
  float   moisture = 0;
  bool    valve    = false;
  float   temp     = 0;
  float   humidity = 0;
  bool    online   = false;
  unsigned long lastSeen = 0;
};
ZoneState zones[3];

// ── General state ─────────────────────────────────────────────────────
float piTemp     = 0;
float piHumidity = 0;
bool  piOnline   = false;
unsigned long piLastSeen = 0;

WiFiClient   wifiNet;
PubSubClient mqtt(wifiNet);
WebServer    http(80);
WebSocketsServer wsServer(81);

unsigned long lastPublish   = 0;
unsigned long lastMqttRetry = 0;
unsigned long lastLedBlink  = 0;
bool ledState = false;

// ══════════════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println(F("\n[TERRA-CORE] ESP32 Hub booting…"));

  snprintf(TOPIC_DATA, sizeof(TOPIC_DATA), "agrisat/%s/data", FARM_ID);
  snprintf(TOPIC_CMD,  sizeof(TOPIC_CMD),  "agrisat/%s/cmd",  FARM_ID);

  pinMode(LED_PIN, OUTPUT);

  initLoRa();
  connectWiFi();

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setCallback(mqttCallback);
  mqtt.setBufferSize(512);
  connectMQTT();

  setupHTTP();
  wsServer.begin();
  wsServer.onEvent(wsEvent);

  Serial.println(F("[TERRA-CORE] Hub ready ✓"));
}

// ══════════════════════════════════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════════════════════════════════
void loop() {
  // ── Receive LoRa packets ─────────────────────────────────────────
  int packetSize = LoRa.parsePacket();
  if (packetSize > 0) {
    handleLoRaRx(packetSize);
  }

  // ── MQTT ─────────────────────────────────────────────────────────
  if (!mqtt.connected()) {
    unsigned long now = millis();
    if (now - lastMqttRetry > 5000) {
      lastMqttRetry = now;
      connectMQTT();
    }
  }
  mqtt.loop();

  // ── HTTP / WS ─────────────────────────────────────────────────────
  http.handleClient();
  wsServer.loop();

  // ── Publish state every 2s ────────────────────────────────────────
  if (millis() - lastPublish > 2000) {
    lastPublish = millis();
    publishState();
  }

  // ── Check node timeouts (offline after 30s) ───────────────────────
  checkTimeouts();

  updateLED();
}

// ══════════════════════════════════════════════════════════════════════
// LoRa
// ══════════════════════════════════════════════════════════════════════
void initLoRa() {
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  LoRa.setPins(LORA_NSS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println(F("[LoRa] Init FAILED — check wiring!"));
    while (true) { digitalWrite(LED_PIN, !digitalRead(LED_PIN)); delay(100); }
  }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.enableCrc();
  Serial.printf("[LoRa] Ready on %.0f MHz SF7 BW125\n", LORA_FREQ / 1E6);
}

void handleLoRaRx(int packetSize) {
  String msg = "";
  while (LoRa.available()) {
    msg += (char)LoRa.read();
  }
  int rssi = LoRa.packetRssi();
  Serial.printf("[LoRa] RX (%ddBm): %s\n", rssi, msg.c_str());

  if (msg.startsWith("CMD:")) {
    // From Raspberry Pi brain — relay to appropriate field node
    relayToNode(msg);
  } else if (msg.startsWith("STS:")) {
    // Status from a field node — parse and store
    parseNodeStatus(msg);
  }
}

void relayToNode(const String& cmd) {
  /*
   * CMD:Z1:OPEN  → relay to node 0x11
   * CMD:Z2:CLOSE → relay to node 0x12
   * CMD:ALL:STOP → broadcast to all nodes
   */
  Serial.printf("[LoRa] Relaying to nodes: %s\n", cmd.c_str());
  if (cmd.indexOf("ALL") > 0) {
    // Broadcast to all 3 nodes
    for (int i = 0; i < 3; i++) {
      loRaSend(NODE_ADDR[i], cmd);
    }
    return;
  }
  // Extract zone number
  int zIdx = cmd.indexOf(":Z");
  if (zIdx < 0) return;
  int zNum = cmd.charAt(zIdx + 2) - '0';  // "Z1" → 1
  if (zNum < 1 || zNum > 3) return;
  loRaSend(NODE_ADDR[zNum - 1], cmd);
}

void loRaSend(uint8_t dest, const String& msg) {
  /*
   * Packet format:
   *   Byte 0: destination address
   *   Byte 1: source (hub)
   *   Byte 2+: payload string
   */
  LoRa.beginPacket();
  LoRa.write(dest);
  LoRa.write(ADDR_HUB);
  LoRa.print(msg);
  LoRa.endPacket();
  Serial.printf("[LoRa] TX → 0x%02X: %s\n", dest, msg.c_str());
}

// ══════════════════════════════════════════════════════════════════════
// Parse node status
// STS:Z1:M:45.2:V:1:T:28.4:H:63.1
// ══════════════════════════════════════════════════════════════════════
void parseNodeStatus(const String& msg) {
  // Extract zone index
  int zIdx = msg.indexOf(":Z");
  if (zIdx < 0) return;
  int zNum = msg.charAt(zIdx + 2) - '0';  // 1-based
  if (zNum < 1 || zNum > 3) return;
  int zi = zNum - 1;

  auto extractFloat = [&](const char* key) -> float {
    int i = msg.indexOf(key);
    if (i < 0) return 0;
    i += strlen(key);
    int e = msg.indexOf(":", i);
    return (e < 0 ? msg.substring(i) : msg.substring(i, e)).toFloat();
  };

  zones[zi].moisture = extractFloat("M:");
  zones[zi].valve    = extractFloat("V:") > 0.5f;
  zones[zi].temp     = extractFloat("T:");
  zones[zi].humidity = extractFloat("H:");
  zones[zi].online   = true;
  zones[zi].lastSeen = millis();

  Serial.printf("[Zone %d] M=%.1f%% V=%s T=%.1f°C H=%.1f%%\n",
    zNum, zones[zi].moisture,
    zones[zi].valve ? "OPEN" : "CLOSED",
    zones[zi].temp, zones[zi].humidity);
}

void checkTimeouts() {
  unsigned long now = millis();
  for (int i = 0; i < 3; i++) {
    if (zones[i].online && now - zones[i].lastSeen > 30000) {
      zones[i].online = false;
      Serial.printf("[WARN] Zone %d offline (no data for 30s)\n", i+1);
    }
  }
}

// ══════════════════════════════════════════════════════════════════════
// Publish full state (MQTT + local WebSocket)
// ══════════════════════════════════════════════════════════════════════
void publishState() {
  StaticJsonDocument<512> doc;

  JsonArray m = doc.createNestedArray("moisture");
  JsonArray v = doc.createNestedArray("valve");
  JsonArray o = doc.createNestedArray("zone_online");
  for (int i = 0; i < 3; i++) {
    m.add(zones[i].moisture);
    v.add(zones[i].valve);
    o.add(zones[i].online);
  }

  // Use Zone 1's temp/humidity as primary (or Pi's if available)
  doc["temperature"] = zones[0].temp > 0 ? zones[0].temp : piTemp;
  doc["humidity"]    = zones[0].humidity > 0 ? zones[0].humidity : piHumidity;
  doc["pico_online"] = true;  // Pi is the "pico" from the dashboard perspective
  doc["source"]      = "esp32_hub";
  doc["farm"]        = FARM_ID;

  char buf[512];
  size_t len = serializeJson(doc, buf);

  // Publish to MQTT cloud
  if (mqtt.connected()) {
    mqtt.publish(TOPIC_DATA, (const uint8_t*)buf, len, false);
  }

  // Broadcast on local WebSocket
  wsServer.broadcastTXT(buf, len);
}

// ══════════════════════════════════════════════════════════════════════
// MQTT
// ══════════════════════════════════════════════════════════════════════
void connectMQTT() {
  if (WiFi.status() != WL_CONNECTED) return;
  Serial.print(F("[MQTT] Connecting…"));
  char cid[32];
  snprintf(cid, sizeof(cid), "hub-%s-%04X", FARM_ID, (uint16_t)(ESP.getEfuseMac() & 0xFFFF));
  if (mqtt.connect(cid)) {
    Serial.println(F(" OK"));
    mqtt.subscribe(TOPIC_CMD);
  } else {
    Serial.printf(" FAILED rc=%d\n", mqtt.state());
  }
}

void mqttCallback(char* topic, byte* payload, unsigned int len) {
  // Dashboard manual override → forward to field node via LoRa
  StaticJsonDocument<256> doc;
  if (deserializeJson(doc, payload, len) != DeserializationError::Ok) return;

  if (doc["emergency_stop"].as<bool>()) {
    for (int i = 0; i < 3; i++) loRaSend(NODE_ADDR[i], "CMD:ALL:STOP");
    return;
  }
  int  zone  = doc["zone"].as<int>();
  bool state = doc["state"].as<bool>();
  if (zone >= 1 && zone <= 3) {
    String cmd = "CMD:Z" + String(zone) + ":" + (state ? "OPEN" : "CLOSE");
    loRaSend(NODE_ADDR[zone - 1], cmd);
  }
}

// ══════════════════════════════════════════════════════════════════════
// WiFi
// ══════════════════════════════════════════════════════════════════════
void connectWiFi() {
  Serial.print(F("[WiFi] Connecting…"));
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  int t = 0;
  while (WiFi.status() != WL_CONNECTED && t < 30) {
    delay(500); Serial.print('.'); t++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected · IP: %s\n", WiFi.localIP().toString().c_str());
  } else {
    Serial.println(F("\n[WiFi] Failed — SoftAP 'TERRA-CORE-Hub'"));
    WiFi.softAP("TERRA-CORE-Hub", "agrisat123");
  }
}

// ══════════════════════════════════════════════════════════════════════
// Local HTTP API
// ══════════════════════════════════════════════════════════════════════
void addCORS() {
  http.sendHeader("Access-Control-Allow-Origin",  "*");
  http.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  http.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}
void setupHTTP() {
  http.on("/", HTTP_GET, []() {
    addCORS();
    http.send(200, "text/plain", String("TERRA-CORE Hub · Farm: ") + FARM_ID);
  });
  http.on("/api/sensors", HTTP_GET, []() {
    addCORS();
    StaticJsonDocument<512> doc;
    JsonArray m = doc.createNestedArray("moisture");
    JsonArray v = doc.createNestedArray("valve");
    for (int i = 0; i < 3; i++) {
      m.add(zones[i].moisture);
      v.add(zones[i].valve);
    }
    doc["temperature"] = zones[0].temp;
    doc["humidity"]    = zones[0].humidity;
    char buf[512]; serializeJson(doc, buf);
    http.send(200, "application/json", buf);
  });
  http.on("/api/valve", HTTP_OPTIONS, []() { addCORS(); http.send(204); });
  http.on("/api/valve", HTTP_POST, []() {
    addCORS();
    StaticJsonDocument<128> doc;
    if (deserializeJson(doc, http.arg("plain")) == DeserializationError::Ok) {
      int z = doc["zone"].as<int>();
      bool s = doc["state"].as<bool>();
      if (z >= 1 && z <= 3) {
        String cmd = "CMD:Z" + String(z) + ":" + (s ? "OPEN" : "CLOSE");
        loRaSend(NODE_ADDR[z - 1], cmd);
      }
      http.send(200, "application/json", "{\"ok\":true}");
    } else http.send(400, "application/json", "{\"error\":\"bad json\"}");
  });
  http.on("/api/stop", HTTP_POST, []() {
    addCORS();
    for (int i = 0; i < 3; i++) loRaSend(NODE_ADDR[i], "CMD:ALL:STOP");
    http.send(200, "application/json", "{\"ok\":true}");
  });
  http.begin();
  Serial.println(F("[HTTP] Local API on port 80"));
}

// ── Local WebSocket handler ────────────────────────────────────────
void wsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t len) {
  if (type == WStype_TEXT) {
    StaticJsonDocument<256> doc;
    if (deserializeJson(doc, payload, len) != DeserializationError::Ok) return;
    if (doc["emergency_stop"].as<bool>()) {
      for (int i = 0; i < 3; i++) loRaSend(NODE_ADDR[i], "CMD:ALL:STOP");
    }
    int  z = doc["zone"].as<int>();
    bool s = doc["state"].as<bool>();
    if (z >= 1 && z <= 3) {
      String cmd = "CMD:Z" + String(z) + ":" + (s ? "OPEN" : "CLOSE");
      loRaSend(NODE_ADDR[z - 1], cmd);
    }
  }
}

// ══════════════════════════════════════════════════════════════════════
// Status LED
//   No WiFi     → fast  200ms
//   WiFi, no MQTT → medium 600ms
//   All OK       → slow  2000ms
// ══════════════════════════════════════════════════════════════════════
void updateLED() {
  unsigned long now = millis();
  unsigned long interval = (WiFi.status() != WL_CONNECTED) ? 200 :
                           (!mqtt.connected())              ? 600 : 2000;
  if (now - lastLedBlink > interval) {
    lastLedBlink = now;
    ledState = !ledState;
    digitalWrite(LED_PIN, ledState);
  }
}
