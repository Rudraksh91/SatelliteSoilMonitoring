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

import collections
import csv
import urllib.parse
import urllib.request
import hashlib
import hmac
import json
import os
import signal
import time
import logging
from datetime import datetime, date, timedelta, timezone
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
TOPIC_CONFIRMED = f"agrisat/{FARM_ID}/confirmed"   # signed, RETAINED: verified run minutes for the daily job

# ── Command authentication ───────────────────────────────────────────
# The broker is public (broker.hivemq.com), so anyone could publish to TOPIC_CMD. Every command
# must therefore carry an HMAC-SHA256 signature made with a shared secret that lives ONLY on this
# Pi (env var AGRISAT_CMD_KEY) and in the farmer's browser. No key configured = ALL commands refused.
#   generate one with:  openssl rand -hex 32
CMD_KEY         = os.environ.get("AGRISAT_CMD_KEY", "").strip().encode()
CMD_MAX_SKEW_S  = 120                 # reject commands whose timestamp is further than this from now
_seen_nonces    = collections.OrderedDict()   # replay protection (last 500 nonces)

# ── DHT22 sensor pin ─────────────────────────────────────────────────
DHT_PIN  = 4   # GPIO4 on Pi
DHT_TYPE = Adafruit_DHT.DHT22 if HAS_DHT else None

# ── Irrigation decision thresholds ───────────────────────────────────
# The Pi is a GATEWAY: it executes the daily decision computed centrally by the GitHub Actions job
# (scripts/run_daily.py -> data/decision.json). It makes no hydrological decisions of its own.
IST = timezone(timedelta(hours=5, minutes=30))
FARM_LAT, FARM_LNG = 21.62707, 73.5854
DECISION_URL        = "https://raw.githubusercontent.com/Rudraksh91/SatelliteSoilMonitoring/main/data/decision.json"
DECISION_REFRESH_S  = 1800            # pull the decision file every 30 min
DECISION_MAX_AGE_H  = 48              # H2: older than this -> stop model-based decisions, use the default below
START_HOUR_IST      = int(os.environ.get("AGRISAT_START_HOUR", "8"))   # earliest hour the day's run may start
STALE_DEFAULT_MIN   = float(os.environ.get("AGRISAT_STALE_DEFAULT_MIN", "0"))  # minutes/zone/day when the decision is stale (0 = no irrigation)
REQUIRE_CALIBRATED  = os.environ.get("AGRISAT_REQUIRE_CALIBRATED", "1").strip() != "0"  # refuse runs while soil/crop values are estimates
RAIN_ACTIVE_MIN_MM  = 1.0             # real-time hold: raining now (mm/h)
RAIN_HOLD_HOURS     = 48              # real-time hold: this long after >= 4 mm/h
RAIN_FORECAST_24H_MM = 10.0
STATE_FILE          = Path.home() / ".agrisat_gateway.json"          # executed decision date etc. (survives restarts)
CONFIRMED_FILE      = Path.home() / ".agrisat_confirmed_runs.json"   # verified run durations (H3) -> data/confirmed_runs.json
MIN_IRRIGATE_SECS = 600     # minimum run time: 10 minutes (blueprint t_min; avoids cavitation on short pulses)
MAX_IRRIGATE_SECS = 7200    # safety cap per run: 120 min (enforced by enforce_valve_limits watchdog; rejects any command above it)
MAX_DAILY_SECS    = 14400   # safety cap per zone per day: 240 min (blocks further AUTO opens)
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
ndvi_state = {"ndvi_avg": 0.5, "date": "", "stressed": False, "zones": [], "age_days": None}

