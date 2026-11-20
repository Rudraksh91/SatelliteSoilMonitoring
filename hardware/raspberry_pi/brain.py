#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════════════
TERRA-CORE AgriSat — Raspberry Pi Brain
══════════════════════════════════════════════════════════════════════

This is the central intelligence of the irrigation system.

Responsibilities:
  1. Load satellite NDVI / soil data (from ndvi_harvester output files)
  2. Read local weather sensors (DHT22 or BME280)
  3. Run the irrigation decision engine (when/how long to irrigate)
  4. Send valve commands to the Main ESP32 Hub via LoRa (SX1276 / RA-02)
  5. Push all data to the TERRA-CORE dashboard via MQTT (cloud access)
  6. Log everything to disk for audit

Hardware needed on Pi:
  - SX1276 / RA-02 LoRa module on SPI0 (pins below)
  - DHT22 sensor on GPIO4 (optional — ESP32 nodes also have sensors)
  - MicroSD already on Pi (for logging)

LoRa wiring (Raspberry Pi 40-pin header):
  LoRa VCC  → Pin 17 (3.3V)
  LoRa GND  → Pin 20 (GND)
  LoRa SCK  → Pin 23 (GPIO11 / SPI0_SCLK)
  LoRa MISO → Pin 21 (GPIO9  / SPI0_MISO)
  LoRa MOSI → Pin 19 (GPIO10 / SPI0_MOSI)
  LoRa NSS  → Pin 24 (GPIO8  / SPI0_CE0)
  LoRa DIO0 → Pin 22 (GPIO25)
  LoRa RST  → Pin 11 (GPIO17)

pip install:
  pip3 install paho-mqtt RPi.GPIO spidev pyLoRa Adafruit-DHT

Run:
  python3 brain.py
  # Or as a service: sudo systemctl enable agrisat-brain
