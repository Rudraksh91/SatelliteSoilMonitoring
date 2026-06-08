/*
 * ╔══════════════════════════════════════════════════════════════╗
 * ║     TERRA-CORE AgriSat — ESP32 Irrigation Controller        ║
 * ║     Dediapada Farm · 21.6277°N, 73.5903°E · Gujarat         ║
 * ║     v1.0.0 — Compatible with Raspberry Pi Pico Sensor Hub   ║
 * ╚══════════════════════════════════════════════════════════════╝
 *
 * LIBRARIES REQUIRED (install via Arduino IDE > Library Manager):
 *   1. WebSockets by Markus Sattler          (v2.4.x)
 *   2. ArduinoJson by Benoit Blanchon        (v6.x)
 *   3. ESP32 board package from Espressif
 *      URL: https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json
 *
 * BOARD SETTINGS (Arduino IDE):
 *   Board: "ESP32 Dev Module"
 *   Upload Speed: 921600
 *   Flash Size: 4MB
 *   Partition Scheme: "Default 4MB with spiffs"
 */

#include <WiFi.h>
#include <WebServer.h>
#include <WebSocketsServer.h>
#include <ArduinoJson.h>
#include <HardwareSerial.h>

// ════════════════════════════════════════════
// ⚙  CONFIGURATION — EDIT BEFORE FLASHING
// ════════════════════════════════════════════
const char* WIFI_SSID      = "YOUR_WIFI_NAME";       // Your WiFi SSID
const char* WIFI_PASSWORD  = "YOUR_WIFI_PASSWORD";    // Your WiFi password
const char* FARM_ID        = "DEDIAPADA-01";
const char* FIRMWARE_VER   = "1.0.0";

// Auto-irrigation thresholds (moisture %)
const float AUTO_OPEN_THRESHOLD  = 30.0;  // Open valve if moisture < 30%
const float AUTO_CLOSE_THRESHOLD = 65.0;  // Close valve if moisture > 65%

// ════════════════════════════════════════════
// 📌 PIN DEFINITIONS
// ════════════════════════════════════════════
// Solenoid Valve Relays (ACTIVE LOW — relay turns ON when GPIO is LOW)
#define RELAY_Z1     25    // Zone 1 / FIELD-A
#define RELAY_Z2     26    // Zone 2 / FIELD-B
#define RELAY_Z3     27    // Zone 3 / FIELD-C
#define LED_STATUS    2    // Onboard LED (blue)

// UART2 ↔ Raspberry Pi Pico
#define PICO_RX      16    // ESP32 GPIO16 (RX2) ← Pico GPIO0 (TX)
#define PICO_TX      17    // ESP32 GPIO17 (TX2) → Pico GPIO1 (RX)
#define PICO_BAUD  9600

// ════════════════════════════════════════════
// 🌐 SERVER INSTANCES
// ════════════════════════════════════════════
WebServer        http(80);
WebSocketsServer ws(81);
HardwareSerial   PicoSerial(2);   // UART2

// ════════════════════════════════════════════
// 📊 LIVE DATA STORE
// ════════════════════════════════════════════
struct SensorStore {
  float  moisture[3]  = {0, 0, 0};   // 0–100%  (Zone 1,2,3)
  float  temperature  = 0;            // °C
  float  humidity     = 0;            // %
  float  soilTemp     = 0;            // °C (if DS18B20 fitted to Pico)
  float  lightLux     = 0;            // lux (optional BH1750)
  bool   picoOnline   = false;
  unsigned long lastPicoMsg = 0;
} sensors;

bool  valve[3]   = {false, false, false};
bool  autoMode   = false;   // Auto-irrigation mode
const uint8_t RELAY[3] = {RELAY_Z1, RELAY_Z2, RELAY_Z3};

// ════════════════════════════════════════════
// 🔧 VALVE CONTROL
// ════════════════════════════════════════════
void setValve(int zone, bool open, bool silent = false) {
  if (zone < 1 || zone > 3) return;
  valve[zone - 1] = open;
  // Active-LOW relay: LOW = valve OPEN, HIGH = valve CLOSED
  digitalWrite(RELAY[zone - 1], open ? LOW : HIGH);
  if (!silent) {
    Serial.printf("[VALVE] Z%d → %s\n", zone, open ? "OPEN" : "CLOSED");
    ws.broadcastTXT(buildJSON());
  }
}

