# Theory of AI Drip Irrigation — AgriSat System
**Location:** CAET Campus, Dediapada (21.628°N, 73.592°E)

---

## What the System Does

An end-to-end AI-driven precision irrigation platform that fuses **satellite imagery**, **real-time weather**, and **soil physics** to decide when and how much to irrigate — autonomously, every 3 days.

---

## Architecture (3 Layers)

### Layer 1 — Satellite NDVI Engine (`ndvi_harvester.py`)
- Queries **Microsoft Planetary Computer** (free Sentinel-2 L2A API) for the latest cloud-free scene over Dediapada
- Computes **NDVI = (NIR − Red) / (NIR + Red)** from Band 8 and Band 4
- Generates a colourised JPEG map (red = bare/stressed, green = healthy vegetation)
- Clips NDVI to 3 named campus zones using saved GeoJSON boundaries
- Runs on a **3-day automatic cron cycle**, uploads outputs to Google Drive in incognito Chrome

### Layer 2 — Weather Intelligence (`get_weather_context` + Open-Meteo)
- Pulls live data from **Open-Meteo API** (no key required):
  - **ET₀** — evapotranspiration (how fast soil is losing water)
  - **3-day rain forecast** — upcoming precipitation
  - **VPD** — vapour pressure deficit (crop stress indicator)
- These values override the base NDVI decision:

| Condition | Override |
|-----------|----------|
| STRESSED + rain ≥ 15 mm forecast | → HOLD (skip irrigation) |
| HEALTHY/THRIVING + ET₀ > 8 mm/d | → MODERATE (irrigate sooner) |
| MODERATE + VPD > 3.0 kPa | → STRESSED (irrigate immediately) |

### Layer 3 — Drip Decision Dashboard (`app.py`, Streamlit)
- Monitors **3 field zones** (A, B, C) with live soil VWC (volumetric water content)
- Uses **Soil Water Balance Model**:
  - Field Capacity (FC) = 32% | Wilting Point (WP) = 14% | TAW = 18%
  - Refill triggered when VWC drops below **WP + 50% × TAW = 23%**
- Calculates **crop water demand** using FAO Penman-Monteith ET₀ × Kc (crop coefficient)
- Drip system specs: 3 mm/hr at 90% efficiency
- Logs every decision to **Google Sheets** via service account
- Fetches EOS-04 SAR (ISRO Bhoonidhi API) for soil moisture radar data

---

## Decision Logic (Per Zone)

```
NDVI threshold → Base decision
     ↓
Weather override check (rain / ET₀ / VPD)
     ↓
Final status: STRESSED / MODERATE / HEALTHY / THRIVING / HOLD
     ↓
Valve command: Open (irrigate) or Hold
     ↓
Volume calculated = (FC - current VWC) × root depth × area / drip efficiency
```

**NDVI Thresholds:**
- < 0.25 → STRESSED (irrigate within 24 h)
- 0.25–0.35 → MODERATE (irrigate in 2–3 days)
- 0.35–0.50 → HEALTHY (hold — normal schedule)
- > 0.50 → THRIVING (no action)

---

## Output Files (Per Cycle)

| File | Contents |
|------|----------|
| `NDVI_Dediapada_scene-[date]_downloaded-[date].jpeg` | Colourised NDVI satellite map |
| `NDVI_ZoneReport_scene-[date]_downloaded-[date].json` | Per-zone NDVI, status, action, weather context |
| `telemetry_log.csv` | All irrigation decisions with timestamp, VWC, ET₀, valve state |
| Google Sheets | Same telemetry, accessible remotely |

---

## Technologies Used

| Component | Technology |
|-----------|------------|
| Satellite data | Microsoft Planetary Computer (Sentinel-2 L2A, free) |
| Radar soil data | ISRO Bhoonidhi API (EOS-04 SAR) |
| Weather | Open-Meteo API (free, no key) |
| Dashboard | Python + Streamlit |
| Zone geometry | GeoJSON + Shapely + Rasterio |
| Drive upload | Selenium Chrome (incognito) |
| Data logging | Google Sheets API (service account) |
| Map display | Folium |

---

## Key Equations

**Soil Water Balance:**
> Net soil water = Previous VWC + Rain − ET₀ × Kc − Drainage

**Irrigation Volume:**
> Vol (L) = (FC − VWC) × root_depth_m × area_m² / drip_efficiency

**NDVI:**
> NDVI = (Band8_NIR − Band4_Red) / (Band8_NIR + Band4_Red)

**Reference ET₀ (FAO Penman-Monteith, simplified):**
> ET₀ = f(solar radiation, temperature, humidity, wind speed)

---

*System runs autonomously — next cycle: 2026-06-09*
