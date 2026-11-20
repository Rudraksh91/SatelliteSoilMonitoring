#!/usr/bin/env python3
"""AgriSat daily job (GitHub Actions) — the single place decisions are made.

  Sentinel-2 farm NDVI (Earth Engine) + Open-Meteo weather + confirmed irrigation + farmer events
    -> FAO-56 depletion per zone -> decision + run time -> data/decision.json, data/state.json, data/log.csv

The Pi gateway only EXECUTES data/decision.json (and stops if it is older than 48 h).
Config: config/farm_config.json.  Alerts: Telegram (TELEGRAM_TOKEN + TELEGRAM_CHAT_ID) or the Actions log.
"""
import csv
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import agrisat_model as M

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("AGRISAT_DATA_DIR", ROOT / "data"))
CONFIG = ROOT / "config" / "farm_config.json"
IST = timezone(timedelta(hours=5, minutes=30))
LOG_COLUMNS = ["date", "zone", "rain_mm", "et0_mm", "ndvi_raw", "ndvi_interp", "ndvi_stale", "kc", "ks",
               "etc_mm", "dr_mm", "dr_pct", "raw_mm", "taw_mm", "irrigation_mm", "manual_events",
               "stress_flag", "decision_for", "decision", "reason", "run_min", "sat_soil_moisture", "note"]


def log(msg):
    print(f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ── alerts (D4) ───────────────────────────────────────────────────────
def send_alert(message, alerts_out=None):
    """Telegram when configured, always visible in the Actions log. Says what failed and what happens instead."""
    print(f"::warning::{message}", flush=True)
    if alerts_out is not None:
        alerts_out.append(message)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"AgriSat: {message}"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15)
        except Exception as exc:
            print(f"::warning::Telegram alert failed: {exc}", flush=True)


# ── inputs ────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def fetch_weather(cfg, past_days=7, forecast_days=3):
    """Open-Meteo daily ET0 + rain: past days feed the balance, forecast days feed the skip rule (D2)."""
    q = urllib.parse.urlencode({
        "latitude": cfg["farm"]["lat"], "longitude": cfg["farm"]["lng"],
        "daily": "precipitation_sum,et0_fao_evapotranspiration",
        "past_days": past_days, "forecast_days": forecast_days, "timezone": cfg["farm"]["timezone"]}, safe=",")
    with urllib.request.urlopen("https://api.open-meteo.com/v1/forecast?" + q, timeout=30) as r:
        raw = json.load(r)
    d = raw["daily"]
    days = {date.fromisoformat(t): {"rain": d["precipitation_sum"][i] or 0.0,
                                    "et0": d["et0_fao_evapotranspiration"][i] or 0.0}
            for i, t in enumerate(d["time"])}
    return days, raw


def fetch_ndvi_series(ee, cfg, zones_geojson, days_back=120):
    """Clean-scene NDVI per date: median over the (inward-shrunk) farm polygon, SCL-masked.
    Scenes with fewer than min_clean_pixels clean pixels are skipped."""
    ncfg = cfg["ndvi"]
    geom = ee.Geometry(zones_geojson)
    try:
        shrunk = geom.buffer(-ncfg["shrink_m"])
        if shrunk.area(1).getInfo() < 100:            # too small to shrink (campus zones are tiny)
            shrunk = geom
    except Exception:
        shrunk = geom
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days_back)

    def mask(img):
        scl = img.select("SCL")
        clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10)).And(scl.neq(11))
        return img.updateMask(clear)

    def per_scene(img):
        ndvi = img.normalizedDifference(["B8", "B4"]).rename("NDVI")
        stats = ndvi.reduceRegion(ee.Reducer.median().combine(ee.Reducer.count(), sharedInputs=True),
                                  shrunk, 10, maxPixels=1e8)
        return ee.Feature(None, {"date": img.date().format("YYYY-MM-dd"),
                                 "ndvi": stats.get("NDVI_median"), "n": stats.get("NDVI_count")})

    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED").filterBounds(shrunk)
           .filterDate(start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d"))
           .map(mask).map(per_scene))
    feats = col.getInfo()["features"]
    out = {}
    for f in feats:
        p = f["properties"]
        if p.get("ndvi") is not None and (p.get("n") or 0) >= ncfg["min_clean_pixels"]:
            out[date.fromisoformat(p["date"])] = float(p["ndvi"])
    return out


