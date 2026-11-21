#!/usr/bin/env bash
# ============================================================================
#  AgriSat Pi gateway — ONE-FILE INSTALLER (self-extracting)
#
#  Copy this single file to the Raspberry Pi and run:
#       sudo bash install_agrisat_pi.sh
#
#  It installs everything: system packages, a Python environment, the gateway program,
#  a systemd service that starts at boot and restarts on failure, a daily self-update,
#  a random command key, and the `agrisat` helper command. Safe to run again (upgrade).
#  Nothing is irrigated automatically until you enable auto mode (see the summary at the end).
# ============================================================================
set -euo pipefail

# ---- test hooks (not needed on a real Pi) ----
PREFIX="${AGRISAT_PREFIX:-}"            # install under this root directory
SKIP_SYSTEM="${AGRISAT_SKIP_SYSTEM:-0}" # 1 = no apt / useradd / chown / systemctl

APP_DIR="$PREFIX/opt/agrisat"
ETC_DIR="$PREFIX/etc/agrisat"
LOG_DIR="$PREFIX/var/log/agrisat"
STATE_DIR="$PREFIX/var/lib/agrisat"
BIN_DIR="$PREFIX/usr/local/bin"
UNIT_DIR="$PREFIX/etc/systemd/system"
ENV_FILE="$ETC_DIR/agrisat.env"
SERVICE_USER="agrisat"

# ---- options ----
FORCE_AUTO=0
for arg in "$@"; do
  case "$arg" in
    --auto) FORCE_AUTO=1 ;;   # fully automatic: start in AUTO and run on the automatic estimates (see summary)
    -h|--help) sed -n '2,12p' "$0"; echo "Option:  --auto   start in AUTO mode and do not require calibrated soil/crop values"; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 1 ;;
  esac
done