void emergencyStopAll() {
  for (int z = 1; z <= 3; z++) setValve(z, false, true);
  Serial.println("[EMERGENCY] All valves CLOSED");
  ws.broadcastTXT(buildJSON());
}

// ════════════════════════════════════════════
// 📦 JSON BUILDER
// ════════════════════════════════════════════
String buildJSON() {
  StaticJsonDocument<640> doc;
  doc["farm"]         = FARM_ID;
  doc["fw"]           = FIRMWARE_VER;
  doc["ip"]           = WiFi.localIP().toString();
  doc["rssi"]         = WiFi.RSSI();
  doc["uptime"]       = millis() / 1000;
  doc["heap"]         = ESP.getFreeHeap();
  doc["auto_mode"]    = autoMode;
  doc["pico_online"]  = sensors.picoOnline;

  JsonArray m = doc.createNestedArray("moisture");
  m.add(round(sensors.moisture[0] * 10) / 10.0);
  m.add(round(sensors.moisture[1] * 10) / 10.0);
  m.add(round(sensors.moisture[2] * 10) / 10.0);

  doc["temperature"]  = round(sensors.temperature * 10) / 10.0;
  doc["humidity"]     = round(sensors.humidity * 10) / 10.0;
  doc["soil_temp"]    = round(sensors.soilTemp * 10) / 10.0;
  doc["light_lux"]    = sensors.lightLux;

  JsonArray v = doc.createNestedArray("valve");
  v.add(valve[0]);
  v.add(valve[1]);
  v.add(valve[2]);

  String out;
  serializeJson(doc, out);
  return out;
}

// ════════════════════════════════════════════
// 🔌 PICO UART PARSER
// Message format from Pico:
//   M1:45.2,M2:38.7,M3:52.1,T:28.4,H:65.3,ST:24.1,L:1200
// ════════════════════════════════════════════
void parsePicoLine(const String& line) {
  int pos = 0;
  while (pos < (int)line.length()) {
    int comma = line.indexOf(',', pos);
    String token = (comma < 0) ? line.substring(pos) : line.substring(pos, comma);
    int colon = token.indexOf(':');
    if (colon > 0) {
      String key = token.substring(0, colon);
      float  val = token.substring(colon + 1).toFloat();
      if      (key == "M1") sensors.moisture[0] = constrain(val, 0, 100);
      else if (key == "M2") sensors.moisture[1] = constrain(val, 0, 100);
      else if (key == "M3") sensors.moisture[2] = constrain(val, 0, 100);
      else if (key == "T")  sensors.temperature  = val;
      else if (key == "H")  sensors.humidity      = constrain(val, 0, 100);
      else if (key == "ST") sensors.soilTemp      = val;
      else if (key == "L")  sensors.lightLux      = val;
    }
    pos = (comma < 0) ? line.length() : comma + 1;
  }
  sensors.picoOnline   = true;
  sensors.lastPicoMsg  = millis();
}

// ════════════════════════════════════════════
// 🌐 HTTP CORS HELPER
// ════════════════════════════════════════════
void cors() {
  http.sendHeader("Access-Control-Allow-Origin",  "*");
  http.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  http.sendHeader("Access-Control-Allow-Headers", "Content-Type,Authorization");
  http.sendHeader("Cache-Control",                "no-cache");
}

// ════════════════════════════════════════════
// 🛣  HTTP ROUTES
// ════════════════════════════════════════════
void routeSensors() {
  cors();
  http.send(200, "application/json", buildJSON());
}

void routeValve() {
  cors();
  if (!http.hasArg("plain")) { http.send(400, "application/json", "{\"error\":\"no body\"}"); return; }
  StaticJsonDocument<128> doc;
  if (deserializeJson(doc, http.arg("plain"))) { http.send(400, "application/json", "{\"error\":\"bad json\"}"); return; }
  int  z     = doc["zone"]  | 0;
  bool state = doc["state"] | false;
  if (z < 1 || z > 3) { http.send(400, "application/json", "{\"error\":\"zone must be 1-3\"}"); return; }
  setValve(z, state);
  http.send(200, "application/json", "{\"ok\":true}");
}

void routeStop() {
  cors();
  emergencyStopAll();
  http.send(200, "application/json", "{\"ok\":true,\"action\":\"emergency_stop\"}");
}

