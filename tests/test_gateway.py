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

# ── farmer autopilot schedule (signed multi-day plan from the app) ──
b.SCHEDULE_FILE = pathlib.Path("/nonexistent/schedule.json")
b.CMD_KEY = b"k" * 32
def reset_sched():
    b.farmer_schedule.update(issued=0.0, days={}); b.schedule_done.clear()
def hm(m): return f"{m//60:02d}:{m%60:02d}"
def now_min(): d = datetime.now(b.IST); return d.hour * 60 + d.minute
def day(offset=0): return (datetime.now(b.IST) + timedelta(days=offset)).strftime("%Y-%m-%d")
def push(plan):
    import hmac, hashlib
    inner = json.dumps({"schedule": plan, "ts": int(time.time() * 1000), "nonce": "n%d" % time.time_ns()})
    env = json.dumps({"m": inner, "sig": hmac.new(b.CMD_KEY, inner.encode(), hashlib.sha256).hexdigest()})
    class M: payload = env.encode()
    b.mqtt_on_message(None, None, M)
nm = now_min()
if 6 <= nm <= 1400:                                   # avoid the midnight edge so "now" and "in 30 min" are the same day
    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(nm - 5), "minutes": 30}, {"zone": 2, "start": hm(nm - 4), "minutes": 20}]}})
    check("signed plan is installed for today", len(b.farmer_schedule["days"][day()]) == 2)
    b.decide_irrigation()
    check("a plan entry whose time has come opens its zone (one valve at a time)", sent == ["CMD:Z1:OPEN"] and b.run_queue == [(2, 1200.0)])
    check("the plan replaces the central decision for today", b.executed["date"] is None)
    sent.clear(); b.decide_irrigation()
    check("entries run once, not again on the next tick", sent == [] and len(b.run_queue) == 1)
    check("status block shows the plan to the app", b.schedule_summary()["today"][0]["done"] and b.schedule_summary()["days"] == 1)

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(min(nm + 30, 1439)), "minutes": 30}]}}); b.decide_irrigation()
    check("a plan entry in the future waits", sent == [] and not b.run_queue)

    fresh(doc(), auto=False); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(nm - 1), "minutes": 30}]}}); b.decide_irrigation()
    check("manual mode: the plan is stored but nothing runs", sent == [] and len(b.farmer_schedule["days"][day()]) == 1)

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): []}}); b.decide_irrigation()
    check("an empty day means NO irrigation, even though the central decision says irrigate", sent == [] and not b.run_queue)

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(nm - 1), "minutes": 30}]}}); b.rain_guard_state.update(prob=90.0)
    b.decide_irrigation()
    check("the Pi's real-time rain hold still blocks a planned run", sent == [] and b.run_queue == [(1, 1800.0)])

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(max(0, nm - 200)), "minutes": 30}]}}); b.decide_irrigation()
    check("a run more than 3 h overdue (Pi was off) is skipped, not run late", sent == [] and not b.run_queue)

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(nm - 1), "minutes": 500}, {"zone": 9, "start": "06:00", "minutes": 30},
                           {"zone": 2, "start": "06:00", "minutes": 4}, {"zone": 3, "start": "25:99", "minutes": 30}],
                   day(40): [{"zone": 1, "start": "06:00", "minutes": 30}], "garbage": [], day(-3): [{"zone": 1, "start": "06:00", "minutes": 30}]}})
    check("limits re-applied: 500 min clamped to 120, unknown zone / under 10 min / bad time / far or past dates dropped",
          list(b.farmer_schedule["days"]) == [day()] and b.farmer_schedule["days"][day()] == [{"zone": 1, "start": nm - 1, "minutes": 120.0}])

    fresh(doc(), auto=True); reset_sched()
    inner = json.dumps({"schedule": {"days": {day(): [{"zone": 1, "start": hm(nm - 1), "minutes": 30}]}}, "ts": int(time.time() * 1000), "nonce": "forged"})
    class Forged: payload = json.dumps({"m": inner, "sig": "00" * 32}).encode()
    b.mqtt_on_message(None, None, Forged)
    check("an unsigned or wrongly signed plan is refused", b.farmer_schedule["days"] == {})

    fresh(doc(), auto=True); reset_sched()
    push({"days": {day(): [{"zone": 1, "start": hm(nm - 1), "minutes": 30}]}}); b.daily_run_secs[1] = b.MAX_DAILY_SECS - 300
    b.decide_irrigation()
    check("planned run is skipped when under 10 min of the daily cap remain", sent == [] and not b.run_queue)
    reset_sched(); b.CMD_KEY = b""
print(f"\n{n} checks passed")

# ── shutdown (systemd stop / update restart) must close valves ──
fresh(doc(zones=[{"id": 1, "decision": "IRRIGATE", "run_minutes": 30.0}], seq=[1])); b.decide_irrigation()
sent.clear()
try:
    b._shutdown(15, None); raised = False
except SystemExit:
    raised = True
check("SIGTERM handler closes every valve, clears the queue and exits", raised and "CMD:ALL:STOP" in sent and not b.valve_open_since)
print(f"{n} checks passed (incl. shutdown)")