say()  { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[error]\033[0m %s\n' "$*" >&2; exit 1; }

if [ "$SKIP_SYSTEM" != "1" ]; then
  [ "$(id -u)" -eq 0 ] || die "Please run with sudo:  sudo bash $0"
  command -v systemctl >/dev/null || die "systemd not found — this installer targets Raspberry Pi OS / Debian."
fi

# ---- 1. system packages ----
if [ "$SKIP_SYSTEM" != "1" ] && command -v apt-get >/dev/null; then
  say "Installing system packages (needs internet)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y || warn "apt-get update failed — continuing with what is installed"
  apt-get install -y python3 python3-venv python3-pip ca-certificates curl openssl || die "could not install base packages"
  # GPIO / SPI bindings: from apt on Raspberry Pi OS (build-free); harmless to skip on other boards
  apt-get install -y python3-rpi.gpio python3-spidev 2>/dev/null || warn "python3-rpi.gpio / python3-spidev not available here (fine if this is not a Pi)"
fi

# ---- 2. service user + folders ----
if [ "$SKIP_SYSTEM" != "1" ]; then
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    say "Creating service user '$SERVICE_USER'"
    useradd --system --home-dir /var/lib/agrisat --shell /usr/sbin/nologin "$SERVICE_USER"
  fi
  for g in gpio spi i2c dialout; do
    getent group "$g" >/dev/null 2>&1 && usermod -aG "$g" "$SERVICE_USER" || true
  done
fi
mkdir -p "$APP_DIR" "$ETC_DIR" "$LOG_DIR" "$STATE_DIR" "$BIN_DIR" "$UNIT_DIR"

# ---- 3. unpack the gateway program (embedded at the end of this file) ----
say "Installing the gateway program"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
sed '1,/^__PAYLOAD__$/d' "$0" | base64 -d | tar -xz -C "$TMP"
[ -f "$TMP/brain.py" ] || die "installer payload is damaged (brain.py missing)"
install -m 0644 "$TMP/brain.py" "$APP_DIR/brain.py"
install -m 0644 "$TMP/VERSION"  "$APP_DIR/VERSION"

# ---- 4. python environment ----
say "Setting up the Python environment"
python3 -m venv --system-site-packages "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip || warn "pip upgrade skipped"
"$APP_DIR/venv/bin/pip" install --quiet paho-mqtt || warn "paho-mqtt not installed (dashboard link will be disabled until it is)"
# optional hardware libraries — the gateway runs without them (simulation / disabled features)
"$APP_DIR/venv/bin/pip" install --quiet pyLoRa      >"$LOG_DIR/pip-optional.log" 2>&1 || warn "pyLoRa not installed (LoRa radio disabled) — see $LOG_DIR/pip-optional.log"
"$APP_DIR/venv/bin/pip" install --quiet Adafruit_DHT >>"$LOG_DIR/pip-optional.log" 2>&1 || warn "Adafruit_DHT not installed (no local temperature sensor; normal on newest Pi OS) — see $LOG_DIR/pip-optional.log"

# ---- 5. settings + command key (kept on upgrade) ----
NEW_KEY=""
PRESET_KEY=""; PRESET_AUTO=0
if [ -f "$TMP/preset.env" ]; then
  PRESET_KEY="$(grep '^AGRISAT_CMD_KEY=' "$TMP/preset.env" | cut -d= -f2- || true)"
  grep -q '^AGRISAT_AUTO_START=1' "$TMP/preset.env" && PRESET_AUTO=1
fi
[ "$PRESET_AUTO" = 1 ] && FORCE_AUTO=1
if [ ! -f "$ENV_FILE" ]; then
  say "Creating settings with a command key"
  NEW_KEY="${PRESET_KEY:-$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')}"
  cat > "$ENV_FILE" <<EOF
# AgriSat gateway settings — edit, then:  sudo agrisat restart
# Secret that authenticates dashboard commands. Type the same value into the dashboard's CMD KEY button.
AGRISAT_CMD_KEY=$NEW_KEY
# 0 = start in MANUAL mode after every reboot (recommended). 1 = start in AUTO.
AGRISAT_AUTO_START=0
# Earliest hour (IST) the day's irrigation may start; the daily decision is published ~07:00 IST.
AGRISAT_START_HOUR=8
# Minutes per zone per day if the decision file is older than 48 h (0 = no irrigation).
AGRISAT_STALE_DEFAULT_MIN=0
# 1 = refuse to run valves while soil/crop values in config/farm_config.json are still estimates.
AGRISAT_REQUIRE_CALIBRATED=1
# Optional: GitHub token (contents:write on the repo only) so the Pi can send confirmed runs back.
#AGRISAT_GITHUB_TOKEN=
# 1 = download the newest gateway program from GitHub each night (with rollback). 0 = never.
AGRISAT_AUTO_UPDATE=1
EOF
else
  say "Keeping your existing settings and command key"
fi
if [ "$FORCE_AUTO" = 1 ]; then
  say "FULLY AUTOMATIC mode requested"
  sed -i.bak 's/^AGRISAT_AUTO_START=.*/AGRISAT_AUTO_START=1/; s/^AGRISAT_REQUIRE_CALIBRATED=.*/AGRISAT_REQUIRE_CALIBRATED=0/' "$ENV_FILE" && rm -f "$ENV_FILE.bak"
fi
if [ "$SKIP_SYSTEM" != "1" ]; then
  chown -R "$SERVICE_USER:$SERVICE_USER" "$LOG_DIR" "$STATE_DIR"
  chown root:"$SERVICE_USER" "$ETC_DIR" "$ENV_FILE"; chmod 750 "$ETC_DIR"; chmod 640 "$ENV_FILE"
fi

# ---- 6. self-update script (verifies, replaces, restarts, rolls back on failure) ----
cat > "$APP_DIR/update.sh" <<'EOF'
#!/usr/bin/env bash
# Fetch the newest gateway program; keep it only if it compiles AND the service starts with it.
set -uo pipefail
APP=/opt/agrisat
[ -n "${AGRISAT_PREFIX:-}" ] && APP="$AGRISAT_PREFIX/opt/agrisat"
ENVF="${AGRISAT_PREFIX:-}/etc/agrisat/agrisat.env"
[ -f "$ENVF" ] && { set -a; . "$ENVF"; set +a; }
[ "${AGRISAT_AUTO_UPDATE:-1}" = "1" ] || { echo "auto-update disabled"; exit 0; }
URL="${AGRISAT_UPDATE_URL:-https://raw.githubusercontent.com/Rudraksh91/SatelliteSoilMonitoring/main/hardware/raspberry_pi/brain.py}"
NEW="$(mktemp)"; trap 'rm -f "$NEW"' EXIT
curl -fsSL --max-time 60 "$URL" -o "$NEW" || { echo "download failed — keeping current version"; exit 0; }
"$APP/venv/bin/python" -m py_compile "$NEW" || { echo "downloaded file does not compile — keeping current version"; exit 0; }
grep -q "TERRA-CORE" "$NEW" || { echo "downloaded file is not the gateway — keeping current version"; exit 0; }
if cmp -s "$NEW" "$APP/brain.py"; then echo "already up to date"; exit 0; fi
cp "$APP/brain.py" "$APP/brain.py.previous"
install -m 0644 "$NEW" "$APP/brain.py"
date -u +"updated %Y-%m-%dT%H:%M:%SZ" > "$APP/VERSION"
[ "${AGRISAT_NO_SYSTEMCTL:-0}" = "1" ] && { echo "updated (service restart skipped)"; exit 0; }
systemctl restart agrisat
sleep 25
if systemctl is-active --quiet agrisat; then echo "updated and running"; else
  echo "new version failed to start — rolling back"
  install -m 0644 "$APP/brain.py.previous" "$APP/brain.py"
  systemctl restart agrisat
fi
EOF
chmod 0755 "$APP_DIR/update.sh"

# ---- 7. helper command ----
cat > "$BIN_DIR/agrisat" <<'EOF'
#!/usr/bin/env bash
ENVF=/etc/agrisat/agrisat.env
case "${1:-help}" in
  status)  systemctl status agrisat --no-pager; echo; cat /opt/agrisat/VERSION ;;
  logs)    journalctl -u agrisat -n "${2:-80}" --no-pager; echo; tail -n 20 /var/log/agrisat/brain.log 2>/dev/null ;;
  follow)  journalctl -u agrisat -f ;;
  restart) systemctl restart agrisat && echo restarted ;;
  key)     grep '^AGRISAT_CMD_KEY=' "$ENVF" | cut -d= -f2- ;;
  auto)    case "${2:-}" in
             on)  sed -i 's/^AGRISAT_AUTO_START=.*/AGRISAT_AUTO_START=1/' "$ENVF"; systemctl restart agrisat; echo "auto mode ON at start-up" ;;
             off) sed -i 's/^AGRISAT_AUTO_START=.*/AGRISAT_AUTO_START=0/' "$ENVF"; systemctl restart agrisat; echo "starts in MANUAL (recommended)" ;;
             *)   echo "usage: sudo agrisat auto on|off" ;;
           esac ;;
  update)  /opt/agrisat/update.sh ;;
  edit)    "${EDITOR:-nano}" "$ENVF"; echo "now run: sudo agrisat restart" ;;
  uninstall)
    systemctl disable --now agrisat agrisat-update.timer 2>/dev/null
    rm -f /etc/systemd/system/agrisat.service /etc/systemd/system/agrisat-update.service /etc/systemd/system/agrisat-update.timer /usr/local/bin/agrisat
    systemctl daemon-reload; rm -rf /opt/agrisat
    echo "removed program + services. Settings kept in /etc/agrisat, data in /var/lib/agrisat (delete them by hand if you want)." ;;
  *) cat <<'USAGE'
