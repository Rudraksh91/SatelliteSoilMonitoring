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
AMgAAA==
