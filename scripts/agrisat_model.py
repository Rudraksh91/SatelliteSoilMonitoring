"""AgriSat water-balance model — pure functions, no network or file access (unit-tested).

Implements the design in the project's Solutions Report / Architecture Blueprint:
  Dr,i = clamp(Dr,i-1 + ETc - Peff - I, 0, TAW)       ETc = ET0 * Kc * Ks
  Ks   = 1 (Dr <= RAW) else max(0, (TAW - Dr) / ((1 - p) * TAW))
  Peff = 0 if P < 5 mm else 0.8 * P
  Kc   = Kcb_min + (Kcb_max - Kcb_min) * fc,  fc = clamp((NDVI - NDVIsoil)/(NDVIfull - NDVIsoil), 0, 1)
  run  = 60 * (Dr/Ea * area) / Q  minutes, clamped to [t_min, t_max]  (below t_min -> 0, carry over)
"""
from datetime import date, datetime, timedelta


# ── storage ───────────────────────────────────────────────────────────
def taw_mm(theta_fc, theta_wp, root_depth_m):
    return 1000.0 * (theta_fc - theta_wp) * root_depth_m


def raw_mm(p, taw):
    return p * taw


def stress_coefficient(dr, taw, p):
    raw = p * taw
    if dr <= raw:
        return 1.0
    return max(0.0, (taw - dr) / ((1.0 - p) * taw))


def effective_rain(rain_mm, min_mm=5.0, fraction=0.80):
    return 0.0 if rain_mm < min_mm else fraction * rain_mm


def update_depletion(dr_prev, et0, kc, rain, irrigation, taw, p, rain_min=5.0, rain_fraction=0.80):
    """One daily step. Returns (dr, etc, ks, peff). 0 = field capacity, taw = wilting point."""
    ks = stress_coefficient(dr_prev, taw, p)
    etc = et0 * kc * ks
    peff = effective_rain(rain, rain_min, rain_fraction)
    dr = min(max(dr_prev + etc - peff - irrigation, 0.0), taw)
    return dr, etc, ks, peff


# ── crop coefficient ──────────────────────────────────────────────────
def canopy_fraction(ndvi, ndvi_soil, ndvi_full):
    return min(1.0, max(0.0, (ndvi - ndvi_soil) / (ndvi_full - ndvi_soil)))


def kc_from_ndvi(ndvi, kcb_min, kcb_max, ndvi_soil, ndvi_full):
    return kcb_min + (kcb_max - kcb_min) * canopy_fraction(ndvi, ndvi_soil, ndvi_full)


def stage_kc(days_after_planting, curve):
    """Linear interpolation over [[dap, kc], ...] (sorted); clamps outside the table."""
    curve = sorted(curve)
    if days_after_planting <= curve[0][0]:
        return curve[0][1]
    for (d0, k0), (d1, k1) in zip(curve, curve[1:]):
        if days_after_planting <= d1:
            return k0 + (k1 - k0) * (days_after_planting - d0) / (d1 - d0)
    return curve[-1][1]


def interpolate_ndvi(series, day):
    """series: {date: ndvi} of CLEAN scenes. Returns (ndvi, age_days) for `day`:
    linear between the surrounding clean scenes, or the last value held when only past scenes exist.
    age_days = days since the most recent clean scene on/before `day` (None if there is none)."""
    past = [d for d in series if d <= day]
    if not past:
        return None, None
    last = max(past)
    age = (day - last).days
    future = [d for d in series if d > day]
    if future:
        nxt = min(future)
        span = (nxt - last).days
        if span > 0:
            return series[last] + (series[nxt] - series[last]) * (day - last).days / span, age
    return series[last], age


def daily_kc(series, day, cfg_crop, stale_days=20):
    """Kc for a day. Returns (kc, ndvi, stale). Beyond `stale_days` since the last clean scene the
    NDVI converter is disabled and the crop-stage curve (days after planting) is used instead."""
    ndvi, age = interpolate_ndvi(series, day)
    stale = ndvi is None or age > stale_days
    if not stale:
        return kc_from_ndvi(ndvi, cfg_crop["kcb_min"], cfg_crop["kcb_max"],
                            cfg_crop["ndvi_soil"], cfg_crop["ndvi_full"]), ndvi, False
    planting = cfg_crop.get("planting_date")
    if planting:
        dap = (day - date.fromisoformat(planting)).days
        return stage_kc(dap, cfg_crop["stage_kc_by_dap"]), ndvi, True
    return (cfg_crop["kcb_min"] + cfg_crop["kcb_max"]) / 2.0, ndvi, True   # no planting date: mid value


# ── NDVI stress flag (supporting signal only; never acts alone) ───────
def ndvi_stress_flag(series, recent_rain_mm, noise, scenes=3, rain_limit_mm=20.0,
                     senescent=False):
    """True only when NDVI fell across the last `scenes` clean scenes, each fall larger than the
    measured scene-to-scene noise, rain over the period was low, and the crop is not senescing."""
    if senescent or recent_rain_mm > rain_limit_mm:
        return False
    vals = [series[d] for d in sorted(series)][-scenes:]
    if len(vals) < scenes:
        return False
    return all((a - b) > noise for a, b in zip(vals, vals[1:]))


# ── decision + run time ───────────────────────────────────────────────
def run_time_minutes(depletion_mm, efficiency, area_m2, flow_lph, max_min, min_min):
    """Minutes to refill `depletion_mm` (net). 1 mm over 1 m2 = 1 litre. Below min_min -> 0 (carry over)."""
    gross_mm = depletion_mm / efficiency
    minutes = 60.0 * (gross_mm * area_m2) / flow_lph
    if minutes < min_min:
        return 0.0
    return min(minutes, max_min)


def zone_flow(zone):
    """(area_m2, flow_lph) from the zone config; rate_mm_hr is an area-free alternative
    (Q = rate * area, so any area gives the same minutes). Returns None if neither is usable."""
    if zone.get("area_m2") and zone.get("flow_lph"):
        return float(zone["area_m2"]), float(zone["flow_lph"])
    if zone.get("rate_mm_hr"):
        return 1.0, float(zone["rate_mm_hr"])      # 1 m2 at rate mm/h => rate L/h
    return None


def decide(dr, taw, p, stress_flag, forecast_rain_mm, rain_skip_mm, stress_fraction=0.70):
    """Returns (decision, reason). decision in IRRIGATE | SKIP_RAIN | NO_RUN."""
    raw = p * taw
    if forecast_rain_mm > rain_skip_mm:
        return "SKIP_RAIN", f"forecast {forecast_rain_mm:.1f} mm > {rain_skip_mm:.0f} mm in the next 24-48 h"
    if dr >= raw:
        return "IRRIGATE", f"depletion {dr:.1f} mm >= RAW {raw:.1f} mm"
    if stress_flag and dr > stress_fraction * raw:
        return "IRRIGATE", f"NDVI stress flag and depletion {dr:.1f} mm > {stress_fraction:.0%} of RAW"
    return "NO_RUN", f"soil water ample (depletion {dr:.1f} mm < RAW {raw:.1f} mm)"


def irrigation_depth_from_minutes(minutes, efficiency, area_m2, flow_lph):
    """Net depth (mm) delivered by `minutes` of confirmed running."""
    return (minutes / 60.0) * flow_lph / area_m2 * efficiency
