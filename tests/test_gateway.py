"""Tests for the Pi gateway (hardware/raspberry_pi/brain.py): executes the central decision with safety limits.
Run: python3 tests/test_gateway.py   (no hardware, network or /var/log access needed)"""
import importlib.util, json, logging, pathlib, sys, time
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
pathlib.Path.mkdir = lambda self, *a, **k: None
logging.FileHandler = lambda *a, **k: logging.NullHandler()
spec = importlib.util.spec_from_file_location("brain", ROOT / "hardware/raspberry_pi/brain.py")
b = importlib.util.module_from_spec(spec); spec.loader.exec_module(b)
start_manual = b.auto_mode is False
b.EVENT_LOG_FILE = pathlib.Path("/nonexistent/log.csv")
b.STATE_FILE = pathlib.Path("/nonexistent/state.json"); b.CONFIRMED_FILE = pathlib.Path("/nonexistent/c.json")
sent = []; b.lora_send = lambda s: sent.append(s)
b.START_HOUR_IST = 0
n = 0
def check(name, cond):
    global n; n += 1; print(("ok   " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)

def doc(age_h=1, date=None, calibrated=True, zones=None, seq=None):
    gen = datetime.now(timezone.utc) - timedelta(hours=age_h)
    zones = zones or [{"id": 1, "decision": "IRRIGATE", "run_minutes": 60.0},
                      {"id": 2, "decision": "IRRIGATE", "run_minutes": 30.0},
                      {"id": 3, "decision": "NO_RUN", "run_minutes": 0.0}]
    return {"generated_at": gen.isoformat(timespec="seconds"), "date": date or b._today_ist(),
            "calibrated": calibrated, "zones": zones, "sequence": seq if seq is not None else [1, 2],
            "farm": {"ndvi": 0.4, "stress_flag": False}}

def fresh(d, auto=True):
    b.valve_open_since.clear(); b.valve_planned_secs.clear(); b.daily_run_secs.clear(); b.pending_confirm.clear()
    b.run_queue.clear(); b.confirmed_secs.clear(); b._confirmed_runs.clear(); b.executed["date"] = None
    b.auto_mode = auto; b.physical_valve_open = False
    b.decision.update(doc=d, status="NONE", fetched=time.time())
    # classify without the network: same logic as refresh_decision() after its fetch
    b._open = b.urllib.request.urlopen
    b.urllib.request.urlopen = lambda *a, **k: (_ for _ in ()).throw(OSError("offline"))
    b.refresh_decision()
    b.rain_guard_state.update(now=0.0, prob=0.0, next24=0.0, hours_since=float("inf"), updated=time.time())
    sent.clear()

check("Pi starts in MANUAL mode by default", start_manual)

# ── classification ──
fresh(doc(age_h=1));                        check("fresh calibrated decision -> OK", b.decision["status"] == "OK")
fresh(doc(age_h=47));                       check("47 h old still OK", b.decision["status"] == "OK")
fresh(doc(age_h=49));                       check("49 h old -> STALE (H2)", b.decision["status"] == "STALE")
fresh(doc(calibrated=False));               check("estimates only -> UNCALIBRATED, refuses to run", b.decision["status"] == "UNCALIBRATED")
fresh(None);                                check("no decision at all -> NONE", b.decision["status"] == "NONE")

# ── execution ──
fresh(doc()); b.decide_irrigation()
check("today's decision: zone 1 opens first, ONE valve at a time", sent == ["CMD:Z1:OPEN"] and list(b.valve_open_since) == [1])
check("zone 2 waits in the queue (sequential)", b.run_queue == [(2, 1800.0)])
b.valve_open_since[1] -= 3601; b.enforce_valve_limits(); b.decide_irrigation()
check("after zone 1's 60 min it closes and zone 2 starts", sent[-2:] == ["CMD:Z1:CLOSE", "CMD:Z2:OPEN"])
b.decide_irrigation()
check("the same decision is not executed twice in a day", b.executed["date"] == b._today_ist() and b.run_queue == [])

fresh(doc(date=(datetime.now(b.IST) - timedelta(days=1)).strftime("%Y-%m-%d"))); b.decide_irrigation()
check("yesterday's file: waits, does not run and does not burn the day", sent == [] and b.executed["date"] is None)
fresh(doc(calibrated=False)); b.decide_irrigation()
check("uncalibrated decision never opens a valve", sent == [])
fresh(doc(age_h=60)); b.decide_irrigation()
check("stale decision + default 0 min -> no irrigation", sent == [])
b.STALE_DEFAULT_MIN = 20.0; fresh(doc(age_h=60)); b.decide_irrigation()
check("stale decision + farmer default 20 min -> every zone runs 20 min, sequentially",
      sent == ["CMD:Z1:OPEN"] and [q[1] for q in b.run_queue] == [1200.0, 1200.0]); b.STALE_DEFAULT_MIN = 0.0
fresh(doc(), auto=False); b.decide_irrigation()
check("manual mode: nothing runs", sent == [])
fresh(doc()); b.rain_guard_state.update(prob=90.0); b.decide_irrigation()
check("real-time rain hold blocks the start", sent == [] and b.executed["date"] is None)

# ── limits re-applied at the gateway (H1) ──
fresh(doc(zones=[{"id": 1, "decision": "IRRIGATE", "run_minutes": 500.0}], seq=[1])); b.decide_irrigation()
check("500 min commanded -> clamped to the 120 min cap", b.valve_planned_secs[1] == 7200.0)
fresh(doc(zones=[{"id": 1, "decision": "IRRIGATE", "run_minutes": 4.0}], seq=[1])); b.decide_irrigation()
check("4 min is under the 10 min minimum -> skipped", sent == [])
fresh(doc()); b.daily_run_secs[1] = b.MAX_DAILY_SECS - 300; b.decide_irrigation()
check("daily cap leaves 5 min -> zone 1 skipped, zone 2 still runs", sent == ["CMD:Z2:OPEN"])

# ── confirmed runs (H3): only verified flow counts ──
fresh(doc(zones=[{"id": 1, "decision": "IRRIGATE", "run_minutes": 20.0}], seq=[1])); b.decide_irrigation()
b.physical_valve_open = False; b._last_track = time.time() - 10; b.track_confirmed_runs()
check("relay not confirmed -> 0 s counted", b.confirmed_secs.get(1, 0) == 0)
b.physical_valve_open = True
for _ in range(130): b._last_track = time.time() - 10; b.track_confirmed_runs()
b.valve_open_since[1] -= 1300; b.enforce_valve_limits()
check("closing records the verified minutes", len(b._confirmed_runs) == 1 and abs(b._confirmed_runs[0]["minutes"] - 21.7) < 0.2)
pub = b._merge_confirmed_for_publish()
check("published format = {date, zone, minutes} totals", pub[0]["zone"] == 1 and pub[0]["date"] == b._today_ist())
b.stop_all_valves(); check("emergency stop clears the queue", b.run_queue == [] and not b.valve_open_since)
print(f"\n{n} checks passed")
