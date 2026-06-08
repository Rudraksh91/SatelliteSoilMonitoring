/*
 * ══════════════════════════════════════════════════════════════════════
 * TERRA-CORE AgriSat — ESP32 Field Node
 * ══════════════════════════════════════════════════════════════════════
 *
 * Role: Sits in the field, far from the hub.
 *       - Receives valve commands from the Hub via LoRa
 *       - Controls the solenoid valve (relay)
 *       - Reads local soil moisture sensor
 *       - Reads DHT22 temperature/humidity
 *       - Reports status back to Hub via LoRa every 5s
 *       - Each node has its own gravity tank
 *
 * IMPORTANT: Set NODE_ZONE (1, 2, or 3) before flashing each device!
 *            Flash 3 different ESP32s with NODE_ZONE = 1, 2, 3
 *
 * Hardware (per field node):
 *   - ESP32 DevKit V1
 *   - SX1276 / RA-02 LoRa module
 *   - 1x Capacitive Soil Moisture Sensor (ADC pin)
 *   - 1x DHT22 (temp + humidity)
 *   - 1x Relay module → Solenoid valve
 *   - 12V DC supply for solenoid + 5V for ESP32
 *   - (optional) LiPo + solar for off-grid operation
 *
 * LoRa wiring (same as Hub):
 *   SX1276 VCC  → 3.3V
 *   SX1276 GND  → GND
 *   SX1276 SCK  → GPIO18
 *   SX1276 MISO → GPIO19
 *   SX1276 MOSI → GPIO23
 *   SX1276 NSS  → GPIO5
 *   SX1276 DIO0 → GPIO26
 *   SX1276 RST  → GPIO14
 *
 * Other pins:
 *   Moisture sensor → GPIO34 (ADC1_CH6 — input only)
 *   DHT22 DATA      → GPIO4
 *   Relay IN        → GPIO25  (active-LOW: LOW = valve OPEN)
 *   Status LED      → GPIO2
 *
 * LoRa message protocol:
 *   Receive: CMD:Z{n}:OPEN / CMD:Z{n}:CLOSE / CMD:ALL:STOP
 *   Send:    STS:Z{n}:M:{moisture}:V:{0|1}:T:{temp}:H:{hum}
 *
 * Libraries: LoRa (Sandeep Mistry), DHT sensor library (Adafruit)
 * ══════════════════════════════════════════════════════════════════════
 */

#include <SPI.h>
#include <LoRa.h>
#include <DHT.h>

// ═══════════════════════════════════════════════════════
// !! CHANGE THIS FOR EACH FIELD NODE BEFORE FLASHING !!
// ═══════════════════════════════════════════════════════
#define NODE_ZONE   1   // 1, 2, or 3 — UNIQUE PER NODE
// ═══════════════════════════════════════════════════════

// ── LoRa SX1276 pins ─────────────────────────────────────────────────
#define LORA_SCK  18
#define LORA_MISO 19
#define LORA_MOSI 23
#define LORA_NSS   5
#define LORA_DIO0 26
#define LORA_RST  14
#define LORA_FREQ 433E6   // 433 MHz — must match Hub + Pi!

// ── Sensor / actuator pins ────────────────────────────────────────────
#define PIN_MOISTURE  34   // ADC1 — capacitive soil moisture sensor
#define PIN_DHT       4    // DHT22 data pin
#define PIN_RELAY     25   // Active-LOW relay → solenoid valve
#define PIN_LED       2    // Built-in LED

// ── Moisture calibration ──────────────────────────────────────────────
// Run with valve closed in dry soil → note ADC value → CAL_DRY
// Submerge in water → note ADC value → CAL_WET
// (Higher ADC = drier with capacitive sensors)
#define CAL_DRY  3400    // ADC reading in completely dry soil
#define CAL_WET   900    // ADC reading in saturated soil
#define SAMPLES    10    // ADC samples to average per reading

// ── LoRa addressing ───────────────────────────────────────────────────
const uint8_t ADDR_HUB  = 0x01;
const uint8_t MY_ADDR   = 0x10 + NODE_ZONE;  // 0x11, 0x12, or 0x13

// ── DHT ───────────────────────────────────────────────────────────────
DHT dht(PIN_DHT, DHT22);

// ── State ─────────────────────────────────────────────────────────────
bool  valveOpen  = false;
float moisture   = 0;
float temp       = 0;
float humidity   = 0;

unsigned long lastReport = 0;
unsigned long lastSensor = 0;
unsigned long lastLed    = 0;
bool ledState = false;

// ── Valve open timestamp for safety timeout ───────────────────────────
unsigned long valveOpenedAt  = 0;
const unsigned long MAX_VALVE_OPEN_MS = 3600000UL; // 1 hour safety limit

// ══════════════════════════════════════════════════════════════════════
// SETUP
// ══════════════════════════════════════════════════════════════════════
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.printf("\n[TERRA-CORE] Field Node Z%d (addr=0x%02X) booting…\n",
                NODE_ZONE, MY_ADDR);

  // ── Relay pin — start CLOSED (HIGH = valve closed, active-LOW)
  pinMode(PIN_RELAY, OUTPUT);
  digitalWrite(PIN_RELAY, HIGH);
  pinMode(PIN_LED, OUTPUT);

  // ── DHT22
  dht.begin();

  // ── LoRa
  SPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);
  LoRa.setPins(LORA_NSS, LORA_RST, LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) {
    Serial.println(F("[LoRa] INIT FAILED — check wiring!"));
    while (true) { digitalWrite(PIN_LED, !digitalRead(PIN_LED)); delay(100); }
  }
  LoRa.setSpreadingFactor(7);
  LoRa.setSignalBandwidth(125E3);
  LoRa.setCodingRate4(5);
  LoRa.enableCrc();
  Serial.printf("[LoRa] Node Z%d ready on %.0f MHz\n", NODE_ZONE, LORA_FREQ / 1E6);
}

