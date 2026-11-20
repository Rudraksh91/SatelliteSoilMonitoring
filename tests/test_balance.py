"""Tests for the Pi's water-balance irrigation engine (hardware/raspberry_pi/brain.py).
Run:  python3 tests/test_balance.py        (no hardware, network or /var/log access needed)"""
import importlib.util, logging, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parent.parent
pathlib.Path.mkdir = lambda self, *a, **k: None                  # brain.py wants /var/log/agrisat
logging.FileHandler = lambda *a, **k: logging.NullHandler()
spec = importlib.util.spec_from_file_location("brain", ROOT / "hardware/raspberry_pi/brain.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
b.lora_send = lambda s: None
b.EVENT_LOG_FILE = pathlib.Path("/nonexistent/irrigation_log.csv")   # log_event swallows the write error
b.STATE_FILE = pathlib.Path("/nonexistent/state.json")

def close(x, y, t=1e-6): return abs(x - y) < t
n = 0
def check(name, cond):
    global n
    n += 1; print(("ok   " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)

# ── 1. parity with tests/test_fao56.js (same hand-calculated vectors) ──
check("TAW = 60 mm", close(b.fao_taw(), 60))
check("dry day ETc = Kc*ET0", close(b.fao56_step(10, 5, 0, 0, 0.8), 14))
check("Ks = 0.5 at Dr 45", close(b.fao56_step(45, 5, 0, 0, 1.0), 47.5))
check("rain < 5 mm ignored", close(b.fao56_step(10, 0, 4.9), 10))
check("20 mm rain -> 18 effective", close(b.fao56_step(30, 0, 20), 12))
check("clamped at 0", close(b.fao56_step(5, 0, 50), 0))
check("clamped at TAW", close(b.fao56_step(55, 400, 0, 0, 1.0), 60))
check("irrigation subtracts", close(b.fao56_step(20, 0, 0, 12), 8))
check("Kc NDVI .5 -> .825", close(b.ndvi_to_kc(0.5, 2), 0.825))
check("Kc stale -> fallback", close(b.ndvi_to_kc(0.5, 25), 0.6))
check("Kc missing -> fallback", close(b.ndvi_to_kc(None, 2), 0.6))
check("Kc low NDVI clamped", close(b.ndvi_to_kc(0.0, 2), 0.3))
check("trigger depletion = 24 mm (22% VWC)", close(b.trigger_depletion_mm(), 24.0))

# ── 2. decision cycle on a long dry spell ──
dry = {"daily": {"time": [f"2026-11-{d:02d}" for d in range(1, 31)],
                 "et0_fao_evapotranspiration": [4.4] * 30, "precipitation_sum": [0.0] * 30}}
b._open_meteo = lambda params: dry
b.ndvi_state.update(zones=[0.4, 0.4, 0.4], age_days=3)
sent = []; b.lora_send = lambda s: sent.append(s)

def fresh():
    b.valve_open_since.clear(); b.valve_planned_secs.clear(); b.daily_run_secs.clear(); b.pending_confirm.clear()
    b.auto_mode = True; b.refresh_balance()
    b.rain_guard_state.update(now=0.0, prob=0.0, next24=0.0, hours_since=float("inf"), updated=time.time())
    sent.clear()

fresh()
check("30 dry days deplete past the trigger", all(dr > b.trigger_depletion_mm() for dr in b.balance["dr"].values()))
b.decide_irrigation()
check("all 3 zones opened", sent == ["CMD:Z1:OPEN", "CMD:Z2:OPEN", "CMD:Z3:OPEN"])
check("run capped at MAX_IRRIGATE_SECS", all(close(v, b.MAX_IRRIGATE_SECS) for v in b.valve_planned_secs.values()))
b.valve_open_since[1] -= b.MAX_IRRIGATE_SECS + 1; sent.clear()
b.enforce_valve_limits()
check("zone 1 closed when its planned run completes", sent == ["CMD:Z1:CLOSE"] and 1 not in b.valve_open_since)
check("zones 2/3 keep running", 2 in b.valve_open_since and 3 in b.valve_open_since)

# water is credited only with confirmed hardware
dr2 = b.balance["dr"][2]; b._last_credit = time.time() - 10
b.physical_valve_open = False; b.credit_open_valves()
check("no credit while relay is NOT energised", close(b.balance["dr"][2], dr2))
b._last_credit = time.time() - 10; b.physical_valve_open = True; b.credit_open_valves()
check("credit 2.7 mm/h while confirmed", close(dr2 - b.balance["dr"][2], 2.7 * 10 / 3600, 1e-3))
b.physical_valve_open = False

# ── 3. guards ──
fresh(); b.rain_guard_state.update(prob=80.0); b.decide_irrigation()
check("rain probability > 60 holds irrigation", sent == [])
fresh(); b.rain_guard_state.update(next24=12.0); b.decide_irrigation()
check("10 mm forecast holds irrigation", sent == [])
fresh(); b.rain_guard_state.update(hours_since=10); b.decide_irrigation()
check("recent heavy rain holds irrigation", sent == [])
fresh(); b.balance["updated"] -= b.BALANCE_MAX_AGE_S + 1; b.decide_irrigation()
check("stale balance -> no automatic decisions", sent == [])
fresh(); b.rain_guard_state["updated"] -= b.BALANCE_MAX_AGE_S + 1; b.decide_irrigation()
check("stale forecast -> conservative hold", sent == [])
fresh(); b.auto_mode = False; b.decide_irrigation()
check("auto mode off -> nothing", sent == [])
fresh(); b.daily_run_secs.update({1: b.MAX_DAILY_SECS, 2: b.MAX_DAILY_SECS - 60, 3: 0}); b.decide_irrigation()
check("daily cap: zone 1 blocked, zone 2 (<5 min left) skipped, zone 3 runs", sent == ["CMD:Z3:OPEN"])
fresh(); b.balance["dr"] = {1: 5.0, 2: 5.0, 3: 5.0}; b.decide_irrigation()
check("well-watered soil -> no irrigation", sent == [])
print(f"\n{n} checks passed")
