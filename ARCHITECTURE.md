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

## Cloud command security (MQTT)

The dashboard and the Raspberry Pi talk over the **public** HiveMQ broker, so the broker cannot be
trusted to keep strangers out. Valve commands are therefore authenticated at the application layer:

- Every command published to `agrisat/<farm>/cmd` is `{"m": "<inner JSON>", "sig": "<hex HMAC-SHA256>"}`.
  The inner JSON holds the command plus `ts` (ms) and a random `nonce`.
- `brain.py` accepts a command only if the signature matches, `ts` is within 2 minutes of now, and the
  nonce was not seen before. With no key configured it refuses everything (fail closed).
- The shared secret is `AGRISAT_CMD_KEY` on the Pi (`openssl rand -hex 32`) and is typed once into the
  dashboard's **CMD KEY** button (kept in that browser's localStorage only — never in the repo).
- Not covered: the `.../data` topic is still readable by anyone, and `hardware/esp32/esp32_agrisat_cloud.ino`
  still acts on **unsigned** commands. Do not deploy that firmware against the public broker as-is.

## Decision architecture (follows the project's Solutions Report / Architecture Blueprint)

```
Sentinel-2 (Earth Engine) + Open-Meteo + farmer / gateway-confirmed events
        │   GitHub Actions, daily 07:00 IST  (.github/workflows/ndvi.yml  <- scripts/workflow_agrisat_daily.yml)
        ▼
scripts/run_daily.py  +  scripts/agrisat_model.py      settings: config/farm_config.json
   NDVI -> Kc (stage-curve fallback after 20 days) -> FAO-56 depletion per zone -> rain-skip / RAW rule -> run time
        ▼
data/decision.json (48 h validity)  data/state.json (depletion, NDVI history)  data/log.csv (append-only daily log)
        ▼ HTTP pull                                    ▲ data/confirmed_runs.json (verified minutes, from the Pi)
Pi gateway (hardware/raspberry_pi/brain.py): executes zones one at a time, re-applies run-time limits,
stops model decisions when the file is > 48 h old, closes on node loss, E-stop, reports only CONFIRMED flow
```

- **One brain:** only the daily job decides. The dashboard shows `decision.json`; its in-browser balance is a
  fallback used when that file is missing or older than 48 h. The Pi makes no hydrological decisions.
- **Calibration gate:** `decision.json` says `calibrated:false` while `config/farm_config.json` still holds
  estimates (soil θFC/θWP, crop Kcb/p, start anchor, zone area/flow). The Pi refuses to run valves on an
  uncalibrated decision (`AGRISAT_REQUIRE_CALIBRATED=0` overrides — not recommended).
- **Not built yet:** Telegram needs `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` secrets; the "I irrigated / it rained"
  form needs a published CSV URL in `inputs.farmer_events_csv_url`; satellite soil-moisture check (M7),
  soil-evaporation term (M10), 4th zone and 865 MHz radio change are not implemented.

## Pi start-up safety and configuration (`brain.py`)

- **Starts in MANUAL mode** after every start or reboot. Automatic irrigation begins only when auto mode is
  switched on from the dashboard (signed command) or `AGRISAT_AUTO_START=1` is set.
- `AGRISAT_START_HOUR` (default 8, IST): earliest hour the day's run may start (the job publishes ~07:00).
- `AGRISAT_STALE_DEFAULT_MIN` (default 0): minutes per zone per day when the decision file is > 48 h old.
- `AGRISAT_GITHUB_TOKEN`: lets the Pi publish confirmed runs to `data/confirmed_runs.json` (fine-grained token,
  contents:write on this repo only). Without it confirmed runs are NOT fed back into the balance.
- `AGRISAT_CMD_KEY`: shared secret that authenticates dashboard commands (see above).
- Limits enforced at the gateway regardless of the JSON: 10 min minimum, 120 min per run, 240 min per zone per day.
- Tests: `python3 tests/test_agrisat_model.py`, `python3 tests/test_gateway.py`, `node tests/test_fao56.js`.

## Installing the Pi gateway
`hardware/raspberry_pi/install_agrisat_pi.sh` is a self-extracting one-file installer (`sudo bash install_agrisat_pi.sh`):
packages, Python environment, systemd service, nightly self-update with rollback, random command key and the `agrisat`
helper command. See `hardware/raspberry_pi/INSTALL.md`. Rebuild with `python3 scripts/build_pi_installer.py`.

## Farmer app (`farmer.html` + `farmer/core.js`) — any farmer, on their own phone
Open `farmer.html` (linked from the dashboard as **FOR FARMERS**). A farmer taps the corners of their field on a satellite
map (or drops a pin + enters the area), and the page works out the advice **entirely in the browser** from public data:
Sentinel-2 NDVI (Earth Search STAC + COG windows read with geotiff.js, SCL cloud mask, 10 m edge shrink, validated against
Earth Engine: mean |ΔNDVI| ≈ 0.02–0.03), Open-Meteo weather/forecast, SoilGrids soil → Saxton-Rawls, and the same FAO-56
balance as the daily job. Farm outlines, names and notes stay in the phone's `localStorage` — no account, no server.
- Consent screen before the first farm (what is sent to which service; DPDP Act 2023 note).
- Warnings for fields under 0.2 ha / 0.1 ha; pin + area fallback; optional pump flow (L/h) turns depth into hours.
- "I irrigated" / "It rained" / "Soil is full today" notes correct the balance (stored per farm on the phone).
- Advice only: it does not control valves (that needs the Pi + daily job for a specific farm).
- **Crops:** Cotton, wheat, maize, groundnut, tomato, sugarcane, banana, vegetables or "general" (typical FAO-56 Kcb, depletion
  fraction p, representative root depth, stage lengths). An optional planting date enables a crop-stage Kc curve when satellite
  pictures are stale. These are TYPICAL values — confirm with the faculty / extension officer before relying on them.
- **Languages:** English, हिन्दी, ગુજરાતી (`farmer/i18n.js`, chosen from the browser language, switchable in the header). The Hindi and
  Gujarati texts were written for this project and should be reviewed by a native speaker.
- The first screen asks how the soil feels (wet / moist / dry) instead of assuming a start state; every network step has a deadline.
- Limits: soil values are estimates; no valve control. Tests: `node tests/test_farmer_core.js`, `node tests/test_i18n.js`.