def load_events(cfg):
    """Confirmed runs (from the gateway) and farmer entries (CSV), per (date, zone)."""
    confirmed = {}
    for e in load_json(DATA / "confirmed_runs.json", []):
        k = (date.fromisoformat(e["date"]), int(e["zone"]))
        confirmed[k] = confirmed.get(k, 0.0) + float(e["minutes"])
    irrig_min, rain_override, notes = {}, {}, {}
    url = cfg["inputs"].get("farmer_events_csv_url")
    if url:
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                rows = list(csv.DictReader(io.StringIO(r.read().decode("utf-8"))))
            zone_ids = [z["id"] for z in cfg["zones"]]
            for row in rows:
                d = date.fromisoformat(row["date"].strip())
                zs = [int(row["zone"])] if (row.get("zone") or "").strip() else zone_ids
                val = float(row["value"])
                for z in zs:
                    if row["type"].strip() == "irrigated_min":
                        irrig_min[(d, z)] = irrig_min.get((d, z), 0.0) + val
                    elif row["type"].strip() == "rain_mm":
                        rain_override[d] = val
                    notes.setdefault((d, z), []).append(f'{row["type"].strip()}={val:g}')
        except Exception as exc:
            send_alert(f"Farmer events could not be read ({exc}); balance runs without them.")
    return confirmed, irrig_min, rain_override, notes