# Auto mode flag. SAFE DEFAULT = MANUAL: after every start/reboot the Pi does NOT irrigate on its own
# until someone enables it (signed "auto_mode: true" from the dashboard) or AGRISAT_AUTO_START=1 is set.
# The water balance starts from an assumption (soil full 30 days ago) and can read far too dry on day one.
auto_mode = os.environ.get("AGRISAT_AUTO_START", "0").strip() == "1"

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
        valve_planned_secs.pop(zone_id, None)
        record_confirmed_run(zone_id)
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
        record_confirmed_run(zid)
    run_queue.clear()
    lora_send(build_stop_cmd())
    set_physical_valve(False)
    valve_planned_secs.clear()
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
    for zid, planned in list(valve_planned_secs.items()):
        t0 = valve_open_since.get(zid)
        if t0 is not None and now - t0 >= planned:
            log.info(f"[AI] Zone {zid}: planned {planned/60:.0f} min complete → CLOSE")
            if command_valve(zid, False, "auto"):
                if mqtt_conn:
                    mqtt_conn.publish(TOPIC_DATA, json.dumps({"ai_action": f"ZONE_{zid}_CLOSE",
                                                              "reason": "planned run complete"}))
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

decision = {"doc": None, "fetched": 0.0, "status": "NONE"}   # status: OK | STALE | NONE | UNCALIBRATED
executed = {"date": None}                                       # decision date already executed (persisted)
run_queue = []                                                   # [(zone_id, secs)] — zones run ONE AT A TIME
confirmed_secs = {}                                              # zone_id -> seconds the hardware confirmed flowing
_confirmed_runs = []                                             # verified runs not yet published
_confirmed_dirty = False
rain_guard_state = {"now": 0.0, "prob": 0.0, "next24": 0.0, "hours_since": float("inf"), "updated": 0.0}
valve_planned_secs = {}                                          # zone_id -> run length for the current cycle

def _today_ist():
    return datetime.now(IST).strftime("%Y-%m-%d")

def load_state():
    global _confirmed_runs
    try:
        executed.update(json.loads(STATE_FILE.read_text()))
    except Exception:
        pass
    try:
        _confirmed_runs = json.loads(CONFIRMED_FILE.read_text())
    except Exception:
        _confirmed_runs = []

def save_state():
    try:
        STATE_FILE.write_text(json.dumps(executed))
        CONFIRMED_FILE.write_text(json.dumps(_confirmed_runs[-400:]))
    except Exception as e:
        log.warning(f"[Gateway] state save failed: {e}")

def valve_confirmed(zid):
    """True only when the hardware itself shows water flowing: the field node reports the valve open
    (fresh), or — in single-relay bring-up with no node — the relay GPIO is energised."""
    nd = node_data.get(_node_for_zone(zid))
    if nd and nd["last_seen"] > 0:
        return bool(nd["valve"]) and time.time() - nd["last_seen"] <= NODE_STALE_SECS
    return bool(physical_valve_open)

_last_track = time.time()
def track_confirmed_runs():
    """Every loop tick: add the seconds each valve was CONFIRMED flowing. Only these verified
    durations are reported back to the water balance (H3)."""
    global _last_track
    now = time.time(); dt = now - _last_track; _last_track = now
    if 0 < dt <= 30:
        for zid in list(valve_open_since):
            if valve_confirmed(zid):
                confirmed_secs[zid] = confirmed_secs.get(zid, 0.0) + dt

def record_confirmed_run(zid):
    """Called when a valve closes: store the verified minutes for the daily job."""
    global _confirmed_dirty
    secs = confirmed_secs.pop(zid, 0.0)
    if secs >= 30:
        _confirmed_runs.append({"date": _today_ist(), "zone": int(zid), "minutes": round(secs / 60.0, 1),
                                "source": "gateway"})
        _confirmed_dirty = True
        log_event("CONFIRMED_RUN", zid, "gateway", f"{secs/60:.1f} min")
        save_state()

def _merge_confirmed_for_publish():
    """Daily totals per (date, zone) — the format scripts/run_daily.py reads."""
    tot = {}
    for e in _confirmed_runs:
        k = (e["date"], e["zone"]); tot[k] = tot.get(k, 0.0) + e["minutes"]
    return [{"date": d, "zone": z, "minutes": round(m, 1), "source": "gateway"} for (d, z), m in sorted(tot.items())]