agrisat status | logs [n] | follow | restart | key | auto on|off | update | edit | uninstall
(run with sudo)
USAGE
  ;;
esac
EOF
chmod 0755 "$BIN_DIR/agrisat"

# ---- 8. systemd units ----
cat > "$UNIT_DIR/agrisat.service" <<'EOF'
[Unit]
Description=AgriSat Pi irrigation gateway
After=network-online.target time-sync.target
Wants=network-online.target time-sync.target

[Service]
User=agrisat
Group=agrisat
EnvironmentFile=/etc/agrisat/agrisat.env
WorkingDirectory=/opt/agrisat
ExecStart=/opt/agrisat/venv/bin/python /opt/agrisat/brain.py
Restart=always
RestartSec=10
# Valves must close if the program is killed: brain.py initialises the relay pin LOW (closed) on start.
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
EOF
cat > "$UNIT_DIR/agrisat-update.service" <<'EOF'
[Unit]
Description=AgriSat gateway self-update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/opt/agrisat/update.sh
EOF
cat > "$UNIT_DIR/agrisat-update.timer" <<'EOF'
[Unit]
Description=Nightly AgriSat gateway self-update

[Timer]
OnBootSec=10min
OnCalendar=*-*-* 03:30:00
RandomizedDelaySec=20min
Persistent=true