# ── core ──────────────────────────────────────────────────────────────
def run(cfg, state, weather, ndvi_series, events, today):
    """Pure orchestration step (no I/O): returns (new_state, decision, log_rows, alerts)."""
    alerts, rows = [], []
    soil, crop, irr, ncfg = cfg["soil"], cfg["crop"], cfg["irrigation"], cfg["ndvi"]
    taw = M.taw_mm(soil["theta_fc"], soil["theta_wp"], soil["root_depth_m"])
    p = crop["p"]
    raw = M.raw_mm(p, taw)
    confirmed, irrig_min, rain_override, notes = events
    zones = cfg["zones"]

    series = {date.fromisoformat(k): v for k, v in state.get("ndvi_series", {}).items()}
    series.update(ndvi_series)
    series = {d: v for d, v in series.items() if (today - d).days <= 200}

    # which days to balance: after the last processed day, up to yesterday
    yesterday = today - timedelta(days=1)
    last = date.fromisoformat(state["date"]) if state.get("date") else None
    if last is None:
        start = date.fromisoformat(cfg["start"]["date"]) if cfg["start"].get("date") else yesterday
        dr = {z["id"]: float(cfg["start"].get("depletion_mm", 0.0)) for z in zones}
        anchor_missing = cfg["start"].get("date") is None
    else:
        start = last + timedelta(days=1)
        dr = {int(k): v for k, v in state["dr"].items()}
        anchor_missing = False
    first_available = min(weather)
    if start < first_available:
        alerts.append(f"Weather history only reaches {first_available}; days {start}..{first_available - timedelta(days=1)} "
                      "were not balanced (job did not run). Check the soil with a sample and set config.start.")
        start = first_available

    day, last_kc, last_ndvi, last_stale = start, None, None, False
    while day <= yesterday:
        w = weather.get(day)
        if w is None:
            alerts.append(f"No weather for {day}; balance stopped before it.")
            break
        kc, ndvi, stale = M.daily_kc(series, day, crop, ncfg["stale_days"])
        rain = rain_override.get(day, w["rain"])
        for z in zones:
            zid = z["id"]
            fl = M.zone_flow(z)
            mins = confirmed.get((day, zid), 0.0) + irrig_min.get((day, zid), 0.0)
            depth = M.irrigation_depth_from_minutes(mins, irr["efficiency"], *fl) if (mins and fl) else 0.0
            dr[zid], etc, ks, _ = M.update_depletion(dr[zid], w["et0"], kc, rain, depth, taw, p,
                                                     irr["effective_rain"]["min_mm"], irr["effective_rain"]["fraction"])
            rows.append({"date": day.isoformat(), "zone": zid, "rain_mm": round(rain, 2), "et0_mm": round(w["et0"], 2),
                         "ndvi_raw": round(series[day], 4) if day in series else "",
                         "ndvi_interp": round(ndvi, 4) if ndvi is not None else "", "ndvi_stale": int(stale),
                         "kc": round(kc, 3), "ks": round(ks, 3), "etc_mm": round(etc, 2), "dr_mm": round(dr[zid], 2),
                         "dr_pct": round(100 * dr[zid] / taw, 1), "raw_mm": round(raw, 1), "taw_mm": round(taw, 1),
                         "irrigation_mm": round(depth, 2), "manual_events": ";".join(notes.get((day, zid), [])),
                         "sat_soil_moisture": ""})
        last_kc, last_ndvi, last_stale = kc, ndvi, stale
        day += timedelta(days=1)
    balanced_to = day - timedelta(days=1)

    # alerts: stale imagery / unconfirmed runs
    clean_dates = [d for d in series if d <= today]
    age = (today - max(clean_dates)).days if clean_dates else None
    if age is None or age >= ncfg["alert_days"]:
        alerts.append(f"No clean Sentinel-2 scene for {age if age is not None else 'ever'} days — "
                      f"using {'stage curve' if (age is None or age > ncfg['stale_days']) else 'held NDVI'}.")
    prev = state.get("last_decision") or {}
    for zid, mins in (prev.get("runs") or {}).items():
        if mins and not confirmed.get((date.fromisoformat(prev["date"]), int(zid))):
            alerts.append(f"Zone {zid}: {mins:.0f} min was commanded on {prev['date']} but never confirmed — "
                          "counted as 0 irrigation.")

    # decision for today
    fwd = sum(weather[today + timedelta(days=i)]["rain"] for i in range(0, 2) if (today + timedelta(days=i)) in weather)
    recent_rain = sum(weather[d]["rain"] for d in weather if d <= yesterday and (today - d).days <= 14)
    senescent = bool(crop.get("senescence_dap") and crop.get("planting_date") and
                     (today - date.fromisoformat(crop["planting_date"])).days >= crop["senescence_dap"])
    stress = M.ndvi_stress_flag(series, recent_rain, ncfg["stress_noise"], ncfg["stress_scenes"], senescent=senescent)
    missing = []
    if not soil.get("calibrated"): missing.append("soil.calibrated (theta_fc/theta_wp are estimates)")
    if not crop.get("confirmed"): missing.append("crop.confirmed (Kcb/p/Zr are estimates)")
    if anchor_missing: missing.append("start (no soil-sample anchor: depletion assumed 0 at first run)")
    if any(not (z.get("area_m2") and z.get("flow_lph")) for z in zones):
        missing.append("zones.area_m2 + flow_lph (using the 3 mm/h rate estimate)")

    dec_zones, sequence, runs = [], [], {}
    for z in zones:
        zid = z["id"]; d_mm = dr.get(zid, 0.0)
        decision, reason = M.decide(d_mm, taw, p, stress, fwd, irr["rain_skip_mm"], irr["stress_trigger_fraction_of_raw"])
        minutes = 0.0
        fl = M.zone_flow(z)
        if decision == "IRRIGATE":
            if fl is None:
                minutes, reason = None, reason + " — NO run time: set area_m2+flow_lph or rate_mm_hr"
            else:
                minutes = M.run_time_minutes(d_mm, irr["efficiency"], fl[0], fl[1], irr["max_run_min"], irr["min_run_min"])
                minutes = min(minutes, irr["max_daily_min"])
                if minutes == 0:
                    decision, reason = "NO_RUN", reason + f" — run under {irr['min_run_min']} min, carried over"
        if minutes:
            sequence.append(zid); runs[str(zid)] = round(minutes, 1)
        dec_zones.append({"id": zid, "name": z["name"], "dr_mm": round(d_mm, 2), "raw_mm": round(raw, 1),
                          "taw_mm": round(taw, 1), "dr_pct": round(100 * d_mm / taw, 1),
                          "decision": decision, "reason": reason,
                          "run_minutes": None if minutes is None else round(minutes, 1)})
        for r in reversed(rows):
            if r["zone"] == zid and "decision" not in r:
                r.update(stress_flag=int(stress), decision_for=today.isoformat(), decision=decision,
                         reason=reason, run_min="" if minutes is None else round(minutes, 1), note="")
                break
    now = datetime.now(timezone.utc)
    decision_doc = {
        "generated_at": now.isoformat(timespec="seconds"), "valid_until": (now + timedelta(hours=48)).isoformat(timespec="seconds"),
        "date": today.isoformat(), "calibrated": not missing, "missing_inputs": missing,
        "farm": {"ndvi": None if last_ndvi is None else round(last_ndvi, 4), "kc": None if last_kc is None else round(last_kc, 3),
                 "ndvi_stale": bool(last_stale), "ndvi_age_days": age, "stress_flag": stress,
                 "forecast_rain_mm_48h": round(fwd, 1), "balanced_to": balanced_to.isoformat()},
        "limits": {"min_run_min": irr["min_run_min"], "max_run_min": irr["max_run_min"], "max_daily_min": irr["max_daily_min"]},
        "soil": {"theta_fc": soil["theta_fc"], "theta_wp": soil["theta_wp"], "root_depth_m": soil["root_depth_m"], "p": p},
        "zones": dec_zones, "sequence": sequence}
    new_state = {"updated": now.isoformat(timespec="seconds"), "date": balanced_to.isoformat(),
                 "dr": {str(k): round(v, 3) for k, v in dr.items()},
                 "ndvi_series": {d.isoformat(): round(v, 4) for d, v in sorted(series.items())},
                 "last_scene_date": max(clean_dates).isoformat() if clean_dates else None,
                 "ndvi_stale": bool(last_stale), "last_decision": {"date": today.isoformat(), "runs": runs}}
    return new_state, decision_doc, rows, alerts


