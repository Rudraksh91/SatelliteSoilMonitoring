# TERRA-CORE AgriSat — Full System Architecture
## TERRA-CORE AgriSat, Gujarat

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
| Dashboard | Any browser | Monitoring + automatic irrigation control (manual override optional) | `index.html` |

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
Open `farmer.html` (linked from the dashboard as **Farmers** page (it also opens standalone at `farmer.html`)). A farmer taps the corners of their field on a satellite
map (or drops a pin + enters the area), and the page works out the advice **entirely in the browser** from public data:
Sentinel-2 NDVI (Earth Search STAC + COG windows read with geotiff.js, SCL cloud mask, 10 m edge shrink, validated against
Earth Engine: mean |ΔNDVI| ≈ 0.02–0.03), Open-Meteo weather/forecast, SoilGrids soil → Saxton-Rawls, and the same FAO-56
balance as the daily job. Farm outlines, names and notes stay in the phone's `localStorage` — no account, no server.
- Consent screen before the first farm (what is sent to which service; DPDP Act 2023 note).
- Warnings for fields under 0.2 ha / 0.1 ha; pin + area fallback; optional pump flow (L/h) turns depth into hours.
- "I irrigated" / "It rained" / "Soil is full today" notes correct the balance (stored per farm on the phone).
- Advice only: it does not control valves (that needs the Pi + daily job for a specific farm).

### Radar check for cloudy weeks (Sentinel-1)
Optical Sentinel-2 pictures can be missing for weeks in the monsoon. The farmer app therefore also reads **Sentinel-1 radar**, which
sees through cloud, and shows a **Radar check** card next to the satellite indices (`farmer/core.js`: `radarSeries`, `radarTrend`, `radarBridge`).
- Data: Microsoft Planetary Computer `sentinel-1-rtc` (terrain-corrected gamma0, VV + VH, 10 m), found through its STAC search and read as COG
  windows in the browser with one short-lived read token for the collection. Roughly one pass every 12 days over Dediapada.
- Method: mean VV/VH over the field's pixels (linear power, then dB), using only passes of the **same orbit track** so they are comparable.
  The newest VH value is compared with the field's own earlier passes. A change counts only if it is bigger than a speckle noise band that
  shrinks with the number of pixels averaged (about 2.7 dB for 4 pixels, 0.7 dB from ~100 pixels); fields under 16 pixels are flagged as noisy.
- When the last clear optical picture is 10+ days old (or missing) the card says so and leans on the radar trend.
- It is a **check, not a trigger**: it never changes the irrigation decision, because no radar-to-water relation is calibrated here (see "Honest status").
- Privacy: the approximate field location goes to one more public service. The consent text now names it (consent v2); people who agreed
  under v1 keep everything else working and are asked once, on the radar card, before any radar request is made.
- Not yet archived by the daily GitHub job, and not used by the dashboard's campus pages.

### WhatsApp advice (Gujarati)
- **Share button** in the farmer app (`farmer/share.js`): turns today's advice into a short message in the farmer's language and opens
  WhatsApp (`wa.me`) so the farmer picks the recipient. Nothing is sent from the page; no account or server is involved.
- **Daily message** (`scripts/whatsapp_notify.py`, last step of the daily GitHub job): one Gujarati template message per opted-in number
  through the WhatsApp Business Cloud API, for the campus farm's decision. Dry run unless three secrets are set; never sends a stale
  decision; at most once per date. Setup, the template text and the consent rules are in `scripts/WHATSAPP_SETUP.md`. Not yet run against the live API.
- **Crops:** Cotton, wheat, maize, groundnut, tomato, sugarcane, banana, vegetables or "general" (typical FAO-56 Kcb, depletion
  fraction p, representative root depth, stage lengths). An optional planting date enables a crop-stage Kc curve when satellite
  pictures are stale. These are TYPICAL values — confirm with the faculty / extension officer before relying on them.
- **Languages:** English, हिन्दी, ગુજરાતી (`farmer/i18n.js`, chosen from the browser language, switchable in the header). The Hindi and
  Gujarati texts were written for this project and should be reviewed by a native speaker.
- The first screen asks how the soil feels (wet / moist / dry) instead of assuming a start state; every network step has a deadline.
- Limits: soil values are estimates; no valve control. Tests: `node tests/test_farmer_core.js`, `node tests/test_i18n.js`.