[Install]
WantedBy=timers.target
EOF

# ---- 9. start ----
if [ "$SKIP_SYSTEM" != "1" ]; then
  systemctl daemon-reload
  systemctl enable agrisat.service agrisat-update.timer >/dev/null
  systemctl restart agrisat.service
  systemctl start agrisat-update.timer
  sleep 3
  systemctl is-active --quiet agrisat && STATE="RUNNING" || STATE="NOT RUNNING — check: sudo agrisat logs"
else
  STATE="(system steps skipped)"
fi

VER="$(cat "$APP_DIR/VERSION")"
cat <<SUMMARY

============================================================
 AgriSat Pi gateway installed — $VER
 Service: $STATE
============================================================
 What happens now, with no further action:
   • starts at every boot and restarts itself if it crashes
   • pulls the daily irrigation decision from GitHub (decision file > 48 h old = it stops)
   • updates its own program each night (rolls back if the new one will not start)
SUMMARY
if [ "$FORCE_AUTO" = 1 ]; then
cat <<AUTOBOX
   • FULLY AUTOMATIC: starts in AUTO and follows the daily decision, using the automatic soil estimate
     (not lab-calibrated) until you set real values. Limits still apply: 10 min minimum, 120 min per run,
     240 min per zone per day, rain hold, stale-file stop, E-stop, and valves close on any stop/restart.
AUTOBOX
else
cat <<MANBOX
   • starts in MANUAL mode: it will NOT irrigate until you enable auto mode  (sudo agrisat auto on)
