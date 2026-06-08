#!/bin/bash
# AgriSat Dediapada — background launcher (used by launchd auto-start)
# This script is different from start.sh (no interactive output)

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# Kill any stale instances from a previous run
pkill -f "streamlit run app.py"  2>/dev/null
pkill -f "cloudflared tunnel"    2>/dev/null
sleep 2

echo "[$(date)] Starting AgriSat app..." >> /tmp/agrisat.log

# Start Streamlit
"$DIR/.venv/bin/streamlit" run "$DIR/app.py" >> /tmp/agrisat_streamlit.log 2>&1 &
SPID=$!
echo "[$(date)] Streamlit PID $SPID" >> /tmp/agrisat.log

# Start Cloudflare quick tunnel (URL saved to log, shown in app header)
echo "[$(date)] Starting Cloudflare tunnel..." >> /tmp/agrisat.log
"$DIR/cloudflared" tunnel --url http://localhost:8501 >> /tmp/agrisat_cf.log 2>&1 &
CFPID=$!
echo "[$(date)] Cloudflared PID $CFPID" >> /tmp/agrisat.log

# Wait — launchd keeps this script alive; if it exits, KeepAlive restarts everything
wait $SPID
