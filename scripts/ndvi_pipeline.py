#!/usr/bin/env python3
"""
Fully automatic NDVI pipeline — runs on GitHub Actions, no computer, no
browser, no human.

  Sentinel-2 (Google Earth Engine) -> cloud-masked NDVI -> coloured JPEG
  + per-zone NDVI/decision report -> ndvi_latest.jpeg, ndvi_status.json,
  ndvi_archive/NDVI_Dediapada_<YYYY-MM-DD>.jpeg

Auth: a service account. Provide the key JSON either as the env var
EE_SERVICE_ACCOUNT_KEY (GitHub secret) or a file path in EE_KEY_FILE.

Exit code is non-zero on any failure so the GitHub Actions run turns red
(GitHub emails the repo owner) instead of failing silently.
"""
import csv
import json
import os
import sys
import tempfile
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import requests

warnings.filterwarnings("ignore")

ROOT        = Path(__file__).resolve().parent.parent
ZONES_CSV   = Path(__file__).resolve().parent / "farm_zones.csv"
EE_PROJECT  = "agriculture-land-473011"
FARM        = {"lat": 21.62707, "lng": 73.5854}
BUFFER_DEG  = 0.018          # ~2 km around the farm point
WINDOW_DAYS = 30             # look-back window for the cloud-masked composite
MAX_SCENE_CLOUD = 85         # whole-scene ceiling; pixels are masked via SCL anyway
IST         = timezone(timedelta(hours=5, minutes=30))
REPO_RAW    = "https://raw.githubusercontent.com/rudraksh91/SatelliteSoilMonitoring/main"

# mean NDVI -> (status, action)
THRESHOLDS = [
    (0.25, "STRESSED", "Irrigate within 24 h"),
    (0.35, "MODERATE", "Irrigate in 2–3 days"),
    (0.50, "HEALTHY",  "Hold — normal schedule"),
    (1.01, "THRIVING", "No action needed"),
]


def log(msg):
    print(f"[{datetime.now(IST):%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def init_earth_engine():
    import ee
    key_json = os.environ.get("EE_SERVICE_ACCOUNT_KEY", "").strip()
    key_file = os.environ.get("EE_KEY_FILE", "").strip()
    tmp = None
    if key_json:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        tmp.write(key_json)
        tmp.close()
        key_file = tmp.name
    if not key_file:
        sys.exit("No Earth Engine credentials: set EE_SERVICE_ACCOUNT_KEY or EE_KEY_FILE")
    try:
        email = json.load(open(key_file))["client_email"]
        creds = ee.ServiceAccountCredentials(email, key_file)
        ee.Initialize(creds, project=EE_PROJECT)
    finally:
        if tmp:
            os.unlink(tmp.name)
    log(f"Earth Engine initialised as {email}")
    return ee


def fetch_ndvi(ee):
    """Return (composite_ndvi, nodata_mask, crs, transform, newest_scene_date, n_scenes, composite_dates)."""
    import rasterio

    region = ee.Geometry.Rectangle([
        FARM["lng"] - BUFFER_DEG, FARM["lat"] - BUFFER_DEG,
        FARM["lng"] + BUFFER_DEG, FARM["lat"] + BUFFER_DEG,
    ])
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=WINDOW_DAYS)

    def mask_clouds(img):
        scl = img.select("SCL")
        clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
        return img.updateMask(clear)

    col = (ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
           .filterBounds(region)
           .filterDate(start.strftime("%Y-%m-%d"), (end + timedelta(days=1)).strftime("%Y-%m-%d"))
           .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", MAX_SCENE_CLOUD))
           .map(mask_clouds))

    n = col.size().getInfo()
    if n == 0:
        raise RuntimeError(f"No Sentinel-2 scenes in the last {WINDOW_DAYS} days")
    stamps = col.aggregate_array("system:time_start").getInfo()
    newest = datetime.fromtimestamp(max(stamps) / 1000, timezone.utc).strftime("%Y-%m-%d")
    oldest = datetime.fromtimestamp(min(stamps) / 1000, timezone.utc).strftime("%Y-%m-%d")
    log(f"{n} scenes available: {oldest} -> {newest}")

    # Median composite: each pixel uses its clearest observations, so zone NDVI
    # stays valid through monsoon cloud cover.
    url = col.select(["B4", "B8"]).median().getDownloadURL(
        {"region": region, "scale": 10, "format": "GEO_TIFF"})
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    with rasterio.io.MemoryFile(r.content) as mf, mf.open() as src:
        crs, transform = src.crs, src.transform
        b04 = src.read(1, out_dtype="float32")
        b08 = src.read(2, out_dtype="float32")

    nodata = (b04 == 0) & (b08 == 0)
    ndvi = np.clip((b08 - b04) / (b08 + b04 + 1e-6), -1.0, 1.0)
    pct_valid = 100.0 * (~nodata).sum() / nodata.size
    log(f"Composite ready — {pct_valid:.1f}% valid pixels, shape {ndvi.shape}")
    if pct_valid < 5:
        raise RuntimeError(f"Composite almost empty ({pct_valid:.1f}% valid) — refusing to publish")
    return ndvi, nodata, crs, transform, newest, n


def render_jpeg(ndvi, nodata, path):
    import matplotlib
    import matplotlib.colors as mcolors
    from PIL import Image
    norm = mcolors.Normalize(vmin=-0.2, vmax=0.8)
    rgb = (matplotlib.colormaps["RdYlGn"](norm(ndvi))[:, :, :3] * 255).astype(np.uint8)
    rgb[nodata] = [48, 48, 48]
    Image.fromarray(rgb, mode="RGB").save(path, "JPEG", quality=92)