void routeMode() {
  cors();
  if (http.hasArg("plain")) {
    StaticJsonDocument<64> doc;
    deserializeJson(doc, http.arg("plain"));
    if (doc.containsKey("auto")) {
      autoMode = doc["auto"].as<bool>();
      if (!autoMode) emergencyStopAll();
      Serial.printf("[MODE] → %s\n", autoMode ? "AUTO" : "MANUAL");
    }
  }
  http.send(200, "application/json", buildJSON());
}

void routeInfo() {
  cors();
  StaticJsonDocument<256> doc;
  doc["farm_id"]   = FARM_ID;
  doc["firmware"]  = FIRMWARE_VER;
  doc["chip"]      = "ESP32";
  doc["mac"]       = WiFi.macAddress();
  doc["ip"]        = WiFi.localIP().toString();
  doc["ssid"]      = WIFI_SSID;
  doc["rssi"]      = WiFi.RSSI();
  doc["uptime"]    = millis() / 1000;
  doc["heap"]      = ESP.getFreeHeap();
  String out; serializeJson(doc, out);
  http.send(200, "application/json", out);
}

void routeOptions() {
  cors();
  http.send(204, "text/plain", "");
}

// ════════════════════════════════════════════
// 🔌 WEBSOCKET EVENT HANDLER
// ════════════════════════════════════════════
void wsEvent(uint8_t num, WStype_t type, uint8_t* payload, size_t len) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("[WS] Client #%u connected from %s\n", num, ws.remoteIP(num).toString().c_str());
      ws.sendTXT(num, buildJSON());   // Send current state immediately
      break;

    case WStype_DISCONNECTED:
      Serial.printf("[WS] Client #%u disconnected\n", num);
      break;

    case WStype_TEXT: {
      StaticJsonDocument<128> doc;
      if (!deserializeJson(doc, payload, len)) {
        // Handle valve command via WebSocket
        if (doc.containsKey("zone") && doc.containsKey("state")) {
          setValve(doc["zone"].as<int>(), doc["state"].as<bool>());
        }
        // Handle emergency stop via WebSocket
        if (doc["emergency_stop"].as<bool>()) {
          emergencyStopAll();
        }
        // Handle mode change via WebSocket
        if (doc.containsKey("auto_mode")) {
          autoMode = doc["auto_mode"].as<bool>();
          if (!autoMode) emergencyStopAll();
          ws.broadcastTXT(buildJSON());
        }
      }
      break;
    }

    default: break;
  }
}