def _signed_envelope(payload):
    """Same wire format as dashboard commands: {"m": "<inner JSON>", "sig": "<hex HMAC-SHA256 of m>"}."""
    inner = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return json.dumps({"m": inner, "sig": hmac.new(CMD_KEY, inner.encode(), hashlib.sha256).hexdigest()})

_last_confirmed_publish = 0.0
def publish_confirmed_runs():
    """Feed verified run minutes back to the daily job — no GitHub token needed: the last 14 days of daily totals
    go out as a signed RETAINED MQTT message that scripts/run_daily.py reads at 07:00 IST. Re-sent every 6 h
    so the broker keeps it fresh."""
    global _confirmed_dirty, _last_confirmed_publish
    now = time.time()
    if not (_confirmed_dirty or now - _last_confirmed_publish > 6 * 3600):
        return
    if not mqtt_conn:
        return                        # retry next tick; nothing is lost — _confirmed_runs persists on disk
    if not CMD_KEY:
        log.warning("[Gateway] AGRISAT_CMD_KEY not set — confirmed runs cannot be signed, not published")
        return
    cutoff = (datetime.now(IST) - timedelta(days=14)).strftime("%Y-%m-%d")
    runs = [r for r in _merge_confirmed_for_publish() if r["date"] >= cutoff]
    try:
        info = mqtt_conn.publish(TOPIC_CONFIRMED, _signed_envelope({"farm": FARM_ID, "ts": int(now * 1000), "runs": runs}),
                                 qos=1, retain=True)
        _confirmed_dirty = False
        _last_confirmed_publish = now
        log.info(f"[Gateway] confirmed runs published ({len(runs)} day/zone totals)")
    except Exception as e:
        log.warning(f"[Gateway] could not publish confirmed runs: {e} — will retry")

def _open_meteo(params):
    url = "https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params, safe=",")
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.load(r)

def refresh_rain_guard():
    """Real-time hold on top of the central forecast rule: raining now, high probability, or just after heavy rain."""
    try:
        d = _open_meteo({"latitude": FARM_LAT, "longitude": FARM_LNG,
                         "hourly": "precipitation,precipitation_probability",
                         "current": "precipitation", "past_days": 2, "forecast_days": 2,
                         "timezone": "Asia/Kolkata"})
        times = d["hourly"]["time"]; prec = d["hourly"]["precipitation"]; prob = d["hourly"]["precipitation_probability"]
        now_s = datetime.now(IST).strftime("%Y-%m-%dT%H:00")
        idx = times.index(now_s) if now_s in times else max(0, len(times) // 2)
        hours_since = float("inf")
        for i in range(idx, max(-1, idx - 49), -1):
            if (prec[i] or 0) >= 4.0:
                hours_since = idx - i
                break
        rain_guard_state.update(now=(d.get("current", {}).get("precipitation") or prec[idx] or 0.0),
                                prob=prob[idx] or 0.0,
                                next24=sum((prec[i] or 0.0) for i in range(idx, min(idx + 24, len(prec)))),
                                hours_since=hours_since, updated=time.time())
    except Exception as e:
        log.warning(f"[RainGuard] refresh failed: {e}")

def rain_guard_active():
    g = rain_guard_state
    if time.time() - g["updated"] > 3 * 3600:
        return True          # no recent forecast -> be conservative and hold automatic irrigation
    return (g["now"] >= RAIN_ACTIVE_MIN_MM or g["prob"] > 60 or
            g["next24"] >= RAIN_FORECAST_24H_MM or g["hours_since"] < RAIN_HOLD_HOURS)

def _parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def refresh_decision():
    """Pull data/decision.json and classify it. H2: a file older than 48 h (or unreadable for that long)
    stops all model-based decisions; the farmer-chosen default (STALE_DEFAULT_MIN) applies instead."""
    try:
        with urllib.request.urlopen(DECISION_URL + f"?_={int(time.time())}", timeout=20) as r:
            doc = json.load(r)
        decision.update(doc=doc, fetched=time.time())
        ndvi_state.update(ndvi_avg=(doc.get("farm") or {}).get("ndvi"), date=doc.get("date", ""),
                          stressed=bool((doc.get("farm") or {}).get("stress_flag")))
    except Exception as e:
        log.warning(f"[Decision] fetch failed: {e}")
    doc = decision["doc"]
    if not doc:
        decision["status"] = "NONE"
        return
    age_h = (datetime.now(timezone.utc) - _parse_ts(doc["generated_at"])).total_seconds() / 3600.0
    if age_h > DECISION_MAX_AGE_H:
        if decision["status"] != "STALE":
            log.error(f"[Decision] file is {age_h:.0f} h old (> {DECISION_MAX_AGE_H} h) — model decisions STOPPED, "
                      f"default {STALE_DEFAULT_MIN:g} min/zone/day applies")
            log_event("DECISION_STALE", None, "gateway", f"{age_h:.0f} h old")
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({"ai_action": "DECISION_STALE",
                                                           "reason": f"decision file {age_h:.0f} h old"}))
        decision["status"] = "STALE"
    elif REQUIRE_CALIBRATED and not doc.get("calibrated", False):
        decision["status"] = "UNCALIBRATED"
    else:
        decision["status"] = "OK"