def get_weather():
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": FARM["lat"], "longitude": FARM["lng"],
            "hourly": ["evapotranspiration", "precipitation", "vapour_pressure_deficit"],
            "forecast_days": 4, "timezone": "Asia/Kolkata"}, timeout=20)
        r.raise_for_status()
        h = r.json()["hourly"]
        return {
            "et0_mm_day": round(sum(v for v in h["evapotranspiration"][:24] if v), 2),
            "rain_3d_mm": round(sum(v for v in h["precipitation"][:72] if v), 2),
            "vpd_kpa":    round(h["vapour_pressure_deficit"][0] or 0.0, 3),
        }
    except Exception as exc:  # weather only refines the advice; don't fail the run
        log(f"WARNING weather fetch failed: {exc}")
        return {"et0_mm_day": 0, "rain_3d_mm": 0, "vpd_kpa": 0}


def analyse_zones(ndvi, crs, transform, weather):
    import rasterio.io
    from rasterio.mask import mask
    from rasterio.warp import transform_geom
    from shapely.geometry import shape, mapping

    zones = []
    with open(ZONES_CSV) as fh:
        for row in csv.DictReader(fh):
            zones.append({"name": row["name"], "poly": shape(json.loads(row[".geo"])).buffer(0.000072)})

    field = np.where(ndvi <= -0.5, -9999.0, ndvi).astype("float32")
    out = []
    profile = {"driver": "GTiff", "dtype": "float32", "width": field.shape[1],
               "height": field.shape[0], "count": 1, "crs": crs, "transform": transform}
    with rasterio.io.MemoryFile() as mf, mf.open(**profile) as ds:
        ds.write(field, 1)
        for z in zones:
            geom = transform_geom("EPSG:4326", str(crs), mapping(z["poly"]))
            try:
                masked, _ = mask(ds, [geom], crop=True, nodata=-9999, filled=True)
                vals = masked[0].flatten()
                valid = vals[(vals > -0.5) & (vals < 1.0)]
                mean, px = (float(valid.mean()) if len(valid) else 0.0), len(valid)
            except Exception:
                mean, px = 0.0, 0

            status, action = "UNKNOWN", "—"
            for limit, s, a in THRESHOLDS:
                if mean < limit:
                    status, action = s, a
                    break
            rain, et0, vpd = weather["rain_3d_mm"], weather["et0_mm_day"], weather["vpd_kpa"]
            if status == "STRESSED" and rain >= 15:
                status, action = "HOLD", f"Rain forecast {rain:.1f} mm — skip irrigation"
            elif status in ("HEALTHY", "THRIVING") and et0 > 8:
                status, action = "MODERATE", f"ET0 high ({et0:.1f} mm/d) — irrigate in 2 days"
            elif status == "MODERATE" and vpd > 3.0:
                status, action = "STRESSED", f"VPD critical ({vpd:.1f} kPa) — irrigate now"
            log(f"{z['name']}: NDVI={mean:.4f} [{px}px] -> {status} — {action}")
            out.append({"zone": z["name"], "ndvi": round(mean, 4), "pixels": px,
                        "status": status, "action": action})
    return out


def main():
    out_dir = Path(os.environ.get("NDVI_OUT_DIR", ROOT))
    (out_dir / "ndvi_archive").mkdir(parents=True, exist_ok=True)
    now = datetime.now(IST)
    tag = now.strftime("%Y-%m-%d")

    ee = init_earth_engine()
    ndvi, nodata, crs, transform, scene_date, n_scenes = fetch_ndvi(ee)
    weather = get_weather()
    zones = analyse_zones(ndvi, crs, transform, weather)

    jpeg_name = f"NDVI_Dediapada_{tag}.jpeg"
    render_jpeg(ndvi, nodata, out_dir / "ndvi_latest.jpeg")
    render_jpeg(ndvi, nodata, out_dir / "ndvi_archive" / jpeg_name)

    age = (now.date() - datetime.strptime(scene_date, "%Y-%m-%d").date()).days
    status = {
        "updated": now.replace(tzinfo=None).isoformat(),
        "scene_date": scene_date,
        "scene_age_days": age,
        "scenes_in_composite": n_scenes,
        "jpeg_name": jpeg_name,
        "jpeg_id": f"{tag}_{scene_date}",   # changes each run; the website uses it to detect new data
        "jpeg_url": f"{REPO_RAW}/ndvi_latest.jpeg",
        "zones": zones,
        "weather": weather,
    }
    (out_dir / "ndvi_status.json").write_text(json.dumps(status, indent=2, ensure_ascii=False) + "\n")

    # Append-only history (V2): one row per zone per run, for validation and tuning later.
    hist = out_dir / "ndvi_archive" / "ndvi_history.csv"
    new_file = not hist.exists()
    with open(hist, "a", newline="") as fh:
        w = csv.writer(fh)
        if new_file:
            w.writerow(["run_date", "scene_date", "scene_age_days", "scenes_in_composite", "zone",
                        "ndvi", "pixels", "status", "et0_mm_day", "rain_3d_mm", "vpd_kpa"])
        for z in zones:
            w.writerow([tag, scene_date, age, n_scenes, z["zone"], z["ndvi"], z["pixels"],
                        z["status"], weather["et0_mm_day"], weather["rain_3d_mm"], weather["vpd_kpa"]])
    if age > 20:   # M6: stale imagery warning, visible on the Actions run page
        print(f"::warning::Newest clean Sentinel-2 scene is {age} days old; the dashboard uses a stage Kc until a clearer pass arrives")
    log(f"Done — scene {scene_date} ({age} d old), {len(zones)} zones written to {out_dir}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"FAILED: {exc}")
        raise
