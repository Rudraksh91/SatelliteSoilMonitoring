"""Unit tests for scripts/agrisat_model.py and the pure core of scripts/run_daily.py.
Run: python3 tests/test_agrisat_model.py   (no network, no Earth Engine)"""
import sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import agrisat_model as M
import run_daily as R

n = 0
def check(name, cond):
    global n; n += 1
    print(("ok   " if cond else "FAIL ") + name)
    if not cond: sys.exit(1)
def near(a, b, t=1e-6): return abs(a - b) < t

TAW, P = 60.0, 0.5       # theta_fc .28, theta_wp .13, Zr .4  ->  60 mm;  RAW = 30 mm
# ── the automatic tests listed in the Solutions Report (V3) ──
check("TAW = 1000(fc-wp)Zr = 60", near(M.taw_mm(0.28, 0.13, 0.40), 60))
check("no ET and no rain leaves depletion unchanged", near(M.update_depletion(22, 0, 1, 0, 0, TAW, P)[0], 22))
check("very large rain brings depletion to 0", M.update_depletion(40, 3, 1, 200, 0, TAW, P)[0] == 0)
check("irrigation larger than depletion does not go negative", M.update_depletion(10, 0, 1, 0, 50, TAW, P)[0] == 0)
check("depletion never exceeds TAW", M.update_depletion(59.9, 50, 1.2, 0, 0, TAW, P)[0] == TAW)
check("Ks = 1 below RAW", M.stress_coefficient(29.9, TAW, P) == 1.0 and M.stress_coefficient(30, TAW, P) == 1.0)
check("Ks falls above RAW: Dr=45 -> (60-45)/(0.5*60)=0.5", near(M.stress_coefficient(45, TAW, P), 0.5))
check("Ks reaches 0 at TAW", near(M.stress_coefficient(60, TAW, P), 0.0))
# ── hand calculation, one full day ──
dr, etc, ks, peff = M.update_depletion(20, 5.0, 0.8, 12.0, 2.0, TAW, P)
check("hand calc: ETc=4.0, Peff=9.6 -> Dr=20+4-9.6-2=12.4", near(etc, 4.0) and near(peff, 9.6) and near(dr, 12.4))
check("rain < 5 mm counts as 0", M.effective_rain(4.9) == 0 and near(M.effective_rain(5.0), 4.0))
# ── Kc from NDVI (blueprint: fc=(NDVI-0.15)/0.70) ──
check("fc clamps 0..1", M.canopy_fraction(0.0, .15, .85) == 0 and M.canopy_fraction(0.95, .15, .85) == 1)
check("Kc = kcb_min at bare soil, kcb_max at full canopy",
      near(M.kc_from_ndvi(.15, .3, 1.15, .15, .85), .3) and near(M.kc_from_ndvi(.85, .3, 1.15, .15, .85), 1.15))
check("Kc mid canopy", near(M.kc_from_ndvi(.5, .3, 1.15, .15, .85), .725))
crop = {"kcb_min": .3, "kcb_max": 1.15, "ndvi_soil": .15, "ndvi_full": .85, "planting_date": "2026-10-01",
        "stage_kc_by_dap": [[0, .4], [30, .7], [60, 1.0]]}
d0 = date(2026, 11, 20)
series = {d0 - timedelta(days=10): 0.30, d0 + timedelta(days=0): 0.60}
v, age = M.interpolate_ndvi(series, d0 - timedelta(days=5))
check("linear interpolation between clean scenes", near(v, 0.45) and age == 5)
kc, ndvi, stale = M.daily_kc({d0 - timedelta(days=21): 0.5}, d0, crop, 20)
check("last clean scene 21 d old -> stage curve (DAP 50 -> .9), stale flag", stale and near(kc, 0.7 + (1.0 - 0.7) * 20 / 30))
kc, ndvi, stale = M.daily_kc({d0 - timedelta(days=3): 0.5}, d0, crop, 20)
check("fresh scene -> NDVI Kc, not stale", (not stale) and near(kc, M.kc_from_ndvi(.5, .3, 1.15, .15, .85)))
# ── stress flag (M8) ──
s3 = {date(2026, 11, 1): .60, date(2026, 11, 8): .52, date(2026, 11, 15): .44}
check("falling across 3 scenes, low rain -> flag", M.ndvi_stress_flag(s3, 5, 0.03))
check("fall within noise -> no flag", not M.ndvi_stress_flag({date(2026,11,1): .60, date(2026,11,8): .59, date(2026,11,15): .58}, 5, 0.03))
check("wet period -> no flag", not M.ndvi_stress_flag(s3, 80, 0.03))
check("senescence -> no flag", not M.ndvi_stress_flag(s3, 5, 0.03, senescent=True))
check("fewer than 3 scenes -> no flag", not M.ndvi_stress_flag({date(2026,11,1): .6, date(2026,11,8): .4}, 5, .03))
# ── decision rules (blueprint flow) ──
check("rain forecast > 10 mm skips", M.decide(40, TAW, P, False, 12, 10)[0] == "SKIP_RAIN")
check("Dr >= RAW irrigates", M.decide(30, TAW, P, False, 0, 10)[0] == "IRRIGATE")
check("Dr < RAW and no stress -> no run", M.decide(29, TAW, P, False, 0, 10)[0] == "NO_RUN")
check("stress flag + Dr > 0.7 RAW irrigates", M.decide(22, TAW, P, True, 0, 10)[0] == "IRRIGATE")
check("stress flag but Dr <= 0.7 RAW does not (acts only with depletion)", M.decide(20, TAW, P, True, 0, 10)[0] == "NO_RUN")
# ── run time (M12) ──
check("run time: 22 mm net / 0.88 = 25 mm gross on 100 m2 at 2000 L/h = 75 min",
      near(M.run_time_minutes(22, 0.88, 100, 2000, 120, 10), 75.0))