def plan_today_runs():
    """Zones to run today as [(zone_id, seconds)], limits re-applied here (H1): the gateway rejects anything
    above the caps no matter what the JSON says."""
    st, doc = decision["status"], decision["doc"]
    today = _today_ist()
    if st == "STALE":
        mins = {z["id"]: STALE_DEFAULT_MIN for z in ZONES} if STALE_DEFAULT_MIN > 0 else {}
    elif st == "OK" and doc and doc.get("date") == today:
        mins = {int(z["id"]): z.get("run_minutes") for z in doc.get("zones", []) if z.get("decision") == "IRRIGATE"}
        order = [int(i) for i in doc.get("sequence", [])]
        mins = {i: mins[i] for i in order if i in mins}
    else:
        return []
    out = []
    for zid, m in mins.items():
        if not m or zid not in {z["id"] for z in ZONES}:
            continue
        secs = float(m) * 60.0
        if secs > MAX_IRRIGATE_SECS:
            log.warning(f"[Safety] Zone {zid}: commanded {m:g} min exceeds the {MAX_IRRIGATE_SECS/60:.0f} min cap — clamped")
            secs = MAX_IRRIGATE_SECS
        secs = min(secs, MAX_DAILY_SECS - daily_run_secs.get(zid, 0.0))
        if secs < MIN_IRRIGATE_SECS:
            log.info(f"[Safety] Zone {zid}: {secs/60:.1f} min is under the {MIN_IRRIGATE_SECS/60:.0f} min minimum — skipped")
            continue
        out.append((zid, secs))
    return out

def start_todays_runs():
    """Queue the day's runs once (after START_HOUR_IST, auto mode on, no rain hold)."""
    if not auto_mode:
        return
    today = _today_ist()
    if executed.get("date") == today or datetime.now(IST).hour < START_HOUR_IST:
        return
    if decision["status"] not in ("OK", "STALE"):
        return
    if decision["status"] == "OK" and (decision["doc"] or {}).get("date") != today:
        return                      # today's file is not published yet (job runs 07:00 IST) — keep waiting, don't burn the day
    if rain_guard_active():
        log.info("[AI] Real-time rain hold — today's run not started yet")
        return
    runs = plan_today_runs()
    executed["date"] = today          # one attempt per day, also after a restart
    save_state()
    if not runs:
        log.info(f"[AI] Decision status {decision['status']}: nothing to run today")
        return
    run_queue.extend(runs)
    log.info("[AI] Today's sequence: " + ", ".join(f"Z{z} {s/60:.0f} min" for z, s in runs))

def run_sequencer():
    """Sequential zone execution: open the next queued zone only when nothing else is running."""
    if not auto_mode:
        run_queue.clear()
        return
    if run_queue and not valve_open_since and not rain_guard_active():
        zid, secs = run_queue.pop(0)
        if command_valve(zid, True, "auto"):
            valve_planned_secs[zid] = secs
            log.info(f"[AI] Zone {zid}: OPEN for {secs/60:.0f} min (decision {decision['doc'].get('date') if decision['doc'] else 'default'})")
            if mqtt_conn:
                mqtt_conn.publish(TOPIC_DATA, json.dumps({"ai_action": f"ZONE_{zid}_OPEN",
                                                           "reason": f"central decision — {secs/60:.0f} min"}))

