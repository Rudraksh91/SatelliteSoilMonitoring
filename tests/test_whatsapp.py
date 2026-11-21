"""Offline tests for scripts/whatsapp_notify.py (no network, no real token). Run: python3 tests/test_whatsapp.py"""
import contextlib, importlib.util, io, json, pathlib, sys, tempfile, urllib.error
from datetime import datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wn", ROOT / "scripts/whatsapp_notify.py")
wn = importlib.util.module_from_spec(spec); spec.loader.exec_module(wn)
n = fail = 0
def check(name, cond):
    global n, fail
    n += 1; print(("ok   " if cond else "FAIL ") + name)
    if not cond: fail += 1

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)
TODAY = NOW.astimezone(IST).date().isoformat()
def dec(zones, rain=0.0, date=TODAY, valid=NOW + timedelta(hours=40)):
    return {"date": date, "valid_until": valid.isoformat(), "farm": {"forecast_rain_mm_48h": rain}, "zones": zones}
Z = lambda i, d, m=0.0: {"id": i, "name": f"Zone {i} – x", "decision": d, "run_minutes": m}

# ── message building ──
m = wn.build_message(dec([Z(1, "NO_RUN"), Z(2, "NO_RUN")]), "CAET Dediapada")
check("all zones fine -> 'no irrigation needed' in Gujarati", m["code"] == "NO_RUN" and m["params"][1] == "પિયતની જરૂર નથી" and "માટીમાં પૂરતું પાણી છે." in m["params"][2])
m = wn.build_message(dec([Z(1, "IRRIGATE", 44.6), Z(2, "NO_RUN"), Z(3, "IRRIGATE", 20)]), "Farm")
check("zones to water: headline says irrigate, only those zones are listed with rounded minutes",
      m["code"] == "IRRIGATE" and m["params"][1] == "આજે પિયત આપો" and "ઝોન 1: લગભગ 45 મિનિટ" in m["params"][2] and "ઝોન 3: લગભગ 20 મિનિટ" in m["params"][2] and "ઝોન 2" not in m["params"][2])
m = wn.build_message(dec([Z(1, "SKIP_RAIN")], rain=17.6), "Farm")
check("rain coming: wait for rain with the forecast amount", m["code"] == "SKIP_RAIN" and m["params"][1] == "વરસાદની રાહ જુઓ" and "લગભગ 18 મિમી" in m["params"][2])
check("irrigate wins over rain when zones differ", wn.overall(dec([Z(1, "SKIP_RAIN"), Z(2, "IRRIGATE", 10)])) == "IRRIGATE")
check("every message says it is an estimate", all("અંદાજ" in wn.build_message(dec(zs), "F")["params"][2] for zs in ([Z(1, "NO_RUN")], [Z(1, "IRRIGATE", 30)], [Z(1, "SKIP_RAIN")])))
check("template parameters are single lines with no tabs or newlines, and under the 1024 limit",
      all(("\n" not in p and "\t" not in p and "    " not in p and len(p) <= 1024) for p in wn.build_message(dec([Z(i, "IRRIGATE", 30) for i in range(1, 40)]), "A\nB\tC")["params"]))
check("one_line collapses whitespace", wn.one_line("a \n b\t\tc   d") == "a b c d")

# ── when a decision may be sent ──
check("today's valid decision is usable", wn.usable(dec([Z(1, "NO_RUN")]), NOW)[0])
check("a decision for yesterday is not sent", not wn.usable(dec([Z(1, "NO_RUN")], date="2026-11-20"), NOW)[0])
check("a decision past valid_until is not sent", not wn.usable(dec([Z(1, "NO_RUN")], valid=NOW - timedelta(hours=1)), NOW)[0])
check("missing or broken decision is not sent", not wn.usable(None, NOW)[0] and not wn.usable({"date": TODAY, "zones": [Z(1, "NO_RUN")]}, NOW)[0] and not wn.usable({"date": TODAY, "zones": []}, NOW)[0])

# ── payload and masking ──
p = wn.payload("919876543210", "daily_irrigation_advice", "gu", ["a", "b", "c"])
check("Cloud API template payload shape", p["messaging_product"] == "whatsapp" and p["type"] == "template" and p["to"] == "919876543210"
      and p["template"]["name"] == "daily_irrigation_advice" and p["template"]["language"]["code"] == "gu"
      and [x["text"] for x in p["template"]["components"][0]["parameters"]] == ["a", "b", "c"])