check("clamped to max", M.run_time_minutes(22, 0.88, 100, 500, 120, 10) == 120)
check("below minimum -> 0 (carry over)", M.run_time_minutes(0.5, 0.88, 100, 2000, 120, 10) == 0)
check("confirmed minutes -> depth round trip", near(M.irrigation_depth_from_minutes(75, 0.88, 100, 2000), 22.0))
check("rate_mm_hr zone gives area-free minutes: 24 mm net, 3 mm/h, Ea .88 -> 545 min before clamp",
      near(M.run_time_minutes(24, 0.88, *M.zone_flow({"rate_mm_hr": 3.0}), 10_000, 10), 60 * 24 / 0.88 / 3, 1e-6))

# ── daily orchestration (pure core of run_daily) ──
import json
cfg = json.loads((Path(__file__).resolve().parent.parent / "config" / "farm_config.json").read_text())
cfg["zones"] = [{"id": 1, "name": "Z1", "area_m2": 100, "flow_lph": 2000}, {"id": 2, "name": "Z2", "area_m2": None, "flow_lph": None, "rate_mm_hr": 3.0}]
cfg["soil"]["calibrated"] = cfg["crop"]["confirmed"] = True
cfg["start"] = {"date": "2026-11-13", "depletion_mm": 0.0}
today = date(2026, 11, 20)
wx = {today - timedelta(days=i): {"rain": 0.0, "et0": 7.0} for i in range(7, -4, -1)}
st, dec, rows, alerts = R.run(cfg, {}, wx, {today - timedelta(days=2): 0.5}, ({}, {}, {}, {}), today)
check("7 dry days (ET0 7 mm, Kc .725) -> 35 mm >= RAW 30 -> zone 1 irrigates",
      dec["zones"][0]["decision"] == "IRRIGATE" and dec["zones"][0]["run_minutes"] > 0)
check("zone 2 (rate estimate only) still gets a run time from rate_mm_hr", dec["zones"][1]["run_minutes"] is not None)
check("zone 2 has no area/flow -> decision.calibrated=false and says why", dec["calibrated"] is False and any("zones.area_m2" in m for m in dec["missing_inputs"]))
check("state.date = yesterday, one log row per zone per balanced day", st["date"] == "2026-11-19" and len(rows) == 7 * 2)
check("48 h validity window written", dec["valid_until"] > dec["generated_at"])
# unconfirmed run alert + counted as zero irrigation
st2 = dict(st); st2["last_decision"] = {"date": "2026-11-19", "runs": {"1": 60.0}}
_, _, _, al = R.run(cfg, st2, wx, {today - timedelta(days=2): 0.5}, ({}, {}, {}, {}), today)
check("commanded-but-unconfirmed run raises an alert", any("never confirmed" in a for a in al))
# confirmed minutes reduce depletion the next day
conf = {(today - timedelta(days=1), 1): 75.0}
st3 = {"date": "2026-11-18", "dr": {"1": 40.0, "2": 40.0}, "ndvi_series": {}}
s_a, *_ = R.run(cfg, st3, wx, {}, ({}, {}, {}, {}), today)
s_b, *_ = R.run(cfg, st3, wx, {}, (conf, {}, {}, {}), today)
check("confirmed irrigation lowers zone 1 depletion", s_b["dr"]["1"] < s_a["dr"]["1"] - 5)
check("...but not zone 2", near(s_b["dr"]["2"], s_a["dr"]["2"]))
# rain skip
wx2 = dict(wx); wx2[today + timedelta(days=1)] = {"rain": 25.0, "et0": 3.0}
_, dec2, _, _ = R.run(cfg, {}, wx2, {}, ({}, {}, {}, {}), today)
check("25 mm forecast tomorrow -> SKIP_RAIN", dec2["zones"][0]["decision"] == "SKIP_RAIN" and dec2["sequence"] == [])
print(f"\n{n} checks passed")
