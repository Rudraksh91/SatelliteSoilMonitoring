"""
AgriSat Dediapada — Professional Satellite Irrigation Dashboard
"""
import base64, io, json, math, os, re, subprocess
from datetime import datetime, timedelta
from typing import Optional, Tuple

import folium
import pandas as pd
import matplotlib
matplotlib.use("Agg")   # non-interactive backend — renders to PNG, no JS needed
import matplotlib.pyplot as plt
import requests
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# Map rendered via st.iframe(data:text/html;base64,…) — no external component JS needed

# ── Physical test valve — controlled via MQTT, not direct GPIO ────────────────
# brain.py (running as its own systemd service on this same Pi) is the single
# real owner of the relay's GPIO pin. If app.py also claimed that GPIO
# directly, the two separate processes would fight over the same line. So
# this just publishes the same command the website's Cloud MQTT panel sends —
# brain.py's mqtt_on_message() picks it up and drives the physical valve.
MQTT_FARM_ID = "dediapada-farm-01"
MQTT_HOST, MQTT_PORT = "broker.hivemq.com", 1883
MQTT_TOPIC_CMD = f"agrisat/{MQTT_FARM_ID}/cmd"

def publish_valve_cmd(zone: int, state: bool) -> bool:
    try:
        import paho.mqtt.publish as mqtt_publish
        mqtt_publish.single(MQTT_TOPIC_CMD, payload=json.dumps({"zone": zone, "state": state}),
                             hostname=MQTT_HOST, port=MQTT_PORT)
        return True
    except Exception:
        return False

# ─── Constants ────────────────────────────────────────────────────────────────
FIELD_LAT, FIELD_LON = 21.628, 73.592
FC, WP = 32.0, 14.0
TAW = FC - WP
REFILL_PT = WP + 0.50 * TAW
DRIP_RATE_MM_HR, DRIP_EFF = 3.0, 0.90
SHEET_ID      = "1kfaroip8-YbA9t4kcCynXvq73Eqk6mx7GT7q5SdZXqc"
SA_KEY        = os.path.join(os.path.dirname(__file__), "agrisat_service_account.json")
TELEMETRY_CSV = os.path.join(os.path.dirname(__file__), "telemetry_log.csv")
NDVI_JSON     = os.path.join(os.path.dirname(__file__), "ndvi_status.json")
NDVI_STRESS_MAX = 0.18   # below this = stressed / dry vegetation (per NDVI harvester doc)
NDVI_HEALTHY_MIN = 0.35  # at/above this = healthy / well-watered

CROPS = {
    "Paddy (Kharif)":    {"Kc": 1.10, "root_m": 0.40},
    "Maize":             {"Kc": 1.15, "root_m": 0.60},
    "Soybean":           {"Kc": 1.10, "root_m": 0.50},
    "Groundnut":         {"Kc": 1.05, "root_m": 0.50},
    "Cotton":            {"Kc": 1.15, "root_m": 0.90},
    "Wheat (Rabi)":      {"Kc": 1.05, "root_m": 0.80},
    "Chickpea":          {"Kc": 0.95, "root_m": 0.60},
    "Ornamental/Shade":  {"Kc": 0.60, "root_m": 0.30},  # boundary / decorative plants
}

# ── 3 Field zones (approximate positions around main property) ────────────────
# Offsets: ~150 m N / ~150 m E / ~150 m S from centre (21.628°N, 73.592°E)
FIELDS = {
    "Field A": {"lat": 21.6294, "lon": 73.592,  "color": "#4ade80", "icon": "leaf"},
    "Field B": {"lat": 21.628,  "lon": 73.5935, "color": "#38bdf8", "icon": "tint"},
    "Field C": {"lat": 21.6267, "lon": 73.592,  "color": "#fbbf24", "icon": "sun-o"},
}
FIELD_NAMES = list(FIELDS.keys())

TELEMETRY_COLS = [
    "Timestamp","Field","VWC (%)","Moisture Source","Temp (°C)","Humidity (%)","VPD (kPa)",
    "ET₀ (mm/d)","Rain (mm)","Rain Forecast 3d (mm)","Valve","Irrigated Vol (L)",
    "Decision","Crop","Area (ha)",
]

# ─── Page config ──────────────────────────────────────────────────────────────
st.set_page_config(page_title="AgriSat Dediapada", page_icon="🛰️", layout="wide",
                   initial_sidebar_state="collapsed")

# ─── CSS ──────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap');

/* ── Design tokens ── */
:root{
  --bg-base:#04080f;
  --bg-card:rgba(8,18,36,0.85);
  --bg-card-alt:rgba(6,14,28,0.9);
  --border-dim:rgba(56,189,248,0.1);
  --border-mid:rgba(56,189,248,0.22);
  --border-glow:rgba(56,189,248,0.42);
  --text-primary:#f0f6ff;
  --text-secondary:#94a3b8;
  --text-dim:#475569;
  --cyan:#38bdf8;
  --green:#4ade80;
  --amber:#fbbf24;
  --red:#f87171;
  --purple:#a78bfa;
}

/* ── Base ── */
html,body,[class*="css"]{font-family:'Inter',sans-serif}
.main,.block-container{
  background:var(--bg-base)!important;
  color:var(--text-primary);
  padding-top:0!important;
  padding-left:clamp(0.5rem,2vw,1.5rem)!important;
  padding-right:clamp(0.5rem,2vw,1.5rem)!important;
}
[data-testid="stSidebar"]{background:var(--bg-base)!important}
button[kind="header"]{display:none}
div[data-testid="stStatusWidget"]{display:none}
.stMetric{display:none}

/* ── Mobile: stack Streamlit columns ── */
@media(max-width:768px){
  div[data-testid="stHorizontalBlock"]{flex-wrap:wrap!important}
  div[data-testid="column"]{min-width:100%!important;flex:1 1 100%!important}
}