// ══════════════════════════════════════════════════════════════════════
// LOOP
// ══════════════════════════════════════════════════════════════════════
void loop() {
  // ── Receive LoRa commands from Hub ───────────────────────────────
  int pktSize = LoRa.parsePacket();
  if (pktSize > 0) {
    handleLoRaRx(pktSize);
  }

  // ── Read sensors every 2s ─────────────────────────────────────────
  if (millis() - lastSensor > 2000) {
    lastSensor = millis();
    readSensors();
  }

  // ── Report status to Hub every 5s ────────────────────────────────
  if (millis() - lastReport > 5000) {
    lastReport = millis();
    sendStatus();
  }

  // ── Safety: auto-close valve after 1 hour ────────────────────────
  if (valveOpen && millis() - valveOpenedAt > MAX_VALVE_OPEN_MS) {
    Serial.println(F("[SAFETY] Valve open > 1 hour — auto-closing"));
    setValve(false);
  }

  updateLED();
}

// ══════════════════════════════════════════════════════════════════════
// LoRa receive
// Packet format from Hub:
//   Byte 0: destination addr (our MY_ADDR)
//   Byte 1: source addr (ADDR_HUB)
//   Byte 2+: CMD string
// ══════════════════════════════════════════════════════════════════════
void handleLoRaRx(int pktSize) {
  // Read first two address bytes
  uint8_t dest   = LoRa.read();
  uint8_t src    = LoRa.read();
  String  msg    = "";
  while (LoRa.available()) {
    msg += (char)LoRa.read();
  }

  // Accept if addressed to us or broadcast (0xFF)
  if (dest != MY_ADDR && dest != 0xFF && !msg.startsWith("CMD:ALL")) {
    // Not for us — ignore
    return;
  }

  int rssi = LoRa.packetRssi();
  Serial.printf("[LoRa] RX from 0x%02X (%ddBm): %s\n", src, rssi, msg.c_str());

  // ── CMD:ALL:STOP
  if (msg.indexOf("ALL:STOP") >= 0) {
    Serial.println(F("[CMD] EMERGENCY STOP"));
    setValve(false);
    return;
  }

  // ── CMD:Z{n}:OPEN / CMD:Z{n}:CLOSE
  String myPrefix = "CMD:Z" + String(NODE_ZONE) + ":";
  if (!msg.startsWith(myPrefix)) return;   // not our zone

  String action = msg.substring(myPrefix.length());
  if (action == "OPEN") {
    setValve(true);
  } else if (action == "CLOSE") {
    setValve(false);
  }
}

// ══════════════════════════════════════════════════════════════════════
// Valve control
// ══════════════════════════════════════════════════════════════════════
void setValve(bool open) {
  valveOpen = open;
  digitalWrite(PIN_RELAY, open ? LOW : HIGH);  // Active-LOW relay
  if (open) valveOpenedAt = millis();
  Serial.printf("[Valve] Zone %d %s\n", NODE_ZONE, open ? "OPEN" : "CLOSED");
  // Flash LED twice on valve change
  for (int i = 0; i < 4; i++) {
    digitalWrite(PIN_LED, i % 2);
    delay(80);
  }
}

// ══════════════════════════════════════════════════════════════════════
// Read moisture + DHT22
// ══════════════════════════════════════════════════════════════════════
void readSensors() {
  // ── Capacitive moisture (multi-sample average) ────────────────────
  long sum = 0;
  for (int i = 0; i < SAMPLES; i++) {
    sum += analogRead(PIN_MOISTURE);
    delay(10);
  }
  long raw = sum / SAMPLES;
  // Map: CAL_DRY (= 0%) to CAL_WET (= 100%), clamp 0–100
  float pct = (float)(CAL_DRY - raw) / (float)(CAL_DRY - CAL_WET) * 100.0f;
  moisture = constrain(pct, 0.0f, 100.0f);

  // ── DHT22 ─────────────────────────────────────────────────────────
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  if (!isnan(t)) temp     = t;
  if (!isnan(h)) humidity = h;
}

// ══════════════════════════════════════════════════════════════════════
// Send status to Hub
// Format: STS:Z1:M:45.2:V:1:T:28.4:H:63.1
// ══════════════════════════════════════════════════════════════════════
void sendStatus() {
  char buf[80];
  snprintf(buf, sizeof(buf),
    "STS:Z%d:M:%.1f:V:%d:T:%.1f:H:%.1f",
    NODE_ZONE, moisture, valveOpen ? 1 : 0, temp, humidity
  );

  LoRa.beginPacket();
  LoRa.write(ADDR_HUB);      // destination: Hub
  LoRa.write(MY_ADDR);       // source: this node
  LoRa.print(buf);
  LoRa.endPacket();
  Serial.printf("[LoRa] STS: %s\n", buf);
}

// ══════════════════════════════════════════════════════════════════════
// Status LED
//   Valve open  → fast blink 200ms (yellow visual indicator)
//   Valve closed → slow pulse 2s
// ══════════════════════════════════════════════════════════════════════
void updateLED() {
  unsigned long now = millis();
  unsigned long interval = valveOpen ? 200 : 2000;
  if (now - lastLed > interval) {
    lastLed  = now;
    ledState = !ledState;
    digitalWrite(PIN_LED, ledState);
  }
}