def decide_irrigation():
    """Kept for the main loop: pick up today's decision and run the queue."""
    start_todays_runs()
    run_sequencer()

# ══════════════════════════════════════════════════════════════════════
# MQTT — Dashboard cloud bridge
# ══════════════════════════════════════════════════════════════════════

def verify_command(raw):
    """Return the command dict if the MQTT payload is authentic and fresh, else None.
    Wire format: {"m": "<inner JSON string>", "sig": "<hex HMAC-SHA256 of m>"};
    inner JSON carries the command plus "ts" (ms since epoch) and a random "nonce"."""
    if not CMD_KEY:
        log.warning("[MQTT] AGRISAT_CMD_KEY is not set — command refused (fail closed)")
        return None
    try:
        env = json.loads(raw)
        inner, sig = env["m"], env["sig"]
        expect = hmac.new(CMD_KEY, inner.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expect, str(sig)):
            log.warning("[MQTT] Command REJECTED: bad signature")
            return None
        d = json.loads(inner)
        if abs(time.time() - float(d["ts"]) / 1000.0) > CMD_MAX_SKEW_S:
            log.warning("[MQTT] Command REJECTED: stale or future timestamp (replay?)")
            return None
        nonce = str(d["nonce"])
        if nonce in _seen_nonces:
            log.warning("[MQTT] Command REJECTED: replayed nonce")
            return None
        _seen_nonces[nonce] = True
        while len(_seen_nonces) > 500:
            _seen_nonces.popitem(last=False)
        return d
    except Exception as e:
        log.warning(f"[MQTT] Command REJECTED: malformed ({e})")
        return None

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
        d = verify_command(msg.payload)
        if d is None:
            return
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

def _shutdown(signum, frame):
    """systemd stop/restart (SIGTERM) or Ctrl-C: close every valve and release the relay pin LOW before exiting,
    otherwise an energised relay would stay latched open after the program is gone."""
    log.warning(f"[Brain] signal {signum} received — closing all valves and exiting")
    try:
        stop_all_valves()
    except Exception as e:
        log.error(f"[Brain] stop_all_valves failed: {e}")
    if HAS_GPIO:
        try:
            GPIO.output(VALVE_GPIO_PIN, GPIO.LOW)
            GPIO.cleanup()
        except Exception:
            pass
    raise SystemExit(0)

def main():
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    log.info("══════════════════════════════════════════")
    log.info("  TERRA-CORE Pi Brain starting…")
    log.info(f"  Farm ID: {FARM_ID}")
    log.info("══════════════════════════════════════════")

    init_lora()
    init_valve_gpio()
    init_mqtt()
    load_state()
    if auto_mode:
        log.warning("[Safety] AGRISAT_AUTO_START=1 — starting in AUTO mode")
    else:
        log.info("[Safety] Starting in MANUAL mode — no automatic irrigation until auto mode is enabled "
                 "from the dashboard (or AGRISAT_AUTO_START=1). Runs follow data/decision.json from the daily job.")

    last_decision_fetch = 0
    last_publish_runs   = 0
    last_weather_read = 0
    last_decision     = 0
    last_publish      = 0

    while True:
        now = time.time()

        # Read weather every 30s
        if now - last_weather_read > 30:
            read_weather()
            last_weather_read = now

        # Pull the central decision every 30 min (real-time rain hold refreshed with it)
        if now - last_decision_fetch > DECISION_REFRESH_S:
            refresh_decision()
            refresh_rain_guard()
            last_decision_fetch = now

        # Verified run durations (only confirmed flow counts); publish when new ones exist
        track_confirmed_runs()
        if now - last_publish_runs > 300:
            publish_confirmed_runs()
            last_publish_runs = now

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