MANBOX
fi
if [ -n "$NEW_KEY" ]; then
cat <<KEYBOX

 YOUR COMMAND KEY (shown once — type it into the dashboard's CMD KEY button):
   $NEW_KEY
 You can show it again any time with:  sudo agrisat key
KEYBOX
fi
cat <<NEXT

 Useful commands (use sudo):  agrisat status | logs | key | auto on|off | update | edit | uninstall
 Valve relay: wired to GPIO23 (active-HIGH); LoRa module on SPI0 — see hardware/WIRING_GUIDE.txt
============================================================
NEXT
exit 0
__PAYLOAD__
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
Iyfl8w1WqYZvTnr0J4KKPdTw3anuA1aGIevtjvthkUKHzVoyxV//8n8/Gz8eKKFt71ImMJmCPSlX
z2W1ImUZqa3xbUzyniYXBK1o5URLspJI4k0TkyuKH7MwX9DaVlaTqbo9w9TKz99oklV+kq+LYMh4
WSD9ppKJ/piCJrVSEQS5eZnvkFh113HRJqeDCnXv+luhDaeStssJqYVmGS9v24y+0MoKwbMdHKlx
tNBcsona6qzEDFdcDhOMU1B13MeK+2Dm3sysPG4YxrdVTdPrONrWZRX2tcQD+8qO6k64nDcj08KP
1pj28hUSWcnImMP/TRSoTftrq4oTTdnSF/laNCWW+iYo3L9ZoPdBvKKzlAlwSJwuNC4pd95UHxZO
uYuk9DVyvVd0la7RU/gamcOu6QuDiKeaYpqu1CQnVmeMlCu6y8npiKEHLGugaZpTW7dGWvU2V/Tm
XcJ4Suvu36TVICXDQIdLYrocwa1XpFmNE1IDk/GnooDlCkTStcLzt54UEEnuYlnRQTjHNe7cRcHC
sLI/qxqnq75XWxWFK8p27HyoVO5xAbQn6tXUSwSNYx2FZ1WWW3hJICVRaquOFH3OPuJYFWAaFOlf
Vm2e+tSErHo17ersGdGzb7MptLf2GsBW7PcHgFsEo3CY7h0pU05pxT97Oz9l7yzqel/7ZGXFKKmf
y8/f6BJS/y3K2B91Do7V4cnJmy/Yqj+MJ8tkHN7MYXudL2cN0iqJj2ZCl4haY7YhrOv8LeX0D16f
dntH7EzcTaLp2q65uFdIsFhoxJo9JTXWt+oeLwivD09+Uuc+yrxAHeJ0OIkuTr1d3jwrjGzYJFcy
46Crqcf1NiQlSmi/hG+FlwQ+FNNLOxirYN36ARLaQAzGU3UnM783tqOxjrsMUdGZy5uI2YSno6HV
cmxORKy2snzKGTNw5TuqkJ/1/VCoIP1A0DMeN+XOBedd5/Bdl1/AtRkNeUa74JbfQPrXPHeB0cMx
vGntc4ly7zPCdGmdkMgllkKalpHjZdGb8o+jv2l0amTo6D7Y+OD4tNQ2s479c89VvQiOUjS3Xmdt
96TXhXDHuyyOVMKkTLuyVDLSEEhfUAd7tOmpfvtfbprar5bezpV9F656uQhC+1exBuhZZLX7rR/y
nme4Ycoha3n3hMkBN761ztvTkyHnHu9sSBCLXmdwUzy0r/Iqm8C1edX02rdePuocv+0cSsydrgJb
VYEKORfB1MrO5grvKKE0rqrDUi8bvbnyUtV83KbqITTwIpxOw5uq+k5WZ2n1bL1PLHSY1kOp07Oj
Wtkzo3lz9KHKP9Py1RBRsPknafKfKr5jqk2mT2qZiwden7ZdXq9QS9a6jYhvzTGX9Qhz2WrF5RtX
y3C+zJX4FuXevs4pb7GsmCbKeVqQcFGtquCLFCyJMYoqErJ1rS59xzwpZe6KGRR2yCo31Ou+6nX7
+0X/YrkKWOVju+pleeIlvChM/Z1dIDkrn+9wVFn+3hTUG50nsftdWm5U8pJJV+NaMWz9tHhW1SUA
K9Ymh6HY3eLV1iuKPZfnm+upNNsewi+zA50VVNLFlVeiX4oQqCZXjK4uBQat3oYySEKQUuMyEyBL
Z9blZFExb7V92p/zG5o4S1pX/qyxgY3oVkaKNGbHD+8INqN6Kywz3spdKM5WcrSmvr9wNgi8ADdw
wrYzHHJph+EQ4sRwWK9IdxBBw5a7fvRvefyDOcmI0XKRVNJ7LXj1SfBaiKkf7sa87yO9kBNiYHb/
X37ecgFk4tOIkKnd2lf/zM+7bg+k4+86Ros+z7e3+V/6FP9ttb7dyP7m379tbWx9pVr/iAVYgvvT
kF/99/yY3IpInS+DaaI2W5vP1zY21jY3Tlub7c3t9uaLX0RmgKEMSc/es80Lb1z76vHz+Hn8PH4e
P4+fx8/j5/Hz+Hn8PH4eP4+fx8/j5/Hz+Hn8PH4eP4+fx8/j5/Hz+Hn8PH4eP4+fx8/j5/Hz+Hn8
/LM//w+V3Sj/APAAAA==