## Active farm — the dashboard follows one location at a time
A selector in the header chooses the **active farm**: `CAMPUS` (daily Earth Engine NDVI, central decision, Pi hardware) or any
farm a farmer outlined on the Farmers page. Selecting a farm (also done automatically when the farmer picks one) re-points the
whole dashboard at that coordinate: Open-Meteo weather/forecast/charts, the global map (zoom + outline), the NDVI map and the
three zone rows (west / centre / east bands of the farm's own Sentinel-2 pixels), and the FAO-56 water model with the farm's soil
(SoilGrids + Saxton-Rawls), crop (Kcb, p, root depth), start state and irrigation/rain notes. A farmer farm is **advisory**: the
central decision, campus NDVI poll and the Pi valves are not used, and valve commands are blocked (emergency stop always passes).
NDVI for farmer farms comes from Sentinel-2 read in the browser (validated against Earth Engine, mean |dNDVI| ~0.02-0.03); Earth
Engine itself cannot be called from a browser without a server-held key.

## Farmer mode (default) and project mode
The site opens in **farmer mode**: there is no campus anywhere — no campus selector option, no campus NDVI/decision/weather fetches, the
Automation / Health / Pipeline pages (campus hardware and project internals) are hidden, and the ESP32 splash is skipped. A farmer sees only
their own farm; with no farm yet the dashboard shows "SELECT YOUR FARM" and opens the Farmers page. The project team switches the campus
view on with `?campus=1` (remembered in that browser) and off with `?campus=0`.

Farmer app map: Esri World Imagery (real to zoom 19, enlarged to 21 for precise corners) with a layer switch — satellite + village/road
names, satellite only, OpenStreetMap streets — and a scale bar. Place search covers India: OpenStreetMap Nominatim (villages, hamlets,
towns, districts; optional state filter; comma-retry for "village district"), Open-Meteo geocoding as fallback, plus pasted coordinates or a
Google Maps link. Note: Nominatim's usage policy allows light, user-initiated searches only (one request per tap) — use a hosted geocoder
if traffic grows.

## Multiple fields
A farmer can save any number of fields (each its own outline, crop, planting date and notes). The Fields list shows a status chip per
field (irrigate / OK / wait for rain / soil check needed) and tick boxes; **Open selected** puts the ticked fields on **one map**
(newest NDVI squares of each, coloured labelled outlines) with a table — area, NDVI, advice, litres — and totals ("2 of 3 fields need
irrigation today — about N litres"). **Save & add another field** keeps the map in place and shows the other fields as white outlines.
Weather is shared by fields in the same ~10 km cell (one request) and cached for 3 h; fields are analysed two at a time. The dashboard's
Analytics NDVI panel has an **ALL MY FIELDS** option that draws every field the same way.

## Autopilot — fully automatic irrigation from NDVI + weather
Everything is satellite or forecast data plus the FAO-56 water balance — no ground sensors (see the "no fake sensors" rule).

**Brain (`farmer/autopilot.js`, pure functions, `node tests/test_autopilot.js`)**
- `plan()` — today's runs for every field: which fields, thirstiest first, start time (default 06:00), minutes (from the pump flow and
  field area), litres and the reason. Rules (each can be switched off): skip when > 10 mm rain is forecast in 48 h; irrigate earlier on
  hot, dry days (ET₀ ≥ 6.5 mm); irrigate earlier when NDVI fell over three clear scenes while the soil is ≥ 70 % used. Limits: 10 min
  minimum, 120 min per run, 240 min per day, 2 min gap so one valve runs at a time. A field with no pump flow gets "set pump flow",
  never an invented time; a field whose soil question is unanswered is never scheduled.
- `outlook()` — each field's next 7 days, stepping the water balance with the forecast.
- `buildSchedule()` — the multi-day plan the controller runs: today from `plan()` (if the planned time has passed, the first run
  starts a few minutes from now), later days from `outlook()`. A day listed with no runs means "do not irrigate that day".

**Farmer page (`farmer.html`, view "Autopilot")** — the ON/OFF switch, today's timeline with reasons, the 7-day outlook, which valve (1–3)
waters which field, the rules, and the controller link (controller ID + command key, kept only in that browser). With autopilot ON, every
time the app opens it re-plans from the newest NDVI and forecast and re-sends the plan. Optional browser notification (only while the app
is open). Turning it ON asks for confirmation, sends `auto_mode: true` and the plan; turning it OFF sends `auto_mode: false` and an empty plan.

**Controller link (`farmer/link.js`, `node tests/test_link.js`)** — signs the command exactly like the dashboard (see *Cloud command security*)
and publishes `{"schedule": {"days": {"YYYY-MM-DD": [{"zone": 1, "start": "06:00", "minutes": 40}, …]}}}` to `agrisat/<id>/cmd`.
The page then waits for the Pi's own status (`schedule` block on `agrisat/<id>/data`) and only says "Controller confirmed" when the Pi
reports it holds the plan.

**Pi (`hardware/raspberry_pi/brain.py`, `python3 tests/test_gateway.py`)** — `schedule` is stored in `~/.agrisat_schedule.json` (survives
restarts) and run **by the Pi itself, with no phone and no open app**, for every day the plan covers:
- It only runs in **auto mode** (still off after every reboot unless `AGRISAT_AUTO_START=1`).
- All safety limits are re-applied on the Pi (10 min minimum, 120 min cap, daily cap, one valve at a time) and the Pi's real-time rain hold
  still blocks any planned run. A run more than 3 h past its planned time (Pi was off) is skipped, not run late.
- For a date that is in the plan, the plan replaces the central `decision.json` (campus pipeline); dates not in the plan fall back to it.
- Emergency stop, manual commands and the watchdog work as before.

**Limits to know**: the plan is computed in the app (that is where the farmer's fields live), so the Pi runs the days it was sent — up to 7.
If the app is not opened for a week the Pi stops getting new plans and runs nothing after the last planned day. A fully server-side
planner (fields stored centrally, planned by a scheduled job) would remove that need, but needs a place to keep farmers' field outlines.

## Multi-index model — NDVI + NDMI + NDRE (from `AgriSat_Multi_Index_Model.pdf`)
The daily FAO-56 soil-water balance stays the backbone; satellite indices adjust it and check it. They never replace it.

| Index | Sentinel-2 bands | Role in AgriSat |
|---|---|---|
| NDVI | (B8 − B4)/(B8 + B4), 10 m | canopy greenness → crop coefficient Kc; the main stress trend while the canopy is young |
| NDMI | (B8 − B11)/(B8 + B11), B11 is 20 m | water in the leaves. An independent **check**: if the newest NDMI is more than 0.06 below the field's own earlier trend (needs ≥ 2 earlier clear scenes) while the model says water is available (Dr < RAW), the app shows **"check this field"**. It never starts or stops watering. |
| NDRE | (B8A − B5)/(B8A + B5), both 20 m | canopy health where NDVI saturates: once NDVI ≥ 0.75 and 3 clear scenes have NDRE, the stress trend is judged on NDRE instead of NDVI |

Field-to-field comparison: with ≥ 3 fields of the same crop, a field whose NDMI is more than 0.12 below the median of the others is flagged.
NDWI (McFeeters, green/NIR) is open-water only and is not used for crop water.

**Farmer app / dashboard** — `farmer/core.js` reads B11, B8A and B5 next to B4/B8/SCL for every clear scene (same cloud mask, B11/B8A/B5 resampled
by nearest neighbour to the 10 m pixel, medians over the same valid pixels). If the extra bands cannot be read the NDVI is still returned and the
indices are left empty — never guessed. `indexTrend()` and `ndmiAnomaly()` are pure helpers; `farmer/autopilot.js` uses `stressCheck()` (NDVI or
NDRE) and adds `flags` to each plan item. Screens: farm view "Satellite indices" card (value, change since the previous picture, meaning, check flag)
and a map switch NDVI | NDMI | NDRE (own colour ramps); dashboard Analytics has the same switch; the Automation decision card shows NDMI, NDRE and the flag.
Old cached NDVI series (without NDMI/NDRE) are re-read once automatically. Tests: `node tests/test_indices.js` (synthetic Sentinel-2 tile), `tests/test_autopilot.js`.

**Campus pipeline** — `scripts/ndvi_pipeline.py` downloads B8/B11/B8A/B5 as a second, optional composite, writes `ndmi`, `ndre`, `ndmi_flag`, `ndmi_drop`
per zone to `ndvi_status.json`, and appends `ndvi_archive/index_history.csv` (the NDMI trend comes from this file; `ndvi_history.csv` keeps its columns).
The Command Center zone panel shows the farm average and ⚠ on a flagged zone. Tests: `python3 tests/test_indices.py`.

**Not done (from the PDF's later phases)**: SAVI for bare early-season soil, Landsat gap-filling, Sentinel-1 radar, OPTRAM, per-crop calibration of Kc = a·NDVI + b,
and validation against gravimetric soil samples. The index thresholds above (0.06, 0.12, 0.75) are starting values and need local calibration.

## Farmer assistant ("Ask" button in the farmer app)
A chat window for farmers' questions, in English, हिन्दी and ગુજરાતી (`farmer/assistant.js`, strings in `farmer/i18n.js` keys `as*`,
UI in `farmer.html`). Tests: `node tests/test_assistant.js`, `node tests/test_chat_proxy.js`.
- **Built-in (default, no server, no key, free):** understands questions about irrigation today, how much water, rain, soil water,
  crop greenness (NDVI), autopilot and how to use the app, and answers **only from the farmer's own field data** that the app has
  already computed (the `advise()` result: decision, litres, depletion, rain/ET0 forecast, NDVI). With a field open it answers for
  that field; on the field list it summarises every field from each field's last result and says when a result is from an earlier day.
  A field that still needs the "how does the soil feel" answer never gets a litres figure. It invents no numbers. It also has short,
  general answers for eight common farming topics (sowing seasons, soil health, compost, fertiliser, saving water, pests, crop
  rotation, seeds) with no made-up figures, so general questions get an answer even with no server. Anything else gets a pointer to
  the local agriculture officer / Kisan Call Centre (1800-180-1551).
- **Optional AI for any agriculture question (off by default):** `chat-proxy/` is a small Cloudflare Worker that holds the AI API key and
  calls **Google Gemini** by default (every question that is not about the farmer's own field goes there when it is set up, so the farmer can ask about any agriculture topic;
  the system prompt in `chat-proxy/lib.mjs` allows any agriculture question and keeps the honesty rules: no invented field data, no invented doses) (`worker.mjs`, model `gemini-3.5-flash-lite`, change with `GEMINI_MODEL`) or Claude
  (`worker-claude.mjs`, `CHAT_MODEL`). `chat-proxy/README.md` has setup, cost and abuse notes. It is switched on by putting the
  Worker URL in `farmer/chat-config.js`, together with the provider name shown to farmers (`AGRISAT_CHAT_NAME`). The farmer must tap
  **Yes** on a consent message that names the service once per session; the page then sends only the question, the language, up to
  6 earlier AI turns and an anonymous field summary (`AgriAssistant.llmContext`: crop, area, decision, litres, soil-water numbers,
  3-day rain/ET0, NDVI; **no name, no coordinates**). Replies are labelled "AI answer · <service>" with a reminder that they can be
  wrong. The API key never appears in the repo or the page.
- Not covered: the Worker has no per-user rate limit of its own (add a Cloudflare rate-limiting rule and an Anthropic spend limit);
  the Hindi and Gujarati wording needs review by a native speaker; the AI path (Gemini and Claude) has been tested only against mocked
  servers, not against the live APIs; Gemini model IDs change, so check `GEMINI_MODEL` before deploying.

## Control philosophy: automatic first, manual optional
Automatic irrigation is the primary mode of the system and manual control is an optional override, not the other way round.
- **Dashboard:** `AUTO MODE` is the default and is labelled PRIMARY; `MANUAL OVERRIDE` is labelled OPTIONAL, its banner says automatic
  irrigation is only paused and offers **RESUME AUTO**. In the Pump control sheet `AUTO IRRIGATION` is the main button; `FORCE ON` /
  `FORCE OFF` sit under "MANUAL OVERRIDE · OPTIONAL". Rain guard, run-time limits and the emergency stop apply in both modes.
- **Farmer app:** the Autopilot card is the first card and says "NOT ON YET — tap to turn on automatic irrigation"; switching it on
  still asks for confirmation because it makes real pumps run.
- **Open decision:** the Pi gateway still **starts in MANUAL after a restart or power cut** (`AGRISAT_AUTO_START=0`, a safety default
  from the start-up safety work). With that default an automatic plan stops after a power cut until auto mode is switched on again,
  which works against "automatic first". Options: keep it, set `AGRISAT_AUTO_START=1` on gateways that should resume by themselves,
  or make the gateway remember the last mode the farmer chose. The calibration gate and hard run-time limits apply either way.