/* ── Header ── */
.site-header{
  background:linear-gradient(135deg,rgba(4,8,20,0.98)0%,rgba(6,16,38,0.98)60%,rgba(4,8,20,0.98)100%);
  backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border-bottom:1px solid var(--border-dim);
  padding:clamp(12px,3vw,18px) clamp(14px,4vw,28px);
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;
  margin:-1rem -1rem 1.5rem -1rem;
  position:relative;
}
.site-header::after{
  content:'';position:absolute;bottom:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent 0%,rgba(56,189,248,0.4)40%,rgba(74,222,128,0.3)60%,transparent 100%);
}
.site-logo{display:flex;align-items:center;gap:12px;flex:1;min-width:180px}
.site-logo h1{
  font-size:clamp(.95rem,3vw,1.4rem);font-weight:800;color:#fff;margin:0;
  background:linear-gradient(135deg,#4ade80 0%,#38bdf8 55%,#a78bfa 100%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
}
.site-logo .sub{font-size:clamp(.58rem,1.8vw,.72rem);color:var(--text-dim);margin-top:2px;display:block}
.share-url{font-size:clamp(.58rem,1.6vw,.7rem);color:var(--cyan);margin-top:2px;display:block}
@media(max-width:500px){.share-url{display:none}}
.header-badges{display:flex;gap:7px;align-items:center;flex-wrap:wrap}
.badge{
  padding:4px 10px;border-radius:20px;font-size:.67rem;font-weight:700;
  letter-spacing:.06em;text-transform:uppercase;white-space:nowrap;
  backdrop-filter:blur(8px);
}
.badge-green{background:rgba(74,222,128,.1);color:#4ade80;border:1px solid rgba(74,222,128,.3)}
.badge-blue{background:rgba(56,189,248,.1);color:#38bdf8;border:1px solid rgba(56,189,248,.3)}
.badge-amber{background:rgba(251,191,36,.1);color:#fbbf24;border:1px solid rgba(251,191,36,.3)}
.badge-red{background:rgba(248,113,113,.1);color:#f87171;border:1px solid rgba(248,113,113,.3)}

/* ── KPI grid — responsive auto-fit ── */
.kpi-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
  gap:12px;margin-bottom:20px;
}
@media(max-width:500px){
  .kpi-grid{grid-template-columns:repeat(2,1fr);gap:8px}
}
.kpi-card{
  background:var(--bg-card);
  border:1px solid var(--border-dim);border-radius:18px;
  padding:clamp(13px,3vw,18px) clamp(12px,2.5vw,16px);
  position:relative;overflow:hidden;
  transition:transform .25s,box-shadow .25s,border-color .25s;
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
}
.kpi-card:hover{
  transform:translateY(-4px);
  border-color:var(--border-glow);
  box-shadow:0 8px 32px rgba(56,189,248,.12),0 0 0 1px rgba(56,189,248,.06);
}
/* Top accent bar */
.kpi-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--accent,linear-gradient(90deg,#4ade80,#38bdf8));
  border-radius:18px 18px 0 0;
}
/* Bottom-right glow orb */
.kpi-card::after{
  content:'';position:absolute;bottom:-10px;right:-10px;
  width:60px;height:60px;border-radius:50%;
  background:radial-gradient(circle,var(--accent-glow,rgba(56,189,248,.1))0%,transparent 70%);
}
.kpi-label{
  font-size:.67rem;color:var(--text-dim);text-transform:uppercase;
  letter-spacing:.1em;font-weight:700;margin-bottom:9px;
}
.kpi-value{
  font-size:clamp(1.3rem,3.5vw,1.75rem);font-weight:800;
  color:var(--text-primary);line-height:1;
}
.kpi-unit{font-size:.73rem;color:var(--text-secondary);margin-left:3px;font-weight:400}
.kpi-delta{font-size:.67rem;margin-top:7px;font-weight:600}
.kpi-delta.up{color:#4ade80}.kpi-delta.down{color:#f87171}

/* ── Section title ── */
.section-title{
  font-size:.78rem;font-weight:700;color:var(--text-secondary);
  text-transform:uppercase;letter-spacing:.1em;margin-bottom:14px;
  display:flex;align-items:center;gap:8px;
}

/* ── Valve status cards ── */
.valve-open{
  background:linear-gradient(145deg,rgba(56,189,248,.09),rgba(74,222,128,.04));
  border:1px solid rgba(56,189,248,.38);border-radius:18px;padding:18px;
  position:relative;overflow:hidden;
  box-shadow:0 0 24px rgba(56,189,248,.07),inset 0 1px 0 rgba(255,255,255,.04);
}
.valve-open::before{
  content:'';position:absolute;top:-40%;left:-40%;width:180%;height:180%;
  background:radial-gradient(circle at 30% 20%,rgba(56,189,248,.06)0%,transparent 55%);
  animation:glow-pulse 3s ease-in-out infinite;
}
.valve-closed{
  background:linear-gradient(145deg,rgba(12,22,45,.92),rgba(6,12,28,.95));
  border:1px solid rgba(30,58,95,.5);border-radius:18px;padding:18px;
  transition:border-color .2s;
}
.valve-closed:hover{border-color:rgba(56,189,248,.2)}
.valve-title{font-size:.88rem;font-weight:700;margin-bottom:8px}
.valve-timer{
  font-family:'JetBrains Mono',monospace;
  font-size:clamp(1.2rem,4vw,1.9rem);
  color:var(--cyan);font-weight:700;letter-spacing:.08em;
}
.stat-row{display:flex;gap:16px;margin-top:10px;flex-wrap:wrap}
.stat-item{display:flex;flex-direction:column;gap:2px}
.stat-label{font-size:.6rem;color:var(--text-dim);text-transform:uppercase;letter-spacing:.09em}
.stat-val{font-size:.88rem;font-weight:600;color:var(--text-primary)}

/* ── Data table ── */
.styled-table{width:100%;border-collapse:collapse;font-size:.78rem}
.styled-table th{
  background:rgba(8,24,50,.8);color:var(--text-secondary);
  padding:10px 12px;text-align:left;font-weight:700;
  font-size:.64rem;text-transform:uppercase;letter-spacing:.08em;
  border-bottom:2px solid var(--border-dim);white-space:nowrap;
}
.styled-table td{padding:8px 12px;border-bottom:1px solid rgba(12,28,50,.8);color:#cbd5e1;white-space:nowrap}
.styled-table tr:hover td{background:rgba(56,189,248,.04)}
.styled-table tr:nth-child(even) td{background:rgba(255,255,255,.015)}
.tag-open{background:rgba(56,189,248,.15);color:#38bdf8;padding:2px 8px;border-radius:10px;font-size:.63rem;font-weight:700}
.tag-closed{background:rgba(100,116,139,.12);color:#64748b;padding:2px 8px;border-radius:10px;font-size:.63rem;font-weight:700}
.vwc-low{color:#f87171;font-weight:700}.vwc-ok{color:#4ade80;font-weight:600}
.source-sat{color:#a78bfa;font-size:.68rem}.source-est{color:#fbbf24;font-size:.68rem}

/* ── Tabs ── */
.stTabs [data-baseweb="tab-list"]{
  background:var(--bg-card);
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  border-radius:14px;padding:5px;gap:4px;
  border:1px solid var(--border-dim);margin-bottom:20px;
  overflow-x:auto;-webkit-overflow-scrolling:touch;
  scrollbar-width:none;
}
.stTabs [data-baseweb="tab-list"]::-webkit-scrollbar{display:none}
.stTabs [data-baseweb="tab"]{
  background:transparent;border-radius:10px;
  color:var(--text-dim);font-weight:500;
  font-size:clamp(.74rem,2vw,.85rem);
  padding:clamp(6px,1.5vw,9px) clamp(10px,2.5vw,18px);
  border:none;white-space:nowrap;transition:all .2s;
}
.stTabs [aria-selected="true"]{
  background:linear-gradient(135deg,rgba(56,189,248,.16),rgba(74,222,128,.07))!important;
  color:var(--cyan)!important;font-weight:700;
  box-shadow:0 0 14px rgba(56,189,248,.15);
}
.stTabs [data-baseweb="tab-highlight"]{display:none}
.stTabs [data-baseweb="tab-border"]{display:none}

/* ── Streamlit overrides ── */
div[data-testid="stHorizontalBlock"]{gap:12px}
.stButton button{
  background:linear-gradient(135deg,rgba(22,50,90,.85),rgba(12,34,68,.85));
  color:var(--cyan);border:1px solid rgba(56,189,248,.22);border-radius:12px;
  font-weight:700;font-size:.82rem;transition:all .25s;
  min-height:44px;backdrop-filter:blur(8px);
}
.stButton button:hover{
  background:linear-gradient(135deg,rgba(30,66,120,.9),rgba(22,50,90,.9));
  border-color:rgba(56,189,248,.55);color:#fff;
  box-shadow:0 4px 20px rgba(56,189,248,.18);
}
.stSelectbox>div>div,.stNumberInput>div>div>input,.stTextInput>div>div>input{
  background:rgba(6,14,30,.9)!important;
  border:1px solid rgba(56,189,248,.15)!important;
  color:var(--text-primary)!important;border-radius:10px!important;
}
.stFileUploader{background:rgba(6,14,30,.7);border:1px dashed rgba(56,189,248,.18);border-radius:12px}

/* ── Scrollbar ── */
::-webkit-scrollbar{width:4px;height:4px}
::-webkit-scrollbar-track{background:var(--bg-base)}
::-webkit-scrollbar-thumb{background:rgba(56,189,248,.28);border-radius:4px}

/* ── Animations ── */
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
.pulse{animation:pulse 2s infinite}
@keyframes glow-pulse{0%,100%{opacity:.5}50%{opacity:1}}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.15}}
@keyframes ring-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
@keyframes ring-spin-rev{from{transform:rotate(0deg)}to{transform:rotate(-360deg)}}

/* ── Space nebula background ── */
.main,.block-container{
  background-image:
    radial-gradient(ellipse 80% 55% at 10% 25%,rgba(56,189,248,.025) 0%,transparent 65%),
    radial-gradient(ellipse 60% 45% at 90% 80%,rgba(167,139,250,.025) 0%,transparent 60%)!important;
}

/* ── Panel title (section header with trailing line) ── */
.panel-title{
  font-size:.68rem;font-weight:700;color:var(--text-dim);
  text-transform:uppercase;letter-spacing:.13em;
  margin-bottom:14px;display:flex;align-items:center;gap:10px;
}
.panel-title::after{
  content:'';flex:1;height:1px;
  background:linear-gradient(90deg,rgba(56,189,248,.25),transparent);
}

/* ── Satellite-style field sidebar cards ── */
.sat-card{
  background:rgba(6,14,32,.82);
  border:1px solid rgba(56,189,248,.12);
  border-radius:16px;padding:14px;margin-bottom:10px;
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  transition:all .25s;position:relative;overflow:hidden;cursor:default;
}
.sat-card::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(56,189,248,.4),transparent);
}
.sat-card:hover{
  border-color:rgba(56,189,248,.28);
  background:rgba(10,22,48,.88);
  transform:translateX(3px);
}
.sat-card.sat-active{
  border-color:rgba(74,222,128,.32);
  background:rgba(4,18,14,.85);
  box-shadow:0 0 22px rgba(74,222,128,.06);
}
.sat-card.sat-active::before{
  background:linear-gradient(90deg,transparent,rgba(74,222,128,.4),transparent);
}

/* ── Stats panel (right column) ── */
.stats-panel{
  background:rgba(6,14,32,.82);
  border:1px solid rgba(56,189,248,.12);
  border-radius:18px;padding:16px;
  backdrop-filter:blur(16px);-webkit-backdrop-filter:blur(16px);
  margin-bottom:12px;position:relative;overflow:hidden;
}
.stats-panel::before{
  content:'';position:absolute;top:0;left:0;right:0;height:1px;
  background:linear-gradient(90deg,transparent,rgba(56,189,248,.35),rgba(167,139,250,.2),transparent);
}
.stat-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:8px}
.stat-block{
  background:rgba(4,8,24,.7);
  border:1px solid rgba(56,189,248,.08);
  border-radius:12px;padding:12px 10px;text-align:center;
  transition:border-color .2s;
}
.stat-block:hover{border-color:rgba(56,189,248,.2)}
.stat-num{
  font-family:'JetBrains Mono',monospace;
  font-size:clamp(1.25rem,2.5vw,1.55rem);font-weight:800;line-height:1;
  color:var(--cyan);text-shadow:0 0 14px rgba(56,189,248,.55);
}
.stat-num.green{color:#4ade80;text-shadow:0 0 14px rgba(74,222,128,.55)}
.stat-num.amber{color:#fbbf24;text-shadow:0 0 14px rgba(251,191,36,.5)}
.stat-num.purple{color:#a78bfa;text-shadow:0 0 14px rgba(167,139,250,.5)}
.stat-num.red{color:#f87171;text-shadow:0 0 14px rgba(248,113,113,.5)}
.stat-num.orange{color:#fb923c;text-shadow:0 0 14px rgba(251,146,60,.5)}
.stat-unit-sm{font-size:.68rem;color:#475569;margin-left:2px;font-weight:400}
.stat-name{font-size:.6rem;color:#475569;text-transform:uppercase;letter-spacing:.1em;margin-top:5px}

/* ── Neon text shadow on KPI values ── */
.kpi-value{text-shadow:0 0 18px rgba(56,189,248,.35)}

/* ── Decorative orbit rings ── */
.orbit-rings{
  position:relative;padding:3px;
}
.orbit-ring{
  position:absolute;border-radius:50%;pointer-events:none;
  border-style:dashed;border-color:rgba(56,189,248,.12);
}
.orbit-ring-1{inset:-8px;border-width:1px;animation:ring-spin 40s linear infinite}
.orbit-ring-2{inset:-18px;border-width:1px;border-color:rgba(167,139,250,.07);animation:ring-spin-rev 60s linear infinite}

/* ── Map glow aura ── */
.map-wrap{
  border-radius:14px;overflow:hidden;position:relative;
  box-shadow:0 0 40px rgba(56,189,248,.06),0 0 80px rgba(56,189,248,.03);
}

/* ── Irrigation event log cards ── */
.irr-event{
  background:rgba(4,12,28,.8);
  border:1px solid rgba(56,189,248,.1);
  border-left:3px solid #38bdf8;
  border-radius:10px;padding:10px 12px;margin-bottom:7px;font-size:.78rem;
}
.irr-event-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:4px}
.irr-event-field{font-weight:700;color:#38bdf8;font-size:.8rem}
.irr-event-time{font-size:.65rem;color:#475569;font-family:'JetBrains Mono',monospace}
.irr-event-body{color:#94a3b8;font-size:.74rem;line-height:1.4}
</style>
""", unsafe_allow_html=True)


# ─── Animated starfield background ───────────────────────────────────────────
st.html("""
<canvas id="agrisat-stars" style="position:fixed;top:0;left:0;width:100%;height:100%;
  z-index:0;pointer-events:none;opacity:0.65"></canvas>
<script>
(function(){
  var c=document.getElementById('agrisat-stars');
  if(!c)return;
  var ctx=c.getContext('2d');
  function resize(){c.width=window.innerWidth;c.height=window.innerHeight;}
  resize();
  window.addEventListener('resize',resize);
  var stars=[];
  for(var i=0;i<180;i++){
    stars.push({
      x:Math.random()*c.width, y:Math.random()*c.height,
      r:Math.random()*1.0+0.15,
      a:Math.random()*Math.PI*2,
      speed:Math.random()*0.006+0.002,
      baseAlpha:Math.random()*0.35+0.08
    });
  }
  /* Two slightly larger 'bright' stars */
  for(var i=0;i<12;i++){
    stars.push({
      x:Math.random()*c.width, y:Math.random()*c.height,
      r:Math.random()*1.6+0.8,
      a:Math.random()*Math.PI*2,
      speed:Math.random()*0.004+0.001,
      baseAlpha:Math.random()*0.55+0.2
    });
  }
  function draw(){
    ctx.clearRect(0,0,c.width,c.height);
    stars.forEach(function(s){
      s.a+=s.speed;
      var alpha=s.baseAlpha*(0.55+0.45*Math.sin(s.a));
      ctx.beginPath();
      ctx.arc(s.x,s.y,s.r,0,Math.PI*2);
      ctx.fillStyle='rgba(200,220,255,'+alpha.toFixed(3)+')';
      ctx.fill();
    });
    requestAnimationFrame(draw);
  }
  draw();
})();
</script>
""", unsafe_allow_javascript=True)

# ─── Voice ────────────────────────────────────────────────────────────────────
def speak(text: str):
    t = text.replace("'","").replace("\n"," ")[:300]
    st.html(f"<script>speechSynthesis.cancel();var u=new SpeechSynthesisUtterance('{t}');u.rate=.95;u.lang='en-IN';speechSynthesis.speak(u)</script>",
            unsafe_allow_javascript=True)


# ─── Drive logger ─────────────────────────────────────────────────────────────
class DriveLogger:
    SCOPES=["https://www.googleapis.com/auth/spreadsheets"]
    MAX_ROWS=50_000
    def __init__(self):
        self._svc=None; self._sid:Optional[str]=None; self._tab:str=""
        self.sheet_url:Optional[str]=None; self._pending:list=[]
    @property
    def connected(self): return self._svc is not None and bool(self._sid)
    @staticmethod
    def _parse(s):
        s=s.strip()
        return s.split("spreadsheets/d/")[1].split("/")[0] if "spreadsheets/d/" in s else s
    def _tab_name(self): return f"Data_{datetime.now().strftime('%b_%Y')}"
    def _row_count(self):
        try:
            meta=self._svc.spreadsheets().values().get(spreadsheetId=self._sid,range=f"{self._tab}!A:A").execute()
            return len(meta.get("values",[]))
        except: return 0
    def _ensure_tab(self,tab):
        meta=self._svc.spreadsheets().get(spreadsheetId=self._sid).execute()
        existing=[s["properties"]["title"] for s in meta.get("sheets",[])]
        if tab not in existing:
            self._svc.spreadsheets().batchUpdate(spreadsheetId=self._sid,
                body={"requests":[{"addSheet":{"properties":{"title":tab}}}]}).execute()
            self._svc.spreadsheets().values().append(spreadsheetId=self._sid,range=f"{tab}!A1",
                valueInputOption="RAW",insertDataOption="INSERT_ROWS",
                body={"values":[TELEMETRY_COLS]}).execute()
    def _active_tab(self):
        tab=self._tab_name(); self._ensure_tab(tab)
        if self._row_count()>=self.MAX_ROWS:
            tab=f"Data_{datetime.now().strftime('%b_%Y_w%W')}"; self._ensure_tab(tab)
        return tab
    def connect(self,creds_dict,url_or_id)->Tuple[bool,str]:
        try:
            creds=Credentials.from_service_account_info(creds_dict,scopes=self.SCOPES)
            self._svc=build("sheets","v4",credentials=creds,cache_discovery=False)
            self._sid=self._parse(url_or_id); self.sheet_url=f"https://docs.google.com/spreadsheets/d/{self._sid}"
            self._svc.spreadsheets().get(spreadsheetId=self._sid,fields="spreadsheetId").execute()
            self._tab=self._active_tab(); return True,self.sheet_url
        except HttpError as e:
            sa=creds_dict.get("client_email",""); return False,f"HTTP {e.status_code}: {e.reason} — share with {sa}"
        except Exception as e: return False,str(e)
    def push_row(self,row): self._pending.append(row)
    def flush_pending(self):
        if not self.connected or not self._pending: return
        try:
            if self._row_count()>=self.MAX_ROWS: self._tab=self._active_tab()
            self._svc.spreadsheets().values().append(spreadsheetId=self._sid,range=f"{self._tab}!A1",
                valueInputOption="RAW",insertDataOption="INSERT_ROWS",
                body={"values":[[r.get(c,"") for c in TELEMETRY_COLS] for r in self._pending]}).execute()
            self._pending.clear()
        except Exception as e: st.toast(f"Drive sync: {e}",icon="⚠️")


# ─── Weather (India Meteorological Department) ─────────────────────────────────
# No third-party weather API, no ground sensor: IMD publishes a daily city
# bulletin (Tmax/Tmin, RH at 0830/1730, 24h rainfall, 7-day categorical
# forecast) for Surat — the nearest IMD-reporting city to Dediapada (~70 km).
# It's a legacy scraped HTML page, not a JSON API, and daily-resolution, not
# hourly — so ET0 is derived via FAO-56 Hargreaves-Samani (needs only
# Tmax/Tmin + latitude) and "current" temp via a diurnal curve bounded by
# today's real Tmax/Tmin, rather than a sensor-style instantaneous reading
# IMD simply doesn't publish.
IMD_STATION_ID = 42840   # Surat
IMD_CITY_URL = f"https://city.imd.gov.in/citywx/city_weather_test.php?id={IMD_STATION_ID}"

# IMD's own rainfall-intensity nomenclature (mm/24h bands) — used to turn a
# categorical text forecast ("Light rain") into an indicative depth. Not a
# measurement, but grounded in IMD's published category definitions.
_IMD_RAIN_MM = [("extremely heavy",150.0),("very heavy",80.0),("heavy",50.0),
                ("moderate",20.0),("thunder",20.0),("shower",10.0),("light",5.0)]

def _imd_strip(html): return re.sub(r"<[^>]+>"," ",html).strip()

def _imd_table_dict(html):
    out={}
    for row in re.findall(r"<tr.*?</tr>",html,re.S):
        cells=re.findall(r"<td[^>]*>(.*?)</td>",row,re.S)
        if len(cells)==2:
            label,value=_imd_strip(cells[0]),_imd_strip(cells[1])
            if label and value: out[label]=value
    return out

def _imd_find(d,*keywords):
    for k,v in d.items():
        kl=k.lower()
        if all(kw in kl for kw in keywords): return v
    return None

def _imd_num(value,fallback):
    """Parse an IMD table value as float, falling back when it's NA/missing."""
    if value and value.upper()!="NA":
        try: return float(value)
        except ValueError: pass
    return float(fallback)

def _imd_forecast_rows(html):
    tail=html[html.find("Day's Forecast"):]
    rows=[]
    for row in re.findall(r"<tr.*?</tr>",tail,re.S):
        cells=re.findall(r"<td[^>]*>(.*?)</td>",row,re.S)
        if len(cells)==5:
            rows.append({"tmin":_imd_strip(cells[1]),"tmax":_imd_strip(cells[2]),
                         "wx":_imd_strip(cells[4])})
    return rows

def _imd_rain_from_text(wx):
    wx=wx.lower()
    for kw,mm in _IMD_RAIN_MM:
        if kw in wx: return mm
    return 0.0

def _hargreaves_et0(tmax,tmin,lat_deg,day_of_year):
    """FAO-56 Hargreaves-Samani reference ET0 (mm/day) from Tmax/Tmin + latitude."""
    tmean=(tmax+tmin)/2; phi=math.radians(lat_deg)
    dr=1+0.033*math.cos(2*math.pi*day_of_year/365)
    delta=0.409*math.sin(2*math.pi*day_of_year/365-1.39)
    ws=math.acos(max(-1,min(1,-math.tan(phi)*math.tan(delta))))
    Ra=(24*60/math.pi)*0.0820*dr*(ws*math.sin(phi)*math.sin(delta)+math.cos(phi)*math.cos(delta)*math.sin(ws))
    return max(0.5,0.0023*(tmean+17.8)*math.sqrt(max(0.1,tmax-tmin))*0.408*Ra)

@st.cache_data(ttl=1800)
def fetch_weather():
    try:
        html=requests.get(IMD_CITY_URL,timeout=10,
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://city.imd.gov.in/"}).text
        obs=_imd_table_dict(html); fc=_imd_forecast_rows(html)
        if not fc: return None
        tmax=_imd_num(_imd_find(obs,"maximum temp"),fc[0]["tmax"])
        tmin=_imd_num(_imd_find(obs,"minimum temp"),fc[0]["tmin"])
        rh0830=_imd_find(obs,"relative humidity","0830"); rh1730=_imd_find(obs,"relative humidity","1730")
        rh_vals=[float(v) for v in (rh0830,rh1730) if v and v.upper()!="NA"]
        hum=sum(rh_vals)/len(rh_vals) if rh_vals else 65.0
        rain_raw=_imd_find(obs,"24 hours rainfall")
        rain=0.0 if (not rain_raw or rain_raw.upper() in ("NIL","NA","TRACE")) else float(rain_raw)

        hour=datetime.now().hour+datetime.now().minute/60
        tmean=(tmax+tmin)/2
        t=tmean+(tmax-tmin)/2*math.cos(2*math.pi*(hour-15)/24)   # diurnal curve, peak ~15:00 IST

        et0=_hargreaves_et0(tmax,tmin,FIELD_LAT,datetime.now().timetuple().tm_yday)
        es=0.6108*math.exp(17.27*t/(t+237.3))
        rain_3d=sum(_imd_rain_from_text(d["wx"]) for d in fc[:3])
        return {"temp":round(t,1),"hum":round(hum,1),"rain":round(rain,1),
                "vpd":round(es*(1-hum/100),3),"et0":round(et0,2),"rain_3d":round(rain_3d,1)}
    except Exception: return None


# ─── Satellite NDVI (per-field ground truth) ──────────────────────────────────
@st.cache_data(ttl=900)
def fetch_ndvi_status():
    """Load the latest NDVI harvester output and map its zones onto FIELD_NAMES
    (Zone 1→Field A, Zone 2→Field B, Zone 3→Field C — see report.html field layer)."""
    try:
        data = json.load(open(NDVI_JSON))
        zones = data.get("zones", [])
        if len(zones) != len(FIELD_NAMES): return None
        data["zone_by_field"] = dict(zip(FIELD_NAMES, zones))
        scene_date = datetime.strptime(data["scene_date"], "%Y-%m-%d")
        data["scene_age_days"] = (datetime.now() - scene_date).days
        return data
    except Exception:
        return None


def ndvi_tier(ndvi: float) -> Tuple[str, str]:
    """Classify an NDVI reading into (label, color) per harvester thresholds."""
    if ndvi < NDVI_STRESS_MAX: return "STRESSED", "#f87171"
    if ndvi < NDVI_HEALTHY_MIN: return "MODERATE", "#fbbf24"
    return "HEALTHY", "#4ade80"


# ─── Irrigation decision ──────────────────────────────────────────────────────
def decide(vwc,weather,crop_name,area_ha,ndvi_zone=None):
    c=CROPS.get(crop_name,{"Kc":1.0,"root_m":0.5}); ETc=weather["et0"]*c["Kc"]
    rain_equiv=min(TAW,weather.get("rain",0)/(c["root_m"]*10))
    deficit=max(0.0,FC-vwc-rain_equiv); depth=deficit/100*c["root_m"]*1000
    rain_guard=weather.get("rain",0)>=2.0 or weather.get("rain_3d",0)>=20
    should=(vwc<REFILL_PT) and not rain_guard
    if rain_guard: reason=f"HOLD — Rain guard ({weather.get('rain',0)}mm now)"
    elif vwc<REFILL_PT: reason=f"IRRIGATE — VWC {vwc:.1f}% below refill point {REFILL_PT:.1f}%"
    else: reason=f"HOLD — Soil at {(vwc-WP)/TAW*100:.0f}% capacity"

    # Satellite ground-truth override: if the weather model says "hold" but the
    # latest NDVI pass shows real vegetation stress, trust the satellite and
    # irrigate with a light pulse rather than the full model-computed depth.
    sat_override = False
    if (not should and not rain_guard and ndvi_zone
            and ndvi_zone.get("ndvi") is not None
            and ndvi_zone["ndvi"] < NDVI_STRESS_MAX):
        should, sat_override = True, True
        depth = max(depth, 3.0)  # minimum light pulse, mm
        reason = f"IRRIGATE — Satellite NDVI {ndvi_zone['ndvi']:.2f} shows stress (ground-truth override)"

    dur=round((depth/DRIP_RATE_MM_HR)/DRIP_EFF,2)
    vol=int(area_ha*10_000*(depth/1000)*1000/DRIP_EFF)
    return {"should":should,"deficit":round(deficit,1),"ETc":round(ETc,2),
            "depth":round(depth,1),"dur":dur,"vol":vol,"reason":reason,
            "sat_override":sat_override}


# ─── Session state ────────────────────────────────────────────────────────────
_now = datetime.now()
for k,v in {"drive":None,
             "irr_log":[],"telemetry":[],"unsent":0,
             "_csv_loaded":False,
             "_last_collect_time": datetime.min,   # tracks 30-min data logging interval
             "farm_vwc": None,          # FAO-56 water-balance state, seeded from CSV history below
             "_irr_depth_since_tick": 0.0,   # mm applied since the last balance tick, accumulated as valves open
             # per-field valve timers
             "valve_close_Field A": _now,
             "valve_close_Field B": _now,
             "valve_close_Field C": _now}.items():
    if k not in st.session_state: st.session_state[k]=v
if not st.session_state.drive:  st.session_state.drive=DriveLogger()

drive:DriveLogger=st.session_state.drive

# ── Load history from CSV on first load of each new session ──────────────────
if not st.session_state._csv_loaded:
    if os.path.exists(TELEMETRY_CSV):
        try:
            hist = pd.read_csv(TELEMETRY_CSV)
            # keep last 200 rows; convert back to list of dicts
            st.session_state.telemetry = hist.tail(200).to_dict("records")
            if st.session_state.farm_vwc is None and len(hist):
                st.session_state.farm_vwc = float(hist["VWC (%)"].iloc[-1])
        except Exception:
            pass
    if st.session_state.farm_vwc is None:
        st.session_state.farm_vwc = round((FC + WP) / 2, 1)   # no history yet — start at mid-capacity
    st.session_state._csv_loaded = True

# Auto-connect Drive — works on Streamlit Cloud (st.secrets) AND locally (json file)
if not drive.connected:
    _creds = None
    try:                                         # Streamlit Cloud: secrets.toml
        if "google_service_account" in st.secrets:
            _creds = dict(st.secrets["google_service_account"])
    except Exception:
        pass
    if not _creds and os.path.exists(SA_KEY):    # Local: json file
        try: _creds = json.load(open(SA_KEY))
        except Exception: pass
    if _creds:
        try: drive.connect(_creds, SHEET_ID)
        except Exception: pass

# ─── Live data ────────────────────────────────────────────────────────────────
weather=fetch_weather()
ndvi_data = fetch_ndvi_status()
zone_by_field = ndvi_data["zone_by_field"] if ndvi_data else {}
src="🧮 FAO-56 Water Balance"
vwc=st.session_state.farm_vwc

# ── Per-field valve + irrigation decision ─────────────────────────────────────
field_crop  = st.session_state.get("crop", "Ornamental/Shade")
field_area  = st.session_state.get("area", 0.05)   # small boundary zones default 500 m²
field_states: dict = {}   # keyed by field name

for fname in FIELD_NAMES:
    vk = f"valve_close_{fname}"
    v_open = datetime.now() < st.session_state[vk]
    irr_f  = decide(vwc, weather, field_crop, field_area, zone_by_field.get(fname)) if (weather and vwc) else None

    if irr_f and irr_f["should"] and not v_open:
        st.session_state[vk] = datetime.now() + timedelta(hours=irr_f["dur"])
        v_open = True
        st.session_state._irr_depth_since_tick += irr_f["depth"]
        st.session_state.irr_log.append({
            "Time": datetime.now().strftime("%d/%m %H:%M"), "Field": fname,
            "VWC %": vwc, "Depth mm": irr_f["depth"],
            "Duration h": irr_f["dur"], "Volume L": irr_f["vol"],
            "Decision": irr_f["reason"]})
        speak(f"{fname} irrigation on. Moisture {vwc:.0f}%. Running {irr_f['dur']:.1f} hours.")

    field_states[fname] = {"valve_open": v_open, "irr": irr_f,
                            "valve_close": st.session_state[vk]}

# Overall valve status for header badge (any field open = irrigating)
valve_open = any(fs["valve_open"] for fs in field_states.values())

_secs_since_log = (datetime.now() - st.session_state._last_collect_time).total_seconds()
_should_log = _secs_since_log >= 1800   # only write data every 30 minutes

if weather and vwc and _should_log:
    # ── Advance the FAO-56 water-balance state: deplete by crop ET over the
    # elapsed interval, replenish by current rainfall and any irrigation
    # applied since the last tick. No sensor, no randomness — pure model.
    _c = CROPS.get(field_crop, {"Kc": 1.0, "root_m": 0.5})
    _elapsed_days = _secs_since_log / 86400.0
    _etc_pct  = weather["et0"] * _c["Kc"] * _elapsed_days / (_c["root_m"] * 10)
    _rain_pct = min(TAW, weather.get("rain", 0) / (_c["root_m"] * 10))
    _irr_pct  = st.session_state._irr_depth_since_tick / (_c["root_m"] * 10)
    vwc = round(max(WP, min(FC, vwc - _etc_pct + _rain_pct + _irr_pct)), 1)
    st.session_state.farm_vwc = vwc
    st.session_state._irr_depth_since_tick = 0.0

    st.session_state._last_collect_time = datetime.now()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    new_rows = []
    for fname, fs in field_states.items():
        trow = {
            "Timestamp": ts, "Field": fname,
            "VWC (%)": round(vwc, 2),
            "Moisture Source": src,
            "Temp (°C)": weather["temp"], "Humidity (%)": weather["hum"],
            "VPD (kPa)": weather["vpd"], "ET₀ (mm/d)": weather["et0"],
            "Rain (mm)": weather["rain"],
            "Rain Forecast 3d (mm)": round(weather["rain_3d"], 1),
            "Valve": "OPEN" if fs["valve_open"] else "CLOSED",
            "Irrigated Vol (L)": fs["irr"]["vol"] if (fs["irr"] and fs["valve_open"]) else 0,
            "Decision": fs["irr"]["reason"] if fs["irr"] else "",
            "Crop": field_crop, "Area (ha)": field_area,
        }
        st.session_state.telemetry.append(trow)
        new_rows.append(trow)
    st.session_state.telemetry = st.session_state.telemetry[-600:]  # 200 per field

    # ── Persist to local CSV ──────────────────────────────────────────────────
    try:
        write_header = not os.path.exists(TELEMETRY_CSV)
        pd.DataFrame(new_rows).to_csv(TELEMETRY_CSV, mode="a", header=write_header, index=False)
        if len(st.session_state.telemetry) % 150 == 0:
            try:
                full = pd.read_csv(TELEMETRY_CSV)
                if len(full) > 15000:
                    full.tail(15000).to_csv(TELEMETRY_CSV, index=False)
            except Exception: pass
    except Exception: pass

    # ── Push all 3 rows to Google Sheet ──────────────────────────────────────
    if drive.connected:
        for trow in new_rows:
            drive.push_row(trow)
        drive.flush_pending()
        st.session_state.unsent = 0


# ══════════════════════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════════════════════
def _tunnel_url() -> str:
    """Read the current quick-tunnel URL — checks file first, then log."""
    import re
    url_file = os.path.join(os.path.dirname(__file__), ".tunnel_url")
    try:
        u = open(url_file).read().strip()
        if u.startswith("https://"): return u
    except Exception: pass
    for logf in ["/tmp/agrisat_cf.log", "/tmp/cf_tunnel.log"]:
        try:
            m = re.search(r'https://[a-z0-9\-]+\.trycloudflare\.com', open(logf).read())
            if m: return m.group(0)
        except Exception: pass
    return "—"

def _permanent_url() -> str:
    """Return the permanent Cloudflare Workers URL if configured, else empty string."""
    purl_file = os.path.join(os.path.dirname(__file__), ".permanent_url")
    try:
        u = open(purl_file).read().strip()
        if u.startswith("https://"): return u
    except Exception: pass
    return ""

def _pi_brain_active() -> bool:
    """Real check — is the agrisat-brain systemd service actually running on
    this machine? Returns False (not fabricated "online") on any host where
    that service doesn't exist, e.g. if this ever runs off the Pi again."""
    try:
        result = subprocess.run(["systemctl", "is-active", "agrisat-brain"],
                                 capture_output=True, text=True, timeout=3)
        return result.stdout.strip() == "active"
    except Exception:
        return False

@st.cache_data(ttl=10)
def _pi_brain_active_cached() -> bool:
    return _pi_brain_active()

_pub_url  = _tunnel_url()
_perm_url = _permanent_url()

open_fields = [f for f in FIELD_NAMES if field_states[f]["valve_open"]]
valve_badge = (f'<span class="badge badge-blue pulse">💧 {len(open_fields)}/3 IRRIGATING</span>'
               if open_fields else '<span class="badge badge-green">✅ ALL STANDBY</span>')
drive_badge = '<span class="badge badge-green">☁️ DRIVE SYNCED</span>' if drive.connected \
              else '<span class="badge badge-red">🔴 DRIVE OFF</span>'
pi_badge = '<span class="badge badge-green">🟢 PI BRAIN ONLINE</span>' if _pi_brain_active_cached() \
           else '<span class="badge badge-red">🔴 PI BRAIN OFFLINE</span>'

st.markdown(f"""
<div class="site-header">
  <div class="site-logo">
    <div>
      <h1>🌾 AgriSat Dediapada</h1>
      <span class="sub">Autonomous Drip Irrigation · {FIELD_LAT}°N {FIELD_LON}°E · {datetime.now():%d %b %Y %H:%M}</span>
      <span class="share-url">
        🔗 <a href="{_perm_url if _perm_url else _pub_url}" target="_blank"
           style="color:#a78bfa;font-weight:600;text-decoration:none">
          {(_perm_url if _perm_url else _pub_url)[:50]}
        </a>
        &nbsp;·&nbsp;<a href="http://192.168.29.198:8501" style="color:#475569;text-decoration:none">Local</a>
      </span>
    </div>
  </div>
  <div class="header-badges">
    {valve_badge}{drive_badge}{pi_badge}
  </div>
</div>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  KPI CARDS
# ══════════════════════════════════════════════════════════════════════════════
def kpi(label, value, unit="", delta=None, accent="#4ade80"):
    delta_html=""
    if delta is not None:
        cls="up" if delta>=0 else "down"
        arrow="▲" if delta>=0 else "▼"
        delta_html=f'<div class="kpi-delta {cls}">{arrow} {abs(delta):.1f}% vs refill</div>'
    # --accent-glow feeds the radial orb in ::after pseudo-element
    return f"""
<div class="kpi-card" style="--accent:linear-gradient(90deg,{accent},{accent}88);--accent-glow:{accent}22">
  <div class="kpi-label">{label}</div>
  <div><span class="kpi-value">{value}</span><span class="kpi-unit">{unit}</span></div>
  {delta_html}
</div>"""

if weather and vwc:
    delta_vwc=round(vwc-REFILL_PT,1)
    st.markdown(f"""
<div class="kpi-grid">
  {kpi("🌡 Temperature", weather['temp'], "°C", accent="#f87171")}
  {kpi("💨 VPD", weather['vpd'], "kPa", accent="#fb923c")}
  {kpi("🌧 Rain Today", weather['rain'], "mm", accent="#38bdf8")}
  {kpi("🌿 ET₀ FAO-56", weather['et0'], "mm/d", accent="#a78bfa")}
  {kpi("💧 Soil VWC", f"{vwc:.1f}", "%", delta=delta_vwc, accent="#4ade80" if delta_vwc>=0 else "#f87171")}
</div>""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  TABS
# ══════════════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4 = st.tabs([
    "🏠  Dashboard", "📊  Live Data", "☁️  Data Sync", "⚙️  Settings"
])


# ── TAB 1: DASHBOARD ─────────────────────────────────────────────────────────
with tab1:
    # ── Top status bar ───────────────────────────────────────────────────────
    st.markdown(f"""
<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
  gap:10px;margin-bottom:16px;padding:12px 18px;
  background:rgba(6,14,32,.75);backdrop-filter:blur(16px);
  border:1px solid rgba(56,189,248,.1);border-radius:14px;position:relative;overflow:hidden;">
  <div style="position:absolute;top:0;left:0;right:0;height:1px;
    background:linear-gradient(90deg,transparent,rgba(56,189,248,.4),rgba(74,222,128,.25),transparent)"></div>
  <div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">
    <div>
      <div style="font-size:.6rem;color:#475569;text-transform:uppercase;letter-spacing:.12em;font-weight:700">
        📍 Dediapada, Narmada, Gujarat</div>
      <div style="font-size:.88rem;font-weight:700;color:#f0f6ff;margin-top:2px">
        Autonomous Drip-Irrigation · Ornamental / Shade Planting</div>
    </div>
    <div style="width:1px;height:28px;background:rgba(56,189,248,.15)"></div>
    <div style="display:flex;gap:20px">
      <div><div style="font-size:1.1rem;font-weight:800;color:#4ade80;
        text-shadow:0 0 10px rgba(74,222,128,.45);font-family:'JetBrains Mono',monospace">3</div>
        <div style="font-size:.58rem;color:#475569;text-transform:uppercase;letter-spacing:.08em">Zones</div></div>
      <div><div style="font-size:1.1rem;font-weight:800;color:#a78bfa;
        text-shadow:0 0 10px rgba(167,139,250,.4);font-family:'JetBrains Mono',monospace">30m</div>
        <div style="font-size:.58rem;color:#475569;text-transform:uppercase;letter-spacing:.08em">Cycle</div></div>
      <div><div style="font-size:1.1rem;font-weight:800;color:#fbbf24;
        text-shadow:0 0 10px rgba(251,191,36,.35);font-family:'JetBrains Mono',monospace">{len(st.session_state.telemetry)}</div>
        <div style="font-size:.58rem;color:#475569;text-transform:uppercase;letter-spacing:.08em">Logged</div></div>
    </div>
  </div>
  <div style="font-size:.68rem;color:#475569">
    🕐 {datetime.now():%H:%M:%S} &nbsp;·&nbsp;
    <span style="color:#38bdf8">🌤️ Weather model</span>
  </div>
</div>""", unsafe_allow_html=True)

    # ── 3-Panel cockpit layout: sidebar | map | stats ────────────────────────
    col_sidebar, col_center, col_stats = st.columns([0.88, 1.5, 1])

    # ── LEFT: Field zone cards ───────────────────────────────────────────────
    with col_sidebar:
        st.markdown('<div class="panel-title">🌾 Field Zones</div>', unsafe_allow_html=True)
        for fname in FIELD_NAMES:
            fs  = field_states[fname]
            fi  = fs["irr"]
            fv  = fs["valve_open"]
            clr = FIELDS[fname]["color"]
            pct = round((vwc - WP) / TAW * 100) if vwc else 0
            bar_w = max(0, min(100, pct))
            card_cls = "sat-card sat-active" if fv else "sat-card"
            if fv:
                rem = fs["valve_close"] - datetime.now()
                timer_html = f"""
<div style="margin-top:10px;padding:8px 10px;
  background:rgba(56,189,248,.06);border-radius:10px;border:1px solid rgba(56,189,248,.15)">
  <div style="font-size:.58rem;color:#475569;letter-spacing:.1em;text-transform:uppercase">Remaining</div>
  <div style="font-family:'JetBrains Mono',monospace;font-size:1.25rem;font-weight:700;
    color:{clr};text-shadow:0 0 10px {clr}88;letter-spacing:.08em">{str(rem).split('.')[0]}</div>
  <div style="display:flex;gap:12px;margin-top:6px">
    <div><div style="font-size:.6rem;color:#475569;text-transform:uppercase">Depth</div>
      <div style="font-size:.82rem;font-weight:600;color:#f0f6ff">{fi['depth'] if fi else '—'} mm</div></div>
    <div><div style="font-size:.6rem;color:#475569;text-transform:uppercase">Volume</div>
      <div style="font-size:.82rem;font-weight:600;color:#f0f6ff">{fi['vol']:,} L</div></div>
  </div>
</div>"""
            else:
                reason_short = (fi["reason"][:50]+"…" if fi and len(fi["reason"])>50 else (fi["reason"] if fi else "No data"))
                timer_html = f'<div style="margin-top:8px;font-size:.67rem;color:#475569;line-height:1.45">{reason_short}</div>'

            zone = zone_by_field.get(fname)
            if zone and zone.get("ndvi") is not None:
                tier_label, tier_clr = ndvi_tier(zone["ndvi"])
                age = ndvi_data["scene_age_days"]
                age_note = f'{age}d old' if age < 10 else f'⚠️ {age}d old'
                ndvi_html = f"""
<div style="display:flex;align-items:center;justify-content:space-between;margin-top:8px;
  padding:5px 8px;background:rgba(167,139,250,.05);border-radius:8px;border:1px solid rgba(167,139,250,.12)">
  <div style="font-size:.63rem;color:#a78bfa">🛰️ NDVI {zone['ndvi']:.2f}
    <span style="color:{tier_clr};font-weight:700">· {tier_label}</span></div>
  <div style="font-size:.55rem;color:#475569">{age_note}</div>
</div>"""
            else:
                ndvi_html = '<div style="margin-top:8px;font-size:.6rem;color:#334155">🛰️ No satellite pass on file</div>'

            st.markdown(f"""
<div class="{card_cls}">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
    <div style="display:flex;align-items:center;gap:8px">
      <div style="width:30px;height:30px;border-radius:8px;
        background:{clr}18;border:1px solid {clr}44;
        display:flex;align-items:center;justify-content:center;font-size:.95rem">
        {'💧' if fv else '🌾'}
      </div>
      <div>
        <div style="font-size:.82rem;font-weight:700;color:{clr}">{fname}</div>
        <div style="font-size:.6rem;color:#475569">{'💧 Irrigating' if fv else '⏸ Standby'}</div>
      </div>
    </div>
    <div style="text-align:right">
      <div style="font-family:'JetBrains Mono',monospace;font-size:1.3rem;font-weight:800;
        color:{clr};text-shadow:0 0 12px {clr}66;line-height:1">
        {f'{vwc:.1f}' if vwc else '?'}</div>
      <div style="font-size:.58rem;color:#475569">% VWC</div>
    </div>
  </div>
  <div style="background:rgba(255,255,255,.04);border-radius:4px;height:3px;overflow:hidden">
    <div style="width:{bar_w}%;height:100%;
      background:linear-gradient(90deg,{clr}88,{clr});border-radius:4px"></div>
  </div>
  <div style="display:flex;justify-content:space-between;margin-top:3px">
    <div style="font-size:.56rem;color:#334155">WP {WP}%</div>
    <div style="font-size:.56rem;color:{clr}">{pct}% cap</div>
    <div style="font-size:.56rem;color:#334155">FC {FC}%</div>
  </div>
  {ndvi_html}
  {timer_html}
</div>""", unsafe_allow_html=True)

        # Compact irrigation event log
        if st.session_state.irr_log:
            st.markdown('<div class="panel-title" style="margin-top:14px">📋 Events</div>',
                        unsafe_allow_html=True)
            for ev in st.session_state.irr_log[-3:][::-1]:
                st.markdown(f"""
<div class="irr-event">
  <div class="irr-event-head">
    <span class="irr-event-field">{ev.get('Field','—')}</span>
    <span class="irr-event-time">{ev.get('Time','')}</span>
  </div>
  <div class="irr-event-body">{ev.get('Decision','')[:60]}</div>
</div>""", unsafe_allow_html=True)

    # ── CENTER: Map + Soil moisture gauge ────────────────────────────────────
    with col_center:
        st.markdown('<div class="panel-title">🗺️ Field Locations · Dediapada</div>',
                    unsafe_allow_html=True)

        # ── Build futuristic map ─────────────────────────────────────────────
        m = folium.Map(location=[FIELD_LAT, FIELD_LON], zoom_start=17,
                       tiles="CartoDB dark_matter",
                       zoom_control=True, scrollWheelZoom=True)

        # ── Futuristic field markers ──────────────────────────────────────────
        for fname, finfo in FIELDS.items():
            fs  = field_states[fname]
            fv  = fs["valve_open"]
            clr = FIELDS[fname]["color"]
            vwc_str = f"{vwc:.1f}" if vwc else "?"
            # Active valves pulse fast (1.4s), standby pulses slow (2.8s)
            p_dur = "1.4s" if fv else "2.8s"
            tip = f"{fname} · VWC {vwc_str}% · {'💧 Irrigating' if fv else '⏸ Standby'}"

            # Icon geometry:  total div 72×94px, ring circle centred at (36,36)
            _icon_html = f"""
<div style="position:relative;width:72px;height:94px">
  <!-- Outer pulse ring A -->
  <div style="position:absolute;top:36px;left:36px;
    width:64px;height:64px;margin-top:-32px;margin-left:-32px;
    border-radius:50%;border:1px solid {clr};
    animation:mping {p_dur} ease-out 0s infinite;opacity:0.75"></div>
  <!-- Outer pulse ring B (offset) -->
  <div style="position:absolute;top:36px;left:36px;
    width:64px;height:64px;margin-top:-32px;margin-left:-32px;
    border-radius:50%;border:1px solid {clr};
    animation:mping {p_dur} ease-out 0.7s infinite;opacity:0.4"></div>
  <!-- Static outer ring -->
  <div style="position:absolute;top:36px;left:36px;
    width:36px;height:36px;margin-top:-18px;margin-left:-18px;
    border-radius:50%;border:1px solid {clr};opacity:0.55"></div>
  <!-- Static inner ring -->
  <div style="position:absolute;top:36px;left:36px;
    width:20px;height:20px;margin-top:-10px;margin-left:-10px;
    border-radius:50%;border:1.5px solid {clr}"></div>
  <!-- Crosshair: left tick -->
  <div style="position:absolute;top:36px;left:5px;
    width:14px;height:1px;margin-top:-0.5px;
    background:{clr};opacity:0.7"></div>
  <!-- Crosshair: right tick -->
  <div style="position:absolute;top:36px;right:5px;
    width:14px;height:1px;margin-top:-0.5px;
    background:{clr};opacity:0.7"></div>
  <!-- Crosshair: top tick -->
  <div style="position:absolute;left:36px;top:5px;
    width:1px;height:14px;margin-left:-0.5px;
    background:{clr};opacity:0.7"></div>
  <!-- Crosshair: bottom tick (above label) -->
  <div style="position:absolute;left:36px;bottom:22px;
    width:1px;height:14px;margin-left:-0.5px;
    background:{clr};opacity:0.7"></div>
  <!-- Corner brackets: top-left -->
  <div style="position:absolute;top:10px;left:10px;
    width:10px;height:10px;
    border-top:2px solid {clr};border-left:2px solid {clr};opacity:0.6"></div>
  <!-- Corner brackets: top-right -->
  <div style="position:absolute;top:10px;right:10px;
    width:10px;height:10px;
    border-top:2px solid {clr};border-right:2px solid {clr};opacity:0.6"></div>
  <!-- Corner brackets: bottom-left -->
  <div style="position:absolute;bottom:22px;left:10px;
    width:10px;height:10px;
    border-bottom:2px solid {clr};border-left:2px solid {clr};opacity:0.6"></div>
  <!-- Corner brackets: bottom-right -->
  <div style="position:absolute;bottom:22px;right:10px;
    width:10px;height:10px;
    border-bottom:2px solid {clr};border-right:2px solid {clr};opacity:0.6"></div>
  <!-- Center dot glow -->
  <div style="position:absolute;top:36px;left:36px;
    width:8px;height:8px;margin-top:-4px;margin-left:-4px;
    border-radius:50%;background:{clr};
    box-shadow:0 0 6px {clr},0 0 14px {clr}88,0 0 24px {clr}44"></div>
  <!-- Label chip -->
  <div style="position:absolute;bottom:0;left:50%;transform:translateX(-50%);
    white-space:nowrap;font-family:monospace;font-size:10px;font-weight:700;
    color:{clr};background:rgba(4,8,18,0.94);
    padding:2px 7px;border-radius:4px;
    border:1px solid {clr}55;letter-spacing:0.05em;
    box-shadow:0 0 10px {clr}22,inset 0 0 6px rgba(0,0,0,0.5)">
    {fname} · {vwc_str}%
  </div>
</div>"""
            folium.Marker(
                [finfo["lat"], finfo["lon"]],
                tooltip=tip,
                popup=folium.Popup(tip, max_width=200),
                icon=folium.DivIcon(
                    html=_icon_html,
                    icon_size=(72, 94),
                    icon_anchor=(36, 36)   # pin at ring centre
                )
            ).add_to(m)

        # ── Inject @keyframes + DivIcon reset CSS into the map HTML ──────────
        _raw_map = m._repr_html_()
        _anim_css = """<style>
.leaflet-div-icon{background:transparent!important;border:none!important;overflow:visible!important}
@keyframes mping{
  0%{transform:scale(0.12);opacity:0.9}
  80%{opacity:0.15}
  100%{transform:scale(1);opacity:0}
}
</style>"""
        _raw_map = _raw_map.replace('</head>', _anim_css + '\n</head>', 1)

        st.markdown('<div class="map-wrap">', unsafe_allow_html=True)
        _map_b64 = base64.b64encode(_raw_map.encode()).decode()
        st.iframe(f"data:text/html;base64,{_map_b64}", height=360)
        st.markdown('</div>', unsafe_allow_html=True)

        # Soil moisture gauge (matplotlib)
        if vwc:
            st.markdown('<div class="panel-title" style="margin-top:12px">💧 Soil Moisture Profile</div>',
                        unsafe_allow_html=True)
            fig, ax = plt.subplots(figsize=(5, 2.0), facecolor="#04080f")
            ax.set_facecolor("#080f1e")
            zone_colors = ["#7f1d1d","#78350f","#14532d","#1e3a5f"]
            zones = [(0,WP),(WP,REFILL_PT),(REFILL_PT,FC),(FC,50)]
            for (lo,hi),c in zip(zones,zone_colors):
                ax.barh(0, hi-lo, left=lo, height=0.55, color=c, linewidth=0)
            ax.barh(0, vwc, height=0.22, color="#38bdf8", linewidth=0,
                    label=f"VWC {vwc:.1f}%")
            ax.axvline(REFILL_PT, color="#fbbf24", linewidth=2, linestyle="--")
            ax.axvline(FC, color="#38bdf8", linewidth=1.5, linestyle=":")
            ax.set_xlim(0, 50); ax.set_ylim(-0.5, 0.9)
            ax.set_xlabel("Volumetric Water Content (%)", color="#64748b", fontsize=8)
            ax.set_title(
                f"VWC  {vwc:.1f}%  {'▲' if vwc>=REFILL_PT else '▼ BELOW'} refill {REFILL_PT:.1f}%",
                color="#f0f6ff", fontsize=10, fontweight="bold", pad=5)
            ax.tick_params(colors="#64748b", labelsize=8)
            ax.spines[:].set_color("#1a3a5c")
            for lbl in ax.get_xticklabels(): lbl.set_color("#64748b")
            ax.set_yticks([])
            ax.text(REFILL_PT+0.5, 0.72, "Refill", color="#fbbf24", fontsize=7, va="top")
            ax.text(FC+0.5, 0.72, "FC", color="#38bdf8", fontsize=7, va="top")
            plt.tight_layout(pad=0.3)
            st.pyplot(fig, width='stretch')
            plt.close(fig)

        # Latest satellite NDVI pass
        if ndvi_data:
            age = ndvi_data["scene_age_days"]
            age_badge = (f'<span style="color:#4ade80">🟢 {age}d old</span>' if age < 5
                         else f'<span style="color:#fbbf24">🟡 {age}d old</span>' if age < 10
                         else f'<span style="color:#f87171">🔴 {age}d old — stale</span>')
            st.markdown(f'<div class="panel-title" style="margin-top:12px">🛰️ Latest Satellite Pass '
                        f'<span style="text-transform:none;letter-spacing:0;font-weight:500">'
                        f'· {ndvi_data["scene_date"]} · {age_badge}</span></div>',
                        unsafe_allow_html=True)
            _ndvi_cols = st.columns([1, 1.4])
            with _ndvi_cols[0]:
                if ndvi_data.get("jpeg_url"):
                    st.image(ndvi_data["jpeg_url"], width='stretch')
            with _ndvi_cols[1]:
                for fname, z in zone_by_field.items():
                    tier_label, tier_clr = ndvi_tier(z["ndvi"])
                    clr = FIELDS[fname]["color"]
                    st.markdown(f"""
<div style="display:flex;justify-content:space-between;align-items:center;
  padding:5px 8px;margin-bottom:4px;background:rgba(6,14,32,.6);border-radius:8px">
  <span style="font-size:.72rem;color:{clr};font-weight:700">{fname}</span>
  <span style="font-size:.68rem;color:#94a3b8">NDVI {z['ndvi']:.2f}</span>
  <span style="font-size:.63rem;color:{tier_clr};font-weight:700">{tier_label}</span>
</div>""", unsafe_allow_html=True)

    # ── RIGHT: Live conditions + radar ───────────────────────────────────────
    with col_stats:
        st.markdown('<div class="panel-title">📡 Live Conditions</div>', unsafe_allow_html=True)
        if weather and vwc:
            delta_vwc = round(vwc - REFILL_PT, 1)
            vwc_cls = "green" if delta_vwc >= 0 else "red"
            st.markdown(f"""
<div class="stats-panel">
  <div style="font-size:.62rem;color:#475569;text-transform:uppercase;letter-spacing:.1em;
    margin-bottom:10px">Current readings · {datetime.now():%H:%M}</div>
  <div class="stat-grid-2">
    <div class="stat-block">
      <div><span class="stat-num red">{weather['temp']}</span><span class="stat-unit-sm">°C</span></div>
      <div class="stat-name">Temperature</div>
    </div>
    <div class="stat-block">
      <div><span class="stat-num {vwc_cls}">{vwc:.1f}</span><span class="stat-unit-sm">%</span></div>
      <div class="stat-name">Soil VWC</div>
    </div>
    <div class="stat-block">
      <div><span class="stat-num amber">{weather['et0']}</span><span class="stat-unit-sm">mm/d</span></div>
      <div class="stat-name">ET₀ FAO-56</div>
    </div>
    <div class="stat-block">
      <div><span class="stat-num purple">{weather['hum']}</span><span class="stat-unit-sm">%</span></div>
      <div class="stat-name">Humidity</div>
    </div>
    <div class="stat-block">
      <div><span class="stat-num">{weather['rain']}</span><span class="stat-unit-sm">mm</span></div>
      <div class="stat-name">Rain Today</div>
    </div>
    <div class="stat-block">
      <div><span class="stat-num orange">{weather['vpd']}</span><span class="stat-unit-sm">kPa</span></div>
      <div class="stat-name">VPD</div>
    </div>
  </div>
  <div style="margin-top:12px;padding:10px;background:rgba(4,8,24,.7);
    border-radius:10px;border:1px solid rgba(56,189,248,.08)">
    <div style="font-size:.58rem;color:#475569;text-transform:uppercase;letter-spacing:.1em">Rain 3-Day Forecast</div>
    <div style="font-family:'JetBrains Mono',monospace;font-size:1.3rem;font-weight:800;
      color:#38bdf8;text-shadow:0 0 12px rgba(56,189,248,.5)">{weather['rain_3d']:.1f}
      <span style="font-size:.68rem;color:#475569;font-family:'Inter',sans-serif">mm</span></div>
    {'<div style="font-size:.67rem;color:#fbbf24;margin-top:2px">⚠️ Rain guard active</div>' if weather['rain_3d']>=20 else '<div style="font-size:.67rem;color:#4ade80;margin-top:2px">✅ No rain guard</div>'}
  </div>
</div>""", unsafe_allow_html=True)

        # Radar chart (matplotlib)
        if weather and vwc:
            st.markdown('<div class="panel-title" style="margin-top:4px">📊 Field Conditions Radar</div>',
                        unsafe_allow_html=True)
            _labels = ["Temp\n(°C)","VPD\n(kPa)","VWC\n(%)","ET₀\n(mm/d)","Hum\n(%)"]
            _raw    = [weather["temp"], weather["vpd"], vwc, weather["et0"], weather["hum"]]
            _norm   = [weather["temp"]/50, weather["vpd"]/3, vwc/50,
                       weather["et0"]/10, weather["hum"]/100]
            N = len(_labels)
            angles = [n/N*2*math.pi for n in range(N)] + [0]
            vals   = _norm + [_norm[0]]
            fig2, ax2 = plt.subplots(figsize=(3.0,3.0), subplot_kw=dict(polar=True),
                                     facecolor="#04080f")
            ax2.set_facecolor("#080f1e")
            ax2.plot(angles, vals, color="#38bdf8", linewidth=2.5)
            ax2.fill(angles, vals, color="#38bdf8", alpha=0.1)
            # Accent dots
            for ang, val in zip(angles[:-1], vals[:-1]):
                ax2.plot(ang, val, 'o', color="#38bdf8", markersize=4,
                         markerfacecolor="#04080f", markeredgewidth=2)
            ax2.set_xticks(angles[:-1])
            ax2.set_xticklabels([f"{l}\n{v:.1f}" for l,v in zip(_labels,_raw)],
                                color="#94a3b8", fontsize=7)
            ax2.set_yticks([]); ax2.spines["polar"].set_color("#1a3a5c")
            ax2.grid(color="#1a3a5c", linewidth=0.7)
            fig2.patch.set_facecolor("#04080f")
            plt.tight_layout(pad=0.2)
            st.pyplot(fig2, width='stretch')
            plt.close(fig2)

        # Valve override quick action
        st.markdown('<div class="panel-title" style="margin-top:4px">⚡ Quick Override</div>',
                    unsafe_allow_html=True)
        qo1, qo2 = st.columns(2)
        with qo1:
            if st.button("🔓 Open All", use_container_width=True, key="quick_open"):
                for t in FIELD_NAMES:
                    st.session_state[f"valve_close_{t}"] = datetime.now()+timedelta(hours=2)
                st.success("All valves open for 2h")
        with qo2:
            if st.button("🔒 Close All", use_container_width=True, key="quick_close"):
                for t in FIELD_NAMES:
                    st.session_state[f"valve_close_{t}"] = datetime.now()-timedelta(seconds=1)
                st.success("All valves closed")

    # ── Full-width: Recent Telemetry ──────────────────────────────────────────
    if st.session_state.telemetry:
        n_rows = len(st.session_state.telemetry)
        st.markdown(
            f'<div class="panel-title" style="margin-top:12px">📡 Recent Telemetry '
            f'<span style="color:#4ade80;font-size:.72rem;font-weight:500;text-transform:none;'
            f'letter-spacing:0">· {n_rows} reading{"s" if n_rows!=1 else ""} · '
            f'auto-logs every 30 min</span></div>',
            unsafe_allow_html=True
        )
        _recent = pd.DataFrame(st.session_state.telemetry[-12:][::-1]).reset_index(drop=True)
        _show_cols = ["Timestamp","Field","VWC (%)","Moisture Source","Temp (°C)",
                      "Humidity (%)","ET₀ (mm/d)","Rain (mm)","Valve","Decision"]
        _available = [c for c in _show_cols if c in _recent.columns]
        st.dataframe(_recent[_available], use_container_width=True, hide_index=True, height=300)
        _cap_col1, _cap_col2 = st.columns([3, 1])
        with _cap_col1:
            drive_lbl = (f"☁️ Also syncing to Google Sheet: [Open Sheet]({drive.sheet_url})"
                         if drive.connected else "💾 Saved locally · Connect Google Drive in ⚙️ Settings")
            st.caption(drive_lbl)
        with _cap_col2:
            if st.button("🔄 Refresh Data", use_container_width=True, key="dash_refresh"):
                st.rerun()
    else:
        st.info("⏳ Collecting first reading — table will appear in a few seconds…")


# ── TAB 2: LIVE DATA ─────────────────────────────────────────────────────────
with tab2:
    if st.session_state.telemetry:
        df=pd.DataFrame(st.session_state.telemetry)

        # Trend chart — VWC over time (matplotlib)
        if len(df)>1:
            _df_plot = df.drop_duplicates("Timestamp").tail(40)
            fig3, ax3 = plt.subplots(figsize=(8,2.4), facecolor="#04080f")
            ax3.set_facecolor("#080f1e")
            ax3.fill_between(range(len(_df_plot)), _df_plot["VWC (%)"],
                             color="#4ade80", alpha=0.12)
            ax3.plot(range(len(_df_plot)), _df_plot["VWC (%)"],
                     color="#4ade80", linewidth=2)
            ax3.axhline(REFILL_PT, color="#fbbf24", linewidth=1.5,
                        linestyle="--", label=f"Refill {REFILL_PT:.1f}%")
            ax3.axhline(FC, color="#38bdf8", linewidth=1,
                        linestyle=":", label=f"FC {FC}%")
            ax3.set_ylim(0,50); ax3.set_xlim(0, max(1, len(_df_plot)-1))
            ax3.set_ylabel("VWC %", color="#64748b", fontsize=8)
            ax3.set_title("Soil Moisture Trend", color="#f1f5f9", fontsize=10, pad=4)
            ax3.tick_params(colors="#64748b", labelsize=7)
            ax3.set_xticks(range(0,len(_df_plot),max(1,len(_df_plot)//6)))
            ax3.set_xticklabels(
                [str(_df_plot["Timestamp"].iloc[i])[-8:-3]
                 for i in range(0,len(_df_plot),max(1,len(_df_plot)//6))],
                color="#64748b", fontsize=7)
            ax3.grid(axis="y", color="#1a3a5c", linewidth=0.6)
            ax3.spines[:].set_color("#1a3a5c")
            ax3.legend(fontsize=7, facecolor="#0d1f2d", labelcolor="#94a3b8",
                       edgecolor="#1a3a5c", loc="upper right")
            plt.tight_layout(pad=0.3)
            st.pyplot(fig3, width='stretch')
            plt.close(fig3)

        # Weather mini-charts (matplotlib)
        c1,c2=st.columns(2)
        with c1:
            _t = df["Temp (°C)"].tail(20)
            fig4, ax4 = plt.subplots(figsize=(4,2), facecolor="#04080f")
            ax4.set_facecolor("#080f1e")
            ax4.bar(range(len(_t)), _t, color="#f87171", width=0.7)
            ax4.set_title("Temperature (°C)", color="#f1f5f9", fontsize=9, pad=3)
            ax4.tick_params(colors="#64748b", labelsize=7)
            ax4.set_xticks([]); ax4.spines[:].set_color("#1a3a5c")
            ax4.grid(axis="y", color="#1a3a5c", linewidth=0.5)
            plt.tight_layout(pad=0.3)
            st.pyplot(fig4, width='stretch')
            plt.close(fig4)
        with c2:
            _e = df["ET₀ (mm/d)"].tail(20)
            fig5, ax5 = plt.subplots(figsize=(4,2), facecolor="#04080f")
            ax5.set_facecolor("#080f1e")
            ax5.bar(range(len(_e)), _e, color="#a78bfa", width=0.7)
            ax5.set_title("ET₀ (mm/day)", color="#f1f5f9", fontsize=9, pad=3)
            ax5.tick_params(colors="#64748b", labelsize=7)
            ax5.set_xticks([]); ax5.spines[:].set_color("#1a3a5c")
            ax5.grid(axis="y", color="#1a3a5c", linewidth=0.5)
            plt.tight_layout(pad=0.3)
            st.pyplot(fig5, width='stretch')
            plt.close(fig5)

        # Table
        n = len(st.session_state.telemetry)
        st.markdown(
            f'<div class="section-title">📋 Telemetry Log '
            f'<span style="color:#4ade80;font-size:.75rem;font-weight:500">'
            f'({n} reading{"s" if n!=1 else ""} collected)</span></div>',
            unsafe_allow_html=True
        )

        # Build display dataframe (latest first, keep key columns)
        display_cols = ["Timestamp","VWC (%)","Moisture Source","Temp (°C)","Humidity (%)",
                        "VPD (kPa)","ET₀ (mm/d)","Rain (mm)","Valve","Decision"]
        dfd = df[display_cols].iloc[::-1].reset_index(drop=True)

        st.dataframe(dfd, use_container_width=True, hide_index=True, height=380)

        st.markdown("<br>", unsafe_allow_html=True)
        csv=io.StringIO(); df.to_csv(csv,index=False)
        col_a,col_b=st.columns([1,4])
        with col_a:
            st.download_button("⬇️ Download CSV",csv.getvalue(),
                f"agrisat_{datetime.now():%Y%m%d_%H%M}.csv","text/csv")
        with col_b:
            drive_status="🟢 Drive synced" if drive.connected else "🔴 Drive not connected"
            st.caption(drive_status)
    else:
        st.info("⏳ Collecting first reading… the table will appear automatically within a few seconds. If this message stays, check your internet connection.")
        if weather:
            st.success(f"✅ Weather OK — Temp {weather['temp']}°C, Humidity {weather['hum']}%, ET₀ {weather['et0']} mm/d")
        else:
            st.error("❌ Weather API not responding — check internet connection")


# ── TAB 3: DATA SOURCES ──────────────────────────────────────────────────────
with tab3:
    st.markdown('<div class="section-title">☁️ Google Drive Sync</div>',unsafe_allow_html=True)
    if drive.connected:
        st.success(f"Connected  ·  Auto-syncing every reading")
        st.markdown(f"[📄 Open Google Sheet]({drive.sheet_url})")
        if st.button("⬆️ Force Push Now",use_container_width=True):
            drive.flush_pending(); st.success("Pushed!")
    else:
        st.info("Upload your service-account JSON key to enable Drive sync.")
        kf=st.file_uploader("Service Account JSON",type="json",label_visibility="collapsed")
        sid=st.text_input("Sheet URL or ID",value=SHEET_ID)
        if st.button("🔗 Connect Drive",use_container_width=True) and kf:
            cd=json.load(kf); ok,res=drive.connect(cd,sid)
            if ok: st.success(f"Connected! [Open Sheet]({res})")
            else: st.error(res)
    st.divider()
    st.markdown(f"""
**Service account:** `agrisat-logger@agriculture-land-473011.iam.gserviceaccount.com`
**Project:** `agriculture-land-473011`
**Sheet:** [Data Of Aero Drip Ai](https://docs.google.com/spreadsheets/d/{SHEET_ID})
**Tab rotation:** Every 50,000 rows (auto)
""")


# ── TAB 4: SETTINGS ──────────────────────────────────────────────────────────
with tab4:
    c1,c2=st.columns(2)
    with c1:
        st.markdown('<div class="section-title">🌾 Field & Crop</div>',unsafe_allow_html=True)
        st.caption("These settings apply equally to **all 3 fields** (same crop, same drip system).")
        crop=st.selectbox("Plant Type (all fields)",list(CROPS.keys()),
            index=list(CROPS.keys()).index(st.session_state.get("crop","Ornamental/Shade")))
        area=st.number_input("Area per field (ha)",value=st.session_state.get("area",0.05),
                             min_value=0.01,step=0.01,
                             help="Each boundary zone is ~500 m² ≈ 0.05 ha by default")
        st.session_state["crop"]=crop; st.session_state["area"]=area
        st.markdown('<div class="section-title" style="margin-top:16px">🗺️ Field Positions</div>',unsafe_allow_html=True)
        for fn,fi in FIELDS.items():
            st.caption(f"**{fn}**: {fi['lat']:.4f}°N, {fi['lon']:.4f}°E")
        st.markdown('<div class="section-title" style="margin-top:16px">🧪 Soil Parameters</div>',unsafe_allow_html=True)
        st.caption(f"Field Capacity: **{FC}%** · Wilting Point: **{WP}%** · Refill: **{REFILL_PT:.1f}%** · TAW: **{TAW}%**")
        c=CROPS[crop]; st.caption(f"Plant Kc: **{c['Kc']}** · Root depth: **{c['root_m']}m**")

    with c2:
        st.markdown('<div class="section-title">💧 Irrigation Hardware</div>',unsafe_allow_html=True)
        st.caption(f"Drip rate: **{DRIP_RATE_MM_HR} mm/hr** · Efficiency: **{DRIP_EFF*100:.0f}%**")
        ov_field = st.selectbox("Select field to override", FIELD_NAMES + ["All Fields"], key="ov_field")
        oc1, oc2 = st.columns(2)
        with oc1:
            if st.button("🔓 Open Valve", use_container_width=True):
                targets = FIELD_NAMES if ov_field=="All Fields" else [ov_field]
                for t in targets: st.session_state[f"valve_close_{t}"] = datetime.now()+timedelta(hours=2)
                mqtt_ok = publish_valve_cmd(1, True)
                st.success(f"Opened {ov_field} for 2h" + (" — physical valve command sent" if mqtt_ok else " (MQTT publish failed — check broker connectivity)"))
        with oc2:
            if st.button("🔒 Close Valve", use_container_width=True):
                targets = FIELD_NAMES if ov_field=="All Fields" else [ov_field]
                for t in targets: st.session_state[f"valve_close_{t}"] = datetime.now()-timedelta(seconds=1)
                mqtt_ok = publish_valve_cmd(1, False)
                st.success(f"Closed {ov_field}" + (" — physical valve command sent" if mqtt_ok else " (MQTT publish failed — check broker connectivity)"))
        st.caption(f"🔌 Sends the same MQTT command the website's Cloud MQTT panel uses (farm: `{MQTT_FARM_ID}`) — brain.py on the Pi receives it and drives the real relay.")

    # ── Drive Connection Panel (full-width row) ──────────────────────────────
    st.markdown("---")
    st.markdown('<div class="section-title">☁️ Google Drive / Google Sheet Connection</div>', unsafe_allow_html=True)
    if drive.connected:
        dc1, dc2, dc3 = st.columns([2, 2, 1])
        with dc1:
            st.success("✅ Drive Connected — Auto-syncing every reading")
            st.markdown(f"📄 **Sheet:** [Data Of Aero Drip Ai]({drive.sheet_url})")
            st.caption(f"Service account: `agrisat-logger@agriculture-land-473011.iam.gserviceaccount.com`")
        with dc2:
            new_sid = st.text_input("Switch to a different Sheet URL or ID", value="", key="new_sid_input",
                                    placeholder="Paste new spreadsheet URL or ID here")
            new_kf  = st.file_uploader("Upload new service-account JSON (optional)", type="json", key="new_sa_kf")
        with dc3:
            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("🔄 Reconnect / Switch Sheet", use_container_width=True):
                sid_val  = new_sid.strip() or SHEET_ID
                cred_src = json.load(new_kf) if new_kf else json.load(open(SA_KEY))
                ok, res  = drive.connect(cred_src, sid_val)
                if ok: st.success(f"Reconnected! [Open Sheet]({res})")
                else:  st.error(res)
            if st.button("⬆️ Force Push All Pending", use_container_width=True):
                drive.flush_pending(); st.success("All pending rows pushed!")
    else:
        dc1, dc2 = st.columns([1, 1])
        with dc1:
            st.warning("⚠️ Drive not connected — data is saved locally only")
            st.markdown("**To connect:**")
            st.markdown("1. Upload your `agrisat_service_account.json` below  \n"
                        "2. Enter your Google Sheet URL or ID  \n"
                        "3. Click **Connect**")
            st.caption("The sheet must be shared (Editor) with:  \n"
                       "`agrisat-logger@agriculture-land-473011.iam.gserviceaccount.com`")
        with dc2:
            drive_kf  = st.file_uploader("Service Account JSON key file", type="json", key="drive_kf_settings")
            drive_sid = st.text_input("Google Sheet URL or ID", value=SHEET_ID, key="drive_sid_settings")
            if st.button("🔗 Connect to Google Drive", use_container_width=True):
                if drive_kf:
                    cred_data = json.load(drive_kf)
                    ok, res   = drive.connect(cred_data, drive_sid)
                    if ok: st.success(f"✅ Connected! [Open Sheet]({res})")
                    else:  st.error(f"Connection failed: {res}")
                else:
                    # Try auto-connect from saved key
                    if os.path.exists(SA_KEY):
                        ok, res = drive.connect(json.load(open(SA_KEY)), drive_sid)
                        if ok: st.success(f"✅ Auto-connected! [Open Sheet]({res})")
                        else:  st.error(f"Auto-connect failed: {res}")
                    else:
                        st.error("No JSON key file uploaded and no saved key found.")


# ─── Auto-refresh ─────────────────────────────────────────────────────────────
# Inject a silent JS timer that reloads the page every 30 minutes.
# This does NOT block the script or cause continuous reruns — the page
# stays completely static until the timer fires or the user interacts.
st.html("""
<script>
  // Reload page every 30 minutes for a fresh weather/satellite/telemetry read
  setTimeout(function(){ window.location.reload(); }, 30 * 60 * 1000);
</script>
""", unsafe_allow_javascript=True)
