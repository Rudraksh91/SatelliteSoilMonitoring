# TERRA-CORE AgriSat — Full System Architecture
## Dediapada Farm, Gujarat

---

## System Overview

```
 ┌──────────────────────────────────────────────────────────────────┐
 │                    TERRA-CORE AgriSat                            │
 │                                                                  │
 │   Satellite EOS-04 SAR                                           │
 │         ↓  (NDVI harvester downloads data)                       │
 │   ┌─────────────────────────────────────────────────────────┐    │
 │   │         RASPBERRY PI  (Central Brain)                    │    │
 │   │  • Processes satellite NDVI                              │    │
 │   │  • Reads DHT22 weather sensor                            │    │
 │   │  • Runs AI irrigation decision engine                    │    │
 │   │  • Sends valve commands via LoRa                         │    │
 │   │  • Pushes live data to cloud dashboard via MQTT          │    │
 │   └──────────────────────┬──────────────────────────────────┘    │
 │                          │ LoRa 433 MHz                          │
 │                          │ (long range, low power)               │
 │   ┌──────────────────────▼──────────────────────────────────┐    │
 │   │         ESP32 HUB  (Main Gateway)                        │    │
 │   │  • Receives LoRa commands from Pi                        │    │
 │   │  • Routes commands to correct field node via LoRa        │    │
 │   │  • Receives status from field nodes, aggregates          │    │
 │   │  • WiFi → MQTT cloud (dashboard remote access)           │    │
 │   │  • WiFi → WebSocket (local dashboard access)             │    │
 │   └─────┬─────────────────────┬─────────────────────┬───────┘    │
 │         │ LoRa                │ LoRa                │ LoRa        │
 │         │                     │                     │             │
 │  ┌──────▼──────┐   ┌──────────▼──────┐   ┌─────────▼──────┐     │
 │  │ FIELD NODE 1│   │  FIELD NODE 2   │   │  FIELD NODE 3  │     │
 │  │  Zone A     │   │    Zone B       │   │    Zone C      │     │
 │  │  ─────────  │   │  ─────────────  │   │  ────────────  │     │
 │  │  LoRa Rx/Tx │   │  LoRa Rx/Tx    │   │  LoRa Rx/Tx   │     │
 │  │  Relay      │   │  Relay         │   │  Relay         │     │
 │  │  Solenoid ↓ │   │  Solenoid ↓    │   │  Solenoid ↓    │     │
 │  │  Gravity    │   │  Gravity       │   │  Gravity       │     │
 │  │  Tank A     │   │  Tank B        │   │  Tank C        │     │
 │  │  Soil       │   │  Soil          │   │  Soil          │     │
 │  │  Moisture   │   │  Moisture      │   │  Moisture      │     │
 │  │  DHT22      │   │  DHT22         │   │  DHT22         │     │
 │  └─────────────┘   └────────────────┘   └────────────────┘     │
 │                                                                  │
 │  Dashboard accessible via:                                       │
 │  • Same WiFi (PWA on phone/tablet)                               │
 │  • Anywhere in world (MQTT cloud via HiveMQ)                     │
 └──────────────────────────────────────────────────────────────────┘
```

---

## Component Roles

| Component | Device | Role | Firmware/Code |
|-----------|--------|------|---------------|
| Brain | Raspberry Pi | NDVI processing, AI decisions, LoRa commander | `hardware/raspberry_pi/brain.py` |
| Hub | ESP32 #1 | LoRa relay, WiFi bridge, MQTT gateway | `hardware/esp32/esp32_hub.ino` |
| Field Node 1 | ESP32 #2 | Zone A valve + sensors | `esp32_field_node.ino` (NODE_ZONE=1) |
| Field Node 2 | ESP32 #3 | Zone B valve + sensors | `esp32_field_node.ino` (NODE_ZONE=2) |
| Field Node 3 | ESP32 #4 | Zone C valve + sensors | `esp32_field_node.ino` (NODE_ZONE=3) |
| Dashboard | Any browser | Monitoring + manual control | `index.html` |

Total hardware needed: **1× Raspberry Pi + 4× ESP32 + 4× LoRa SX1276 modules**

---

## LoRa Communication Protocol

### Message Formats

| Direction | Format | Example |
|-----------|--------|---------|
| Pi → Hub (command) | `CMD:Z{n}:{OPEN\|CLOSE}` | `CMD:Z1:OPEN` |
| Pi → Hub (stop all) | `CMD:ALL:STOP` | `CMD:ALL:STOP` |
| Hub → Field Node | Same, with address header bytes | (same string) |
| Field Node → Hub | `STS:Z{n}:M:{%}:V:{0\|1}:T:{°C}:H:{%}` | `STS:Z1:M:45.2:V:1:T:28.4:H:63.1` |

### LoRa Packet Header
```
Byte 0: Destination address  (0x01=Hub, 0x11=Zone1, 0x12=Zone2, 0x13=Zone3)
Byte 1: Source address       (same table)
Byte 2+: Payload string
```