// ════════════════════════════════════════════
// 🚀 SETUP
// ════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println();
  Serial.println(F("╔═══════════════════════════════════╗"));
  Serial.println(F("║  TERRA-CORE AgriSat  ESP32 v1.0   ║"));
  Serial.println(F("╚═══════════════════════════════════╝"));

  // Init relay pins — ALL OFF (HIGH = relay coil OFF for active-low board)
  for (int i = 0; i < 3; i++) {
    pinMode(RELAY[i], OUTPUT);
    digitalWrite(RELAY[i], HIGH);
  }
  pinMode(LED_STATUS, OUTPUT);
  digitalWrite(LED_STATUS, LOW);

  // UART2 for Pico
  PicoSerial.begin(PICO_BAUD, SERIAL_8N1, PICO_RX, PICO_TX);
  Serial.printf("[UART] Pico serial on RX=GPIO%d TX=GPIO%d @%d baud\n", PICO_RX, PICO_TX, PICO_BAUD);

  // WiFi connect
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("[WiFi] Connecting to '%s'", WIFI_SSID);
  unsigned long t = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t < 20000) {
    delay(400);
    Serial.print(".");
    digitalWrite(LED_STATUS, !digitalRead(LED_STATUS));
  }
  if (WiFi.status() == WL_CONNECTED) {
    digitalWrite(LED_STATUS, HIGH);
    Serial.println();
    Serial.println("[WiFi] ✓ Connected!");
    Serial.print("[WiFi] IP Address: "); Serial.println(WiFi.localIP());
    Serial.print("[WiFi] Signal:     "); Serial.print(WiFi.RSSI()); Serial.println(" dBm");
  } else {
    // Fallback: create soft AP for direct connection
    WiFi.softAP("AgriSat-Dediapada", "agrisat123");
    Serial.println("[WiFi] ✗ Station failed — AP mode active");
    Serial.print("[WiFi] AP IP: "); Serial.println(WiFi.softAPIP());
    digitalWrite(LED_STATUS, LOW);
  }

  // HTTP routes
  http.on("/api/sensors",   HTTP_GET,     routeSensors);
  http.on("/api/valve",     HTTP_POST,    routeValve);
  http.on("/api/valve",     HTTP_OPTIONS, routeOptions);
  http.on("/api/stop",      HTTP_POST,    routeStop);
  http.on("/api/stop",      HTTP_OPTIONS, routeOptions);
  http.on("/api/mode",      HTTP_POST,    routeMode);
  http.on("/api/mode",      HTTP_OPTIONS, routeOptions);
  http.on("/api/info",      HTTP_GET,     routeInfo);
  http.on("/",              HTTP_GET,     []() {
    cors();
    http.send(200, "text/plain",
      "TERRA-CORE AgriSat ESP32 Online\n"
      "API:       http://" + WiFi.localIP().toString() + "/api/sensors\n"
      "WebSocket: ws://"   + WiFi.localIP().toString() + ":81\n"
      "Firmware:  v" + String(FIRMWARE_VER));
  });
  http.onNotFound([]() { cors(); http.send(404, "application/json", "{\"error\":\"not found\"}"); });
  http.begin();
  Serial.println("[HTTP] Server started on port 80");

  // WebSocket server
  ws.begin();
  ws.onEvent(wsEvent);
  Serial.println("[WS]   WebSocket server on port 81");

  Serial.println(F("══════════════════════════════════"));
  Serial.println("[READY] Dashboard integration:");
  Serial.print(  "[READY]   ESP32 IP → ");  Serial.println(WiFi.localIP());
  Serial.println("[READY] Enter IP in dashboard > Hardware Settings");
  Serial.println(F("══════════════════════════════════"));
}

// ════════════════════════════════════════════
// 🔄 LOOP
// ════════════════════════════════════════════
unsigned long lastBroadcast = 0;
unsigned long lastAutoCheck  = 0;
unsigned long lastLEDBlink   = 0;
String picoBuffer = "";

void loop() {
  http.handleClient();
  ws.loop();

  // ── Read UART from Pico (line-buffered)
  while (PicoSerial.available()) {
    char c = (char)PicoSerial.read();
    if (c == '\n') {
      picoBuffer.trim();
      if (picoBuffer.length() > 5) {
        parsePicoLine(picoBuffer);
      }
      picoBuffer = "";
    } else if (picoBuffer.length() < 128) {
      picoBuffer += c;
    }
  }

  // ── Pico offline detection (10 s silence)
  if (sensors.picoOnline && millis() - sensors.lastPicoMsg > 10000) {
    sensors.picoOnline = false;
    Serial.println("[PICO] ✗ Offline — no data for 10 s");
    ws.broadcastTXT(buildJSON());
  }

  // ── Broadcast sensor data every 2 s
  if (millis() - lastBroadcast > 2000) {
    ws.broadcastTXT(buildJSON());
    lastBroadcast = millis();
  }

  // ── Auto-irrigation logic every 5 s
  if (autoMode && millis() - lastAutoCheck > 5000) {
    for (int i = 0; i < 3; i++) {
      float m = sensors.moisture[i];
      if (m > 0) {   // Only act if sensor is online
        if (m < AUTO_OPEN_THRESHOLD  && !valve[i]) setValve(i + 1, true);
        if (m > AUTO_CLOSE_THRESHOLD &&  valve[i]) setValve(i + 1, false);
      }
    }
    lastAutoCheck = millis();
  }

  // ── WiFi watchdog — reconnect if dropped
  if (WiFi.status() != WL_CONNECTED && millis() % 30000 < 100) {
    Serial.println("[WiFi] Reconnecting...");
    WiFi.reconnect();
  }

  // ── Status LED: fast blink = no WiFi | slow blink = OK | solid = pico online
  unsigned long blinkRate = (WiFi.status() != WL_CONNECTED) ? 150
                          : sensors.picoOnline              ? 1500
                          : 600;
  if (millis() - lastLEDBlink > blinkRate) {
    digitalWrite(LED_STATUS, !digitalRead(LED_STATUS));
    lastLEDBlink = millis();
  }
}
