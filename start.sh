#!/bin/bash
# AgriSat Dediapada — Start app + public tunnel
cd "$(dirname "$0")"

echo "⏹  Stopping any running instances..."
pkill -f "streamlit run app.py" 2>/dev/null
pkill -f "cloudflared tunnel" 2>/dev/null
sleep 1

echo "🚀 Starting Streamlit app..."
.venv/bin/streamlit run app.py > /tmp/streamlit_out.log 2>&1 &
APP_PID=$!

echo "🌐 Starting Cloudflare tunnel..."
./cloudflared tunnel --url http://localhost:8501 > /tmp/cf_tunnel.log 2>&1 &
CF_PID=$!

echo "⏳ Waiting for tunnel URL..."
for i in $(seq 1 20); do
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/cf_tunnel.log 2>/dev/null | head -1)
    if [ -n "$URL" ]; then break; fi
    sleep 1
done

echo ""
echo "════════════════════════════════════════════════════"
echo "  ✅ AgriSat Dediapada is running!"
echo ""
echo "  📱 Public URL (any device, any network):"
echo "     $URL"
echo ""
echo "  🏠 Local network:"
echo "     http://$(ipconfig getifaddr en0):8501"
echo ""
echo "  💻 This Mac:"
echo "     http://localhost:8501"
echo ""
echo "  Press Ctrl+C to stop everything"
echo "════════════════════════════════════════════════════"

# Keep running and show URL again every 30 seconds
trap "pkill -f 'streamlit run app.py'; pkill -f 'cloudflared tunnel'; echo 'Stopped.'; exit" INT TERM
wait $APP_PID