### LoRa Settings (must be IDENTICAL on all 4 devices)
```
Frequency:       433 MHz  (or 868 MHz for Europe/India LoRa band)
Spreading Factor: 7
Bandwidth:       125 kHz
Coding Rate:     4/5
CRC:             Enabled
```

### Range
- Open field: **1–3 km** typical
- With obstacles: **300–800 m**
- Long range mode (SF=12, BW=62kHz): up to **8–10 km** (slower)

---

## MQTT Cloud Topics

```
Farm ID: dediapada-farm-01  (change in firmware + dashboard)

Sensor data (Hub/Pi → Dashboard):   agrisat/dediapada-farm-01/data
Valve commands (Dashboard → Hub):   agrisat/dediapada-farm-01/cmd
NDVI analysis (Pi → Dashboard):     agrisat/dediapada-farm-01/ndvi
```

---

## Wiring Summary

### Raspberry Pi ↔ LoRa SX1276
| SX1276 Pin | RPi Pin | GPIO |
|------------|---------|------|
| VCC | Pin 17 | 3.3V |
| GND | Pin 20 | GND |
| SCK | Pin 23 | GPIO11 |
| MISO | Pin 21 | GPIO9 |
| MOSI | Pin 19 | GPIO10 |
| NSS/CS | Pin 24 | GPIO8 |
| DIO0 | Pin 22 | GPIO25 |
| RST | Pin 11 | GPIO17 |

### ESP32 (Hub + all Field Nodes) ↔ LoRa SX1276
| SX1276 Pin | ESP32 Pin | GPIO |
|------------|-----------|------|
| VCC | 3.3V | — |
| GND | GND | — |
| SCK | GPIO18 | SPI CLK |
| MISO | GPIO19 | SPI MISO |
| MOSI | GPIO23 | SPI MOSI |
| NSS/CS | GPIO5 | SPI CS |
| DIO0 | GPIO26 | Interrupt |
| RST | GPIO14 | Reset |

### Field Node ↔ Sensors + Valve
| Component | ESP32 Pin | Notes |
|-----------|-----------|-------|
| Soil Moisture (AOUT) | GPIO34 | ADC1, input-only pin |
| DHT22 DATA | GPIO4 | 10kΩ pull-up to 3.3V |
| Relay IN | GPIO25 | Active-LOW (LOW = valve opens) |
| Relay VCC | 5V | Or external 5V |
| Solenoid | Relay COM/NO | 12V supply through relay |
| Gravity Tank | Field | Each zone has its own tank |

---

## Installation & Flashing Order

```
STEP 1 — Flash 3× Field Nodes (change NODE_ZONE each time):
  esp32_field_node.ino  →  NODE_ZONE=1  →  Flash to ESP32 #2
  esp32_field_node.ino  →  NODE_ZONE=2  →  Flash to ESP32 #3
  esp32_field_node.ino  →  NODE_ZONE=3  →  Flash to ESP32 #4

STEP 2 — Flash Hub:
  esp32_hub.ino  →  Set WIFI_SSID + WIFI_PASSWORD + FARM_ID
  Flash to ESP32 #1

STEP 3 — Set up Raspberry Pi:
  pip3 install paho-mqtt pyLoRa Adafruit-DHT
  Edit FARM_ID in brain.py
  python3 hardware/raspberry_pi/brain.py

STEP 4 — Phone App:
  See README below or BUILD_APK.md

STEP 5 — Dashboard connection:
  Click [HW] → CLOUD MQTT tab → enter Farm ID → CONNECT
  OR: LOCAL WiFi tab → enter Hub's IP → CONNECT
```

---

## Phone App (Right Now — No Install Needed)

```
Your Mac IP:  10.249.103.124
Server port:  7432

On your phone (same WiFi as Mac):
  1. Open Chrome / Safari
  2. Go to:  http://10.249.103.124:7432
  3. Chrome: menu → "Add to Home Screen"
     Safari: Share → "Add to Home Screen"
  4. App icon appears on your home screen!
```

---

## Power Budget (per field node)
```
ESP32:           240 mA (active) / 10 µA (deep sleep)
SX1276 LoRa:     120 mA (TX) / 10 mA (RX) / 1 µA (sleep)
Soil sensor:     5 mA
DHT22:           2.5 mA (active)
Relay + solenoid: 70 mA relay coil + solenoid (valve) power separate

For solar-powered nodes:
  18V 10W solar panel + 3.7V 6600mAh LiPo → weeks of autonomy
  Use ESP32 deep sleep between readings to extend battery life
```

---

## Decision Logic (Raspberry Pi Brain)

```
Every 60 seconds:
  FOR each zone (1, 2, 3):
    moisture = latest from field node
    ndvi = today's satellite reading

    dry_threshold = 30%  (base)
    IF ndvi < 0.4 (stressed crop)  → threshold -= 5%
    IF temperature > 35°C          → threshold -= 3%

    IF moisture < dry_threshold AND valve is CLOSED:
      → Send LoRa: CMD:Z{n}:OPEN

    IF moisture > 65% AND valve is OPEN:
      → Send LoRa: CMD:Z{n}:CLOSE
```