══════════════════════════════════════════════════════════════════════
"""

import csv
import json
import time
import logging
from datetime import datetime, date
from pathlib import Path

# ── Third-party (install with pip3) ──────────────────────────────────
try:
    import paho.mqtt.client as mqtt_client
    HAS_MQTT = True
except ImportError:
    HAS_MQTT = False
    print("[WARN] paho-mqtt not installed — cloud dashboard disabled")

try:
    import Adafruit_DHT
    HAS_DHT = True
except ImportError:
    HAS_DHT = False
    print("[WARN] Adafruit_DHT not installed — using simulated weather")

try:
    from SX127x.LoRa import *
    from SX127x.board_config import BOARD
    HAS_LORA = True
except ImportError:
    HAS_LORA = False
    print("[WARN] SX127x not installed — LoRa disabled (install: pip3 install pyLoRa)")

# ── Physical test valve (direct Pi GPIO, no LoRa field node yet) ──────
# One relay+solenoid wired straight to this Pi for hardware bring-up —
# see hardware/raspberry_pi/test_valve.py for the standalone CLI, and
# app.py's matching set_physical_valve() for the same pin/polarity.
#
# NOTE: the actual GPIO.setup() claim happens in init_valve_gpio(), called
# from main() AFTER init_lora(). SX127x's BOARD.setup() calls
# GPIO.setmode(GPIO.BCM) again for its own pins — doing that a second time
# resets lgpio's internal chip handle and silently drops any claim made
# before it, which is exactly what happened when this pin was claimed here
# at import time (confirmed via /sys/kernel/debug/gpio: GPIO23 lost its
# claim the moment init_lora() ran, while LoRa's own pins stayed claimed).
VALVE_GPIO_PIN = 23
try:
    import RPi.GPIO as GPIO
    HAS_GPIO = True
except ImportError:
    HAS_GPIO = False
    print("[WARN] RPi.GPIO not available — physical test valve disabled")

def init_valve_gpio():
    if not HAS_GPIO:
        return
    try:
        GPIO.setmode(GPIO.BCM)
        GPIO.setwarnings(False)
        GPIO.setup(VALVE_GPIO_PIN, GPIO.OUT, initial=GPIO.LOW)  # relay is ACTIVE-HIGH: LOW = relay off = valve closed
        log.info(f"[Valve] GPIO{VALVE_GPIO_PIN} claimed for physical test valve")
    except Exception as e:
        log.error(f"[Valve] GPIO init failed: {e}")

physical_valve_open = False   # tracked separately from node_data — see publish_dashboard()

def set_physical_valve(open_: bool):
    global physical_valve_open
    physical_valve_open = open_
    if HAS_GPIO:
        GPIO.output(VALVE_GPIO_PIN, GPIO.HIGH if open_ else GPIO.LOW)

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

# ── Farm identity (must match dashboard + ESP32 cloud firmware) ──────
FARM_ID   = "dediapada-farm-01"

# ── MQTT broker ──────────────────────────────────────────────────────
MQTT_HOST  = "broker.hivemq.com"
MQTT_PORT  = 1883
TOPIC_DATA = f"agrisat/{FARM_ID}/data"
TOPIC_CMD  = f"agrisat/{FARM_ID}/cmd"     # listen for manual dashboard overrides
TOPIC_NDVI = f"agrisat/{FARM_ID}/ndvi"   # push NDVI analysis

# ── DHT22 sensor pin ─────────────────────────────────────────────────
DHT_PIN  = 4   # GPIO4 on Pi
DHT_TYPE = Adafruit_DHT.DHT22 if HAS_DHT else None

# ── Irrigation decision thresholds ───────────────────────────────────
MOISTURE_DRY      = 30.0    # % — start irrigation below this
MOISTURE_ADEQUATE = 65.0    # % — stop irrigation above this
MIN_IRRIGATE_SECS = 300     # minimum run time: 5 minutes
MAX_IRRIGATE_SECS = 3600    # safety cap per run: 1 hour (enforced by enforce_valve_limits watchdog)
MAX_DAILY_SECS    = 7200    # safety cap per zone per day: 2 hours (blocks further AUTO opens)
NODE_STALE_SECS   = 120     # fail-safe: close a zone whose field node has been silent this long (H2)
CONFIRM_WAIT_SECS = 60      # a valve command must be confirmed by the node's status within this (H3)

# ── Zone definitions (3 fields, 3 gravity tanks) ─────────────────────
# node_id maps to the LoRa node address in esp32_field_node.ino
ZONES = [
    {"id": 1, "node_id": 0x11, "name": "FIELD-A", "area_ha": 0.5},
    {"id": 2, "node_id": 0x12, "name": "FIELD-B", "area_ha": 0.8},
    {"id": 3, "node_id": 0x13, "name": "FIELD-C", "area_ha": 0.6},
]

# ── NDVI data source (from ndvi_harvester.py output) ─────────────────
NDVI_DATA_DIR  = Path.home() / "agrisat_ndvi_data"
NDVI_THRESHOLD = 0.4   # below this NDVI = stressed crop = irrigate sooner

# ── Logging ──────────────────────────────────────────────────────────
LOG_DIR = Path("/var/log/agrisat")
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "brain.log"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger("brain")

# ══════════════════════════════════════════════════════════════════════
# GLOBAL STATE
# ══════════════════════════════════════════════════════════════════════

# Live sensor data from field nodes (received via LoRa)
node_data = {
    0x11: {"moisture": 0, "valve": False, "temp": 0, "humidity": 0, "last_seen": 0},
    0x12: {"moisture": 0, "valve": False, "temp": 0, "humidity": 0, "last_seen": 0},
    0x13: {"moisture": 0, "valve": False, "temp": 0, "humidity": 0, "last_seen": 0},
}

# Local weather from Pi DHT22
weather = {"temp": None, "humidity": None}   # None = no sensor reading yet

# NDVI state
ndvi_state = {"ndvi_avg": 0.5, "date": "", "stressed": False}

# Auto mode flag (can be toggled from dashboard)
auto_mode = True

# MQTT client
mqtt_conn = None

# LoRa instance
lora_radio = None

# ══════════════════════════════════════════════════════════════════════
# LoRa COMMUNICATION
# ══════════════════════════════════════════════════════════════════════

HUB_ADDR = 0x01   # Main ESP32 Hub address

def lora_send(payload_str: str):
    """Send a LoRa message to the hub. Hub relays to field nodes."""
    if not HAS_LORA or lora_radio is None:
        log.debug(f"[LoRa SIM] → {payload_str}")
        return
    try:
        data = [ord(c) for c in payload_str]
        lora_radio.write_payload(data)
        lora_radio.set_mode(MODE.TX)
        time.sleep(0.1)
        log.info(f"[LoRa] SENT: {payload_str}")
    except Exception as e:
        log.error(f"[LoRa] Send error: {e}")

def build_valve_cmd(zone_id: int, state: bool) -> str:
    """
    LoRa command format sent to hub:
      CMD:Z{zone_id}:{OPEN|CLOSE}
    Hub then relays to the correct field node by zone.
    """
    return f"CMD:Z{zone_id}:{'OPEN' if state else 'CLOSE'}"

def build_stop_cmd() -> str:
    return "CMD:ALL:STOP"

# ══════════════════════════════════════════════════════════════════════
# VALVE SAFETY LAYER — every open/close goes through command_valve()
#   • MAX_IRRIGATE_SECS : watchdog force-closes any run longer than this
#   • MIN_IRRIGATE_SECS : AUTO logic may not close a valve before this
#   • MAX_DAILY_SECS    : AUTO logic may not open a zone past its daily total
#   • Manual close / emergency stop are ALWAYS allowed (farmer has priority)
# ══════════════════════════════════════════════════════════════════════

valve_open_since = {}   # zone_id -> epoch when it was opened
daily_run_secs   = {}   # zone_id -> seconds run today
_daily_date      = datetime.now().date()

def _roll_daily_counters():
    global _daily_date
    today = datetime.now().date()
    if today != _daily_date:
        daily_run_secs.clear()
        _daily_date = today

def _record_close(zone_id):
    t0 = valve_open_since.pop(zone_id, None)
    if t0 is not None:
        daily_run_secs[zone_id] = daily_run_secs.get(zone_id, 0) + (time.time() - t0)

pending_confirm  = {}   # zone_id -> (expected_state, sent_at) until the node reports it (H3)
EVENT_LOG_FILE   = LOG_DIR / "irrigation_log.csv"   # V2: one row per valve event / watchdog action

def log_event(event: str, zone_id=None, source: str = "", detail: str = ""):
    """Append one row to irrigation_log.csv (never raises — logging must not stop irrigation)."""
    try:
        new_file = not EVENT_LOG_FILE.exists()
        with open(EVENT_LOG_FILE, "a", newline="") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["timestamp", "event", "zone", "source", "zone_secs_today",
                            "ndvi_avg", "temp_c", "auto_mode", "detail"])
            w.writerow([datetime.now().isoformat(timespec="seconds"), event, zone_id, source,
                        round(daily_run_secs.get(zone_id, 0)) if zone_id is not None else "",
                        ndvi_state.get("ndvi_avg"), weather.get("temp"), auto_mode, detail])
    except Exception as e:
        log.debug(f"[Log] event log write failed: {e}")

def _node_for_zone(zone_id):
    for z in ZONES:
        if z["id"] == zone_id:
            return z["node_id"]
    return None

def command_valve(zone_id, state: bool, source: str = "auto") -> bool:
    """Single entry point for valve commands. source: auto | manual | watchdog.
    Returns True if the command was sent, False if blocked by a safety rule."""
    _roll_daily_counters()
    try:
        zone_id = int(zone_id)   # dashboard may send "1" or 1 — keep dict keys consistent
    except (TypeError, ValueError):
        log.error(f"[Safety] Invalid zone id {zone_id!r} — command ignored")
        return False
    now = time.time()
    if state:
        if source == "auto" and daily_run_secs.get(zone_id, 0) >= MAX_DAILY_SECS:
            log.warning(f"[Safety] Zone {zone_id}: daily cap {MAX_DAILY_SECS}s reached — AUTO open blocked")
            log_event("BLOCKED_DAILY_CAP", zone_id, source)
            return False
        valve_open_since.setdefault(zone_id, now)
    else:
        t0 = valve_open_since.get(zone_id)
        if source == "auto" and t0 is not None and now - t0 < MIN_IRRIGATE_SECS:
            log.info(f"[Safety] Zone {zone_id}: min run {MIN_IRRIGATE_SECS}s not reached — AUTO close deferred")
            return False
        _record_close(zone_id)
    lora_send(build_valve_cmd(zone_id, state))
    pending_confirm[zone_id] = (state, now)
    log_event("OPEN" if state else "CLOSE", zone_id, source)
    # One physical relay stands in for all zones during bring-up: keep it open
    # while any zone is open, close it only when none are.
    set_physical_valve(bool(valve_open_since))
    return True

def stop_all_valves():
    for zid in list(valve_open_since):
        _record_close(zid)
    lora_send(build_stop_cmd())
    set_physical_valve(False)
    pending_confirm.clear()
    log_event("EMERGENCY_STOP", None, "manual")

def enforce_valve_limits():
    """Watchdog (runs every loop tick):
       • force-close any run past MAX_IRRIGATE_SECS
       • fail-safe: close a zone whose field node went silent for NODE_STALE_SECS (H2)
       • flag valve commands the node never confirmed within CONFIRM_WAIT_SECS (H3)"""
    now = time.time()
    for zid, t0 in list(valve_open_since.items()):
        addr = _node_for_zone(zid)
        nd = node_data.get(addr)
        # last_seen == 0 means no field node has ever reported (bring-up with the single Pi relay)
        if nd and nd["last_seen"] > 0 and now - nd["last_seen"] > NODE_STALE_SECS:
            log.warning(f"[Safety] Zone {zid} node silent {now - nd['last_seen']:.0f}s — FAIL-SAFE CLOSE")
            command_valve(zid, False, "watchdog")
            log_event("FAILSAFE_STALE_NODE", zid, "watchdog")
    for zid, (want, sent) in list(pending_confirm.items()):
        nd = node_data.get(_node_for_zone(zid))
        if not nd or nd["last_seen"] == 0:
            pending_confirm.pop(zid, None)          # no node to confirm with (single-relay bring-up)
        elif nd["last_seen"] > sent and nd["valve"] == want:
            pending_confirm.pop(zid, None)          # confirmed
        elif now - sent > CONFIRM_WAIT_SECS:
            log.error(f"[Safety] Zone {zid}: {'OPEN' if want else 'CLOSE'} NOT CONFIRMED by node after {CONFIRM_WAIT_SECS}s")
            log_event("UNCONFIRMED", zid, "watchdog", f"wanted {'OPEN' if want else 'CLOSE'}")
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({
                    "ai_action": f"ZONE_{zid}_UNCONFIRMED",
                    "reason": f"node did not confirm {'OPEN' if want else 'CLOSE'} within {CONFIRM_WAIT_SECS}s",
                }))
            pending_confirm.pop(zid, None)
    for zid, t0 in list(valve_open_since.items()):
        if now - t0 > MAX_IRRIGATE_SECS:
            log.warning(f"[Safety] Zone {zid} open {now - t0:.0f}s > {MAX_IRRIGATE_SECS}s — FORCE CLOSE")
            command_valve(zid, False, "watchdog")
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({
                    "ai_action": f"ZONE_{zid}_CLOSE",
                    "reason": f"max run time {MAX_IRRIGATE_SECS}s exceeded (watchdog)",
                }))

def parse_lora_status(msg: str):
    """
    Parse status message from field node (relayed through hub):
      STS:Z{id}:M:{moisture}:V:{0|1}:T:{temp}:H:{hum}
    Example: STS:Z1:M:45.2:V:0:T:28.4:H:63.1
    """
    try:
        parts = msg.split(":")
        if parts[0] != "STS":
            return
        zone_id = int(parts[1][1:])   # Z1 → 1
        node_addr = 0x10 + zone_id    # 1 → 0x11
        if node_addr not in node_data:
            return
        d = {}
        for i in range(2, len(parts) - 1, 2):
            key, val = parts[i], parts[i+1]
            if key == "M":   d["moisture"] = float(val)
            elif key == "V": d["valve"]    = val == "1"
            elif key == "T": d["temp"]     = float(val)
            elif key == "H": d["humidity"] = float(val)
        d["last_seen"] = time.time()
        node_data[node_addr].update(d)
        log.debug(f"[LoRa] Node Z{zone_id}: {d}")
    except Exception as e:
        log.error(f"[LoRa] Parse error '{msg}': {e}")

class LoRaReceiver(LoRa if HAS_LORA else object):
    def __init__(self):
        if HAS_LORA:
            super().__init__(verbose=False)
        self.set_mode(MODE.RXCONT if HAS_LORA else None)

    def on_rx_done(self):
        if not HAS_LORA:
            return
        payload = self.read_payload(nocheck=True)
        msg = "".join([chr(b) for b in payload])
        log.info(f"[LoRa] RECV: {msg}")
        parse_lora_status(msg)

def init_lora():
    global lora_radio
    if not HAS_LORA:
        log.warning("[LoRa] Library not available — running in simulation mode")
        return
    try:
        BOARD.setup()
        lora_radio = LoRaReceiver()
        lora_radio.set_freq(433.0)       # 433 MHz (change to 868.0 for Europe)
        lora_radio.set_spreading_factor(7)
        lora_radio.set_bw(Bw.BW_125KHZ)
        lora_radio.set_coding_rate(CodingRate.CR4_5)
        lora_radio.set_pa_config(pa_select=1)
        lora_radio.set_rx_crc(True)
        log.info("[LoRa] Initialised on 433 MHz SF7 BW125")
    except Exception as e:
        log.error(f"[LoRa] Init failed: {e}")
        lora_radio = None

# ══════════════════════════════════════════════════════════════════════
# WEATHER SENSOR (DHT22 on Pi GPIO4)
# ══════════════════════════════════════════════════════════════════════

def read_weather():
    global weather
    if not HAS_DHT:
        # No DHT22 present: report "no reading" instead of inventing one. A simulated value
        # here used to be published as if it were the real sensor and overrode the live
        # Open-Meteo temperature on the dashboard.
        weather = {"temp": None, "humidity": None}
        return
    try:
        h, t = Adafruit_DHT.read_retry(DHT_TYPE, DHT_PIN)
        if t is not None and h is not None:
            weather = {"temp": round(t, 1), "humidity": round(h, 1)}
    except Exception as e:
        log.warning(f"[DHT22] Read error: {e}")

# ══════════════════════════════════════════════════════════════════════
# NDVI DATA LOADER (reads output from ndvi_harvester.py)
# ══════════════════════════════════════════════════════════════════════

NDVI_STATUS_URL = "https://raw.githubusercontent.com/rudraksh91/SatelliteSoilMonitoring/main/ndvi_status.json"
NDVI_MAX_AGE_DAYS = 20   # older imagery is ignored (no "stressed" flag from stale data)

def load_ndvi():
    """
    Reads ndvi_status.json, produced every 2 days by the GitHub Actions NDVI pipeline
    (scripts/ndvi_pipeline.py). ndvi_avg is the mean of the zone NDVIs. If the file cannot be
    fetched, or its scene is older than NDVI_MAX_AGE_DAYS, the previous state is kept and the
    crop is NOT flagged stressed (a missing signal must not lower irrigation thresholds).
    """
    import urllib.request
    try:
        with urllib.request.urlopen(NDVI_STATUS_URL, timeout=15) as r:
            data = json.load(r)
        zones = [z["ndvi"] for z in data.get("zones", []) if isinstance(z.get("ndvi"), (int, float))]
        if not zones:
            raise ValueError("no zone NDVI values in ndvi_status.json")
        scene = data.get("scene_date", "")
        age = (date.today() - date.fromisoformat(scene)).days if scene else 999
        ndvi_state["ndvi_avg"] = sum(zones) / len(zones)
        ndvi_state["date"]     = scene
        ndvi_state["stressed"] = age <= NDVI_MAX_AGE_DAYS and ndvi_state["ndvi_avg"] < NDVI_THRESHOLD
        log.info(f"[NDVI] scene {scene} ({age} d old): avg={ndvi_state['ndvi_avg']:.3f}, stressed={ndvi_state['stressed']}")
    except Exception as e:
        ndvi_state["stressed"] = False
        log.warning(f"[NDVI] load failed ({e}) — NDVI not used for decisions")

# ══════════════════════════════════════════════════════════════════════
# IRRIGATION DECISION ENGINE
# ══════════════════════════════════════════════════════════════════════

def decide_irrigation():
    """
    Core logic: check each zone and decide open/close.

    Rules (in priority order):
      1. Manual override from dashboard → respect it (auto_mode=False)
      2. Moisture < MOISTURE_DRY → open valve
      3. Moisture > MOISTURE_ADEQUATE → close valve
      4. NDVI stressed crop → lower the DRY threshold by 5% (irrigate sooner)
      5. High temperature (>35°C) → lower DRY threshold by 3%
    """
    if not auto_mode:
        return

    # Adaptive thresholds
    dry_threshold = MOISTURE_DRY
    if ndvi_state["stressed"]:
        dry_threshold -= 5.0   # stressed crops need more water
        log.debug(f"[AI] NDVI stressed → dry threshold lowered to {dry_threshold}%")
    if weather["temp"] is not None and weather["temp"] > 35.0:
        dry_threshold -= 3.0   # hot day → irrigate sooner
        log.debug(f"[AI] Hot day {weather['temp']}°C → dry threshold lowered to {dry_threshold}%")
    dry_threshold = max(15.0, dry_threshold)   # never below 15%

    now = time.time()
    for zone in ZONES:
        zid  = zone["id"]
        addr = zone["node_id"]
        nd   = node_data[addr]

        # Skip if node hasn't reported recently (offline > 30s)
        if now - nd["last_seen"] > 30:
            log.debug(f"[AI] Zone {zid} ({zone['name']}) — node offline, skipping")
            continue

        m = nd["moisture"]
        v = nd["valve"]

        if not v and m < dry_threshold:
            log.info(f"[AI] Zone {zid} {zone['name']}: moisture {m:.1f}% < {dry_threshold:.1f}% → OPEN valve")
            if not command_valve(zid, True, "auto"):
                continue
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({
                    "ai_action": f"ZONE_{zid}_OPEN",
                    "reason": f"moisture {m:.1f}% below threshold {dry_threshold:.1f}%",
                    "ndvi_stressed": ndvi_state["stressed"],
                }))

        elif v and m > MOISTURE_ADEQUATE:
            log.info(f"[AI] Zone {zid} {zone['name']}: moisture {m:.1f}% > {MOISTURE_ADEQUATE}% → CLOSE valve")
            if not command_valve(zid, False, "auto"):
                continue
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({
                    "ai_action": f"ZONE_{zid}_CLOSE",
                    "reason": f"moisture {m:.1f}% above {MOISTURE_ADEQUATE}%",
                }))

# ══════════════════════════════════════════════════════════════════════
# MQTT — Dashboard cloud bridge
# ══════════════════════════════════════════════════════════════════════

def mqtt_on_connect(client, userdata, flags, rc):
    if rc == 0:
        log.info(f"[MQTT] Connected to {MQTT_HOST}")
        client.subscribe(TOPIC_CMD)
    else:
        log.warning(f"[MQTT] Connect failed rc={rc}")

def mqtt_on_message(client, userdata, msg):
    """
    Handle manual valve commands from the dashboard.
    When user taps a valve on the dashboard, we receive it here
    and forward to the hub via LoRa.
    """
    global auto_mode
    try:
        d = json.loads(msg.payload)
        if d.get("emergency_stop"):
            log.info("[MQTT] Dashboard: EMERGENCY STOP")
            stop_all_valves()
            return
        if "auto_mode" in d:
            auto_mode = d["auto_mode"]
            log.info(f"[MQTT] Dashboard: auto_mode = {auto_mode}")
            return
        zone = d.get("zone")
        state = d.get("state")
        if zone and state is not None:
            log.info(f"[MQTT] Dashboard: Zone {zone} → {'OPEN' if state else 'CLOSE'}")
            # Manual commands always win over auto logic (no min-run / daily-cap
            # blocking), but opens are still bounded by the MAX_IRRIGATE_SECS
            # watchdog. Only one physical relay exists right now, so
            # command_valve() drives it for whichever zone was commanded.
            command_valve(zone, bool(state), "manual")
    except Exception as e:
        log.error(f"[MQTT] Message error: {e}")

def init_mqtt():
    global mqtt_conn
    if not HAS_MQTT:
        return
    try:
        cid = f"pi-brain-{FARM_ID}"
        mqtt_conn = mqtt_client.Client(client_id=cid)
        mqtt_conn.on_connect = mqtt_on_connect
        mqtt_conn.on_message = mqtt_on_message
        mqtt_conn.reconnect_delay_set(5, 60)
        mqtt_conn.connect_async(MQTT_HOST, MQTT_PORT, keepalive=60)
        mqtt_conn.loop_start()
        log.info(f"[MQTT] Connecting to {MQTT_HOST}…")
    except Exception as e:
        log.error(f"[MQTT] Init failed: {e}")
        mqtt_conn = None

def publish_dashboard():
    """Push full system state to MQTT → dashboard updates in real time."""
    if not mqtt_conn:
        return
    try:
        payload = {
            "source":      "pi_brain",
            "farm":        FARM_ID,
            "timestamp":   datetime.now().isoformat(),
            "weather":     weather,
            "ndvi":        ndvi_state,
            "auto_mode":   auto_mode,
            "zones": [
                {
                    "id":       z["id"],
                    "name":     z["name"],
                    "moisture": node_data[z["node_id"]]["moisture"],
                    # No real per-zone LoRa field nodes yet — the one physical
                    # relay wired to this Pi stands in for whichever zone is
                    # commanded, so report its real state here rather than
                    # node_data's (always-False, no hardware) value. Otherwise
                    # the dashboard's state-sync logic force-closes the valve
                    # again a few seconds after every real OPEN command.
                    "valve":    physical_valve_open,
                    "temp":     node_data[z["node_id"]]["temp"],
                    "online":   (time.time() - node_data[z["node_id"]]["last_seen"]) < 30,
                }
                for z in ZONES
            ],
            # Flat fields for dashboard compatibility
            "moisture":    [node_data[z["node_id"]]["moisture"] for z in ZONES],
            "temperature": weather["temp"],
            "humidity":    weather["humidity"],
            "valve":       [physical_valve_open for z in ZONES],
            "pico_online": all(
                (time.time() - node_data[z["node_id"]]["last_seen"]) < 30
                for z in ZONES
            ),
        }
        mqtt_conn.publish(TOPIC_DATA, json.dumps(payload))
    except Exception as e:
        log.error(f"[MQTT] Publish error: {e}")

# ══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════

def main():
    log.info("══════════════════════════════════════════")
    log.info("  TERRA-CORE Pi Brain starting…")
    log.info(f"  Farm ID: {FARM_ID}")
    log.info("══════════════════════════════════════════")

    init_lora()
    init_valve_gpio()
    init_mqtt()

    last_ndvi_load    = 0
    last_weather_read = 0
    last_decision     = 0
    last_publish      = 0

    while True:
        now = time.time()

        # Read weather every 30s
        if now - last_weather_read > 30:
            read_weather()
            last_weather_read = now

        # Load NDVI once per hour (harvester updates once per 2 days)
        if now - last_ndvi_load > 3600:
            load_ndvi()
            last_ndvi_load = now

        # Run irrigation decisions every 60s
        if now - last_decision > 60:
            decide_irrigation()
            last_decision = now

        # Safety watchdog — force-close runs past MAX_IRRIGATE_SECS
        enforce_valve_limits()

        # Push to dashboard every 3s
        if now - last_publish > 3:
            publish_dashboard()
            last_publish = now

        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("[Brain] Stopped by user")
        if HAS_LORA and lora_radio:
            BOARD.teardown()