# ── I/O wrapper ───────────────────────────────────────────────────────
def append_log(rows):
    DATA.mkdir(parents=True, exist_ok=True)
    path = DATA / "log.csv"
    existing = set()
    if path.exists():
        with open(path, newline="") as fh:
            existing = {(r["date"], r["zone"]) for r in csv.DictReader(fh)}
    new = [r for r in rows if (r["date"], str(r["zone"])) not in existing]   # append-only, never overwrite a day
    with open(path, "a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG_COLUMNS)
        if not existing and not path.stat().st_size:
            w.writeheader()
        for r in new:
            w.writerow({c: r.get(c, "") for c in LOG_COLUMNS})
    return len(new)


def main():
    cfg = load_json(CONFIG, None)
    if cfg is None:
        sys.exit(f"Missing/invalid {CONFIG}")
    today = datetime.now(IST).date()
    DATA.mkdir(parents=True, exist_ok=True)
    state = load_json(DATA / "state.json", {})
    try:
        weather, raw = fetch_weather(cfg)
        (DATA / "raw").mkdir(exist_ok=True)
        (DATA / "raw" / f"weather_{today}.json").write_text(json.dumps(raw))
    except Exception as exc:
        send_alert(f"Weather call failed ({exc}); no new decision was written — the gateway keeps the previous one until it is 48 h old.")
        raise
    ndvi_series = {}
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import ndvi_pipeline as P
        ee = P.init_earth_engine()
        zones_geojson = P.farm_geojson()
        ndvi_series = fetch_ndvi_series(ee, cfg, zones_geojson)
        log(f"{len(ndvi_series)} clean scenes in the last 120 days")
    except Exception as exc:
        send_alert(f"Earth Engine NDVI failed ({exc}); using the stored NDVI history / stage curve.")
    new_state, decision, rows, alerts = run(cfg, state, weather, ndvi_series, load_events(cfg), today)
    for a in alerts:
        send_alert(a)
    n = append_log(rows)
    (DATA / "state.json").write_text(json.dumps(new_state, indent=2) + "\n")
    (DATA / "decision.json").write_text(json.dumps(decision, indent=2) + "\n")
    log(f"decision for {today}: {[(z['id'], z['decision'], z['run_minutes']) for z in decision['zones']]}  "
        f"calibrated={decision['calibrated']}  log rows +{n}")


if __name__ == "__main__":
    main()
