"""Automatic ESTIMATE of field capacity / wilting point from open soil data (no human input).

SoilGrids 2.0 (ISRIC, 250 m) sand / clay / soil organic carbon at the farm point, depth-weighted down to the
root depth, then the Saxton & Rawls (2006) pedotransfer equations. The project's reports explicitly allow this
as a clearly-marked ESTIMATE until lab values exist (flagged: soil.calibrated stays false).
"""
import json
import urllib.request

LAYERS = [("0-5cm", 5), ("5-15cm", 15), ("15-30cm", 30), ("30-60cm", 60)]   # (label, bottom depth in cm)


def saxton_rawls(sand, clay, om_pct):
    """sand, clay as fractions (0-1), organic matter in % -> (theta_fc [33 kPa], theta_wp [1500 kPa]) in m3/m3."""
    s, c, om = sand, clay, om_pct
    t1500t = (-0.024 * s + 0.487 * c + 0.006 * om + 0.005 * s * om - 0.013 * c * om + 0.068 * s * c + 0.031)
    t1500 = t1500t + (0.14 * t1500t - 0.02)
    t33t = (-0.251 * s + 0.195 * c + 0.011 * om + 0.006 * s * om - 0.027 * c * om + 0.452 * s * c + 0.299)
    t33 = t33t + (1.283 * t33t ** 2 - 0.374 * t33t - 0.015)
    return t33, t1500


def depth_weighted(values_by_label, root_depth_m):
    """Thickness-weighted mean over the part of each layer that lies inside the root zone.
    SoilGrids returns None where it has no data (built-up, water): such layers are skipped, never counted as 0."""
    root_cm, top, total, wsum = root_depth_m * 100.0, 0, 0.0, 0.0
    for label, bottom in LAYERS:               # layer bottoms: 5, 15, 30, 60 cm
        v = values_by_label.get(label)
        thick = max(0.0, min(bottom, root_cm) - top)
        if thick > 0 and v is not None:
            total += v * thick
            wsum += thick
        top = bottom
    if wsum == 0:
        raise ValueError("SoilGrids has no soil data for this location")
    return total / wsum


def fetch_soilgrids(lat, lng, timeout=40):
    q = ("https://rest.isric.org/soilgrids/v2.0/properties/query?"
         f"lon={lng}&lat={lat}&property=sand&property=clay&property=soc"
         + "".join(f"&depth={l}" for l, _ in LAYERS) + "&value=mean")
    with urllib.request.urlopen(q, timeout=timeout) as r:
        doc = json.load(r)
    out = {}
    for layer in doc["properties"]["layers"]:
        out[layer["name"]] = {d["label"]: d["values"]["mean"] for d in layer["depths"]}
    return out


def estimate(lat, lng, root_depth_m, fetch=fetch_soilgrids):
    """Returns {'theta_fc','theta_wp','sand','clay','om_pct','source'} or raises."""
    raw = fetch(lat, lng)
    sand = depth_weighted(raw["sand"], root_depth_m) / 1000.0           # g/kg -> fraction
    clay = depth_weighted(raw["clay"], root_depth_m) / 1000.0
    soc_gkg = depth_weighted(raw["soc"], root_depth_m) / 10.0            # dg/kg -> g/kg
    om_pct = min(8.0, soc_gkg * 1.724 / 10.0)                            # SOC -> organic matter, capped at the SR2006 range
    if sand + clay < 0.05 or sand + clay > 1.0:
        raise ValueError("SoilGrids has no soil data for this location")
    fc, wp = saxton_rawls(sand, clay, om_pct)
    if not (fc > wp + 0.03) or fc > 0.55 or wp < 0.01 or fc - wp > 0.30:
        raise ValueError("soil estimate outside the physical range")   # reject nonsense instead of modelling it
    return {"theta_fc": round(fc, 3), "theta_wp": round(wp, 3), "sand": round(sand, 3), "clay": round(clay, 3),
            "om_pct": round(om_pct, 2),
            "source": "ESTIMATE: SoilGrids 250 m + Saxton-Rawls 2006 (replace with core/lab values)"}