check("numbers are masked in logs", "9876543210" not in wn.mask("919876543210") and wn.mask("919876543210").endswith("3210"))

# ── run(): dry run, sending, once a day, failures ──
tmp = pathlib.Path(tempfile.mkdtemp()); wn.STATE = tmp / "whatsapp_state.json"
calls = []
class Resp:
    status = 200
    def __enter__(self): return self
    def __exit__(self, *a): return False
def opener_ok(req, timeout=0): calls.append((req.full_url, json.loads(req.data), dict(req.headers))); return Resp()
ENV = {"WHATSAPP_TOKEN": "SECRET-TOKEN-123", "WHATSAPP_PHONE_NUMBER_ID": "1055", "WHATSAPP_TO": "919876543210, +91 98123-45678, 12"}
D = dec([Z(1, "IRRIGATE", 30)])

def captured(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf): r = fn()
    return r, buf.getvalue()

r, out = captured(lambda: wn.run(env={}, now=NOW, opener=opener_ok, decision=D, farm_name="F"))
check("nothing configured: dry run, nothing sent, nothing written", r["sent"] == 0 and r["skipped"] == "dry run" and not calls and not wn.STATE.exists())
r, out = captured(lambda: wn.run(env=dict(ENV, WHATSAPP_DRY_RUN="1"), now=NOW, opener=opener_ok, decision=D, farm_name="F"))
check("WHATSAPP_DRY_RUN=1 beats a full configuration", r["sent"] == 0 and not calls)
r, out = captured(lambda: wn.run(env=dict(ENV, WHATSAPP_TO=""), now=NOW, opener=opener_ok, decision=D, farm_name="F"))
check("no recipients: nothing sent", r["sent"] == 0 and not calls)

r, out = captured(lambda: wn.run(env=ENV, now=NOW, opener=opener_ok, decision=D, farm_name="CAET"))
check("configured: one message per valid number (the 2-digit entry is ignored)", r["sent"] == 2 and len(calls) == 2)
check("goes to the sender's endpoint with a bearer token", calls[0][0].endswith("/1055/messages") and calls[0][2].get("Authorization") == "Bearer SECRET-TOKEN-123")
check("recipients get the digits-only numbers", [c[1]["to"] for c in calls] == ["919876543210", "919812345678"])
check("the log never shows a full number or the token", "9876543210" not in out and "98123" not in out and "SECRET-TOKEN" not in out and "sent to +91" in out)
check("state file records the date, not the numbers", json.loads(wn.STATE.read_text()) == {"last_sent_date": TODAY, "recipients": 2, "failed": 0} and "9876" not in wn.STATE.read_text())

calls.clear()
r, out = captured(lambda: wn.run(env=ENV, now=NOW, opener=opener_ok, decision=D, farm_name="CAET"))
check("second run the same day sends nothing", r["sent"] == 0 and r["skipped"] == "already sent" and not calls)
r, out = captured(lambda: wn.run(env=dict(ENV, FORCE="1"), now=NOW, opener=opener_ok, decision=D, farm_name="CAET"))
check("FORCE=1 repeats the send", r["sent"] == 2)

wn.STATE.unlink(); calls.clear()
def opener_401(req, timeout=0): raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)
r, out = captured(lambda: wn.run(env=ENV, now=NOW, opener=opener_401, decision=D, farm_name="F"))
check("API refusing (401): counted as failed, no exception, state not written so tomorrow retries", r["sent"] == 0 and r["failed"] == 2 and not wn.STATE.exists() and "status 401" in out)
def opener_net(req, timeout=0): raise OSError("boom " + req.headers.get("Authorization", ""))
r, out = captured(lambda: wn.run(env=ENV, now=NOW, opener=opener_net, decision=D, farm_name="F"))
check("network error does not leak the token into the log", r["failed"] == 2 and "SECRET-TOKEN" not in out)
r, out = captured(lambda: wn.run(env=ENV, now=NOW, opener=opener_ok, decision=dec([Z(1, "NO_RUN")], date="2026-11-18"), farm_name="F"))
check("stale decision is never sent even when fully configured", r["sent"] == 0 and "not today" in r["skipped"])

print(f"\n{n - fail}/{n} checks passed")
sys.exit(1 if fail else 0)
