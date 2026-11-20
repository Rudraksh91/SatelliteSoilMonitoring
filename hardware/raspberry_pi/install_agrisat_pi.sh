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
if [ ! -f "$ENV_FILE" ]; then
  say "Creating settings with a new random command key"
  NEW_KEY="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
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
   • starts in MANUAL mode: it will NOT irrigate until you enable auto mode
SUMMARY
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
H4sIAK93AGsC/+1923LbRrZonvkVPXClDOxQvEjyJUzoKVqiLW5LooaUnUlULBRIQCRiEmAAULKi
UdU8nedTp/b5gfNwPixfctalG2hcKF8yk9nHY1ZmLBKN7tXdq9d9rZ5Gjh801jdf/RM/Lfg83t+n
f+GT/7e9+3h3d1f9Jn9/st9+8pVoffUHfDZx4kQw5Ff/np8Hf2pu4qg59YOmF1yJ9U2yCIO9mmEY
td/+9//8zP6rnfdHo97OwXDUF7155I+dRPz29/8SIydeT70ouhFnvniOJ+JznHztfOHHAv5LFp6Y
eUESOUvhB4m3XPpzL5h5IrykZ34U+XMn8cNAxDdx4q0atdrIi9dhEPtTf+knvhd3akK0G+I4dFwR
O9RH4onTwzcD0RRx6C+F6ySOMC+jcCUC98q3F0505UFvkQg3yXqTiEt/6cUW9LPbECMP+lmGM4Do
2nMAiEjEXhCHUSzMw6Pz3V0RRuL5SX/3aQvf2IM3NkERWNeb+TH+4QVzP/CEeb3wguYivIaeg7lI
QtXYwz72G2LsBa64cpZXsB7hauUEboytsNsTQALRH5/t7YqjzVRc+Q7MdQQTGv+1vfvkMUwSEKm1
ix09aoizTbwQzlJOWnahIZvrxItp6EQudXTyl/NzYc6W4cYVzmzmxbQKj3E158K78iI4hD4D7Prx
W3EJc3c2rp/UakfQx7UTeSLwPNdzBUz2zMe92BF5wBjaVehulh62Gp8NWsJc+0Espt4yvLboHV5a
Xmls9fJsMNwXZrjG9YS9wMPBixCErhfDFONQLBxYL7k73M2JP4vC8SE8jmAfbxgq2HvodBnOYS/m
Vq1GAF37Ec7MzJ24/dYOACYW8K4XWTgbavvm4ECI3/7H/4ImgWg/EeZeY++NpZ6+PD3Mnu7C3OCH
9OH44JX2cA8ewsTabVgcXAd7fHD8Km17MhgPs7ZtbvutUG3xcdZ2OB5kIH0r+22lbeFx2vZ0PNZg
2Oe2T9N+D/qttOnhYNjKmu5y091H6fPR+FxbCQli+wks6tpfwxEGFrZc4rrB1z31XaydRbiz+iVJ
xOjMb+ArIl77rodUnrrtuc5ltPGTHUADOOGbgLpgDiCmUjCAnx6IISAg7D7senTlz7yOiDduKInD
LFnCgXOmgGcO0FQgBjvTz5WEIlus+at1GCVAMZZLb4YHJU5/iq/Un5sIKOK0sXai2Cv8Fnm/bIAQ
ql8XQBvg5/Trypmpv3+Ow0D9HaajxP4czqb6lvirdAB52GpEdIEUefhQyIfqe53+qtOLrrdMHP7z
1zDw+MU10F8ASL13Bl9rtQfit//6O/wngIlE7g7MK7kRpkK1az9ZEPJZstk/+79aEt0gtgoFJSJ7
A5G9MVv6wNsQX/GrzV+p6VFvbBPt7YrzaOPVvHczb52IAfXQj6Iw6hTbvQCK59GPayBciWlc/NAb
nU60sxWEiTpxQI+RYDJhz0g+0HA8Ha4B57UItjqCNh5BNTb8/SEgcrNtEOo9VwC5iZEOx/5qswRs
cBXXzcFI2EBs5V2DCIYE+j9KT2mi9iwMLv25avV82BsdpsAeD0e9D5mUbLdtVjxexXwIPrXSKWJ2
ChSR6J6Fk0zx+WxxE/sodyRwJqUwYLp+BEcbeROSzTqMxwNc+t7SJV4obrykCtmh4yGIHpG3dG6+
icOlF4S+i2wPoIpB4vLni4QFBBDEoH/kkQvF1KfIHHc2a5wRdBR7XvqsGSmOaa/9JsJqE6xAoakP
FDhgkoHrgKzjiYPjQV3AN+jFWa+h0UM4Dk4yI7ki9hJ7LafNvZhW1okDJAPYcXMdLp3IT24atQfQ
y+nwvN+hBs4s2cBy4cI0oKfNGl6eLR1/BbCu1yAWwHLDf74E0J6v/dC06mJGmwVdEeKsgEPAi70X
ICVx62UYOabVkFsM8BICZUPA6zG8rcYF0cYjPth4fnBiAetBgQ0n4SexCK8DQbIOooYbkjC1AGEf
ORhgqctU8wFsE/QUiyXC+DAmYThCuQfWaQ3TCVxkatA8BlE1SJY3wo3CNTDC4EZOeQUiC/Qz9WBk
oLRJXVwv/NkCpWzvHawUvHKNA/PS4DkDkZQ3H0Wea6BS1BE8gdOHXUFjjbKDmIiHKsIGKDo2gec2
3wKQ3rLpetPNvImgd2hVQM5ZhoDDsADQD8OHG7YKV0gQtUUWkRMQpDA9xOuH2ooBEt3AYBIqq1F7
0zt+07dxAPtscApnc3evRMZSAQPmg/+mx5l+/IBjL9ttO/Zp/3jwnSvHX5K8gbu7rji/OsV1vcsy
NkrgL6k/NT7/iJ8IcC4K6Gs6U/xU417pMZzXAFAuNmk65eeAz/lFrfOj4evzOsHqO8su/XI8/MFC
+YvICSJV7+B88Ka/czR4edQR8BTWjJ+Fl5fwt9RlAA3gpKlhQSpo+MFlaF4aF2+wwYSGu83DcJci
Ih6iilU1eCJyF/v0DypbsOVeJzeYh3tbGI3mJS5h5zy3I269O9ybPBGyQzgiCgkEThvo5ewtEk4P
5A1UMm+YeCABtknVQgxAMrneTJd+vLBTrmvKra+gdTiM3RHTMFxKRJgvwynMtQIcxsVKMKkbhUdl
HKL9Y023erdxD/FV6kd4OOd0z4k/fX7i8wNxMDx9MXj5etQ7HwxPP89JZqLFCydaCVC4gsRHYXm1
gaNETFgTDr+R+jULjUjpkdtXShYveqMTewBaLyCfAcq/76wd19m5hFF2Wm1DG5iE12kUAqP4g+Tx
3yXLI7j20RCVXJgZw91Y+Ffe6pfGLFwZ3OBsOKIG7adP92rnw7PBgX3YO0dh8dKQimfzVq7RXROp
gyGbHZwcii3NZivXoOP6QAD9SDwWIVZOgCJOtkvhFdA02MlY9kh2ruoe0dZlUIdrNApRSwdkCiAh
sbZFB2xvEs4GWDQgyIxNWP/6zXgAGp6nkAfFFKSsM2GWtgVEujhEUQjlzVm4AcFYUmGUb9OVb4g+
WrSUgQ36p3OAygYLTTMHzUBOII5Oegc746Pe7qPHrOMCE/ZIwmL9EoQ3kIaJHcyAQ7NEtwR4YjE8
Pf4RrU4oWMEIaHxCa/aVE4ney9Fg3DtHUOxX/R8tkud8th3i0fEikH5gctcxzE6chuKth8CiJrPB
wbqid3ycmQcB6A0wVxCKkW+ASId8SeASIIwdQeQ8jpcoYrliZ+G9E3u7NTm44g3IPeIGQOhHYdCY
eyDoFMA06sIwrAZoDD4Iv9B0hiKHRR2d9P5qj1/1f7DHdBx2W6L4QYHhZ1RgUrCvFyASkEQJ8t1q
jRt7uYnIzgrLKCVSyVuvazaw1MAOwmAGa0vgasaOxjByYe/cQ3+WgCjJo61RBllHYcKNhLl0YJMf
tVqCe9E1rpzREaXg/440CYAkcRfmvk9zZNMoWTbp4fmPZ31EDk3NbvDMpDiAejdx9VO0rGTTH1QY
rJMFaCKLcAk79UefdDgrPpr2XvbO+z/0fuyA+gAynjfbgORHh8QFme0mAxVQCkQa1BDYgwDPpjfU
8KWfoKm8x2gifg6nMIQZzwCFk7gZbQKbukKNdecZWcqbqtcGWrpA9xsgg3wLA4PCvbgBZQvESRJD
VcMYfRRSw2vUBmO0gCjrlZlatMxFuIni7qO6WPkBTqS717IsZqDHPRCy+a/Tl6jNtBuPd5+0ntTF
k73Go6eP9muH/YPBGMQT+/XoODuwxiJJ1nGnCYr4dWMOZ30zBUIQAaUArpEgQWyONm7kvI0X37ab
Y+USGYf+8iQE2TdE3b6Jam+zPHUjG3TUfzHqj4/k4X7aauUP9nqzXPK2qA1BNwp7DcReCyec9YWU
oveybx8RGj8tEomj3Y4AnMvRANiZOAnX6DnwljtTB2hdtvh1sUEqQqNfOptlwr6E2vi8N0IO/npk
45bIFUMFbhuZy95ASvfUsJiQeE609FHjwA2U2IemC0AeQIwbVE6jBIc77tuH/Re918fn9gmeUmTG
y9C5d8D8Ozhui8Z9oLCkiVgEu3OjtHRtlX3SjGGlzRaMFYSa38mqjfp/eT0Y9e2D3vHgOci2/cP7
SHy5NcLSzsi9+FMXYWPKiuwG5x9LfR19a81ZBFsE2sgGfTLAJGHJfJAsQT4Z9QanNmuJOEv75ITw
qNEqMQhnuUMWBiQ7HYEWezSTAPkHSXXVXFjc1dHw+JD2aSx3tYxGxa4Ij8jj5lyiu+8ZklDsknt8
MRz1D3rjc3t3/wjBA+hajRZu6nnffjE47guNTaLxubEIV2ihagqjIeUtG514184NHx4dGEm6MqQl
a7fwklkDaNEmuiJ5IfIIk4AtkUIyOukfakNvHzY1xti4I+noDwQcP//Sh2ERU91N5DANNI/2rJTY
Vbxcezk4P3r93D4fvuqfvl840FvnJQQCQrntOmKJVq2EqbsSyjJDEg6vRh71z4baehvvp2EgjsMu
DkajAbIMe9w/GMOLjyWhosPkrzYrWgnEig7srzphIEYCzpJtRyQ2/PidcK5CH9jezLnyE+aK6HZe
kE1/A+wT9gjJWHG8J7s84AMRO5ceqFczZy3WgG0wbIeEIugdpUCQMWcwZ+BR8m+pxS/9FfKRa9TH
3HD+nRSZpHFPSedTkPyB3zAMh73B8Y8MAC1We3+fgCjBgHSE/gBa0hG7+xKY6TKcvc0Er97r8yGL
i1btdHjYlzRK9p9Jdg/IbLKDY3TYtgPsmoZgqU4zSi8cdO8C7WKDpXYUzaPdFNntH3qD82zj1NFx
8q5wltOnnoY4ktHjUA+JHiabmOReX3IPxHdN1PkJgQRWQTYtOg97DC1wkj0xj3DPoUsneBv/bscR
jEpmIR8Ad9apH5/M9rQ2juvCsSfrtBev93ZtgsTGZw0/CGs/DU/7uCAXZMK5NXzXAESCUya7hW+t
d236wVl58M14MegfH+708CQCEXbshYNtGo/u6noXu8UudktdPC928TTfxV6xi71SFwfFLh5DFxNt
L0gRJZtZDLx15lWGZqBsxjarT9qPGg5CWrl9OBhVEFJFR2lUVtHplfMjlHmA1cArrQYL3CRaMFpJ
dRtoHWwgip7I/rppNAfMCBAt0iZ7zI7Q/w+MH7nlOx6+pIXjdTONJiiwTRCBm3LdDEs1aazeun5k
rmHHgyTuon29DrzPjxM7fEtfrZr0BjdAiPNnB6TRmoRVS5AWl131eHD6YsjYBuQRhIiu8bXpxDMk
3VYsLr42qTlimxVPxNcmqJCxM4cvBr/FbhIQty90IzB1/QKo0BE/NtXcmmjhwdACaGRY9dI7Y9hk
Z6XeYpvzpEazgXVRrYAt4h5DC+7N+HxNpi+Ph897x4Lko8/WYnrspzFFTKSIOGWcDVhH5M08aOWm
sVhWLfMDdMUtYQpS6A6QzVUIR2ETIYFsAWFkB0aHXQvwPfFWa/losVn5LrAh+RXtFmT+wO+SCiPN
/md0uvcP7fSO1jEXQkeLCCIg2SVq6ldYK9UtmibyPeMvd0R/8S9WdeS+YGwXEtUbL8GhiCijDODV
iJzTn9Q5fXWu5swNoX8UwZFVIY9SRFybI8oyMXy/mCAPm+OW3sQKFhyqtwFujhopKHnOvCHGvRd9
IXU5GPGkd/q6d9yR6garwiTfNyNvGoaJkoTdEPDodHieMQ5U7NiYAKNsgsRfAi9ZeThzDmmK0Rxi
ojUSEM9wAA4b4QAlB0iswQvMaqo0FVsYqKjkdZTwWNXttkmD9JKGNLuA3AmwTp2lg4GXrI1wdw66
1OLNir1rJsVSXqLWD9o9Loxw5iFbMWfQFDcF7Zgg74TCjSj2DjVYmEKjlsJ7j06RwSg14lSl6HZR
K8X1J3+CDKThoJowQBeYsmxxcAgGWcBkauhjtiPAlVBr8lmSZpr3wfDk5PXp4OBzdmmhptg7PETJ
pPWu1Sb6UIiQleI1e10JBYBsuCCh3CxDxwXqEHVQgJMeV8MwKPhWRtVKmUIJ7YvNtEGdkm+bZHmN
FTQwCq/gv6ewIQo3TXEP5UZAvrxrmmIm0DXNYaKDkwnFVd5qYN4Z1nsDASTPuQgj15xx7MwMFQut
m4k2roKpcR2BOm3LVib2YlU1Q6c1xRicgFrYOP9r1gilska89Ly12Wq0rUofP05tIsb90/NO5cQ+
xoMv+8Ktot9S1z3u8nTjgwLF6vRs5ZpIx0FF6aDlr86sQbrZ2awYpXtP/9IWKGWTZU9kNRQeBRig
IDo4Oez8dCv7vuvcDs/6p387OB6O+3ccPgJ4gj40DVkowD2MKIZL045Bff2V6KIOBW+xuDSK4zzE
gR4ikjFnI1v+Qxr44Z2hrwAaTGkB8vOUPVPHvePjzvh8eGZ8trSQYhyIM5//KI57P/ZHFJzB3Bit
HE02XcxD8itE4Wa+ULuvgtDIqfbb3/+PKFt8OqmlRpAVZ4d6Y2sNWprQyKEbsrOuSsaqDlteyLVA
VmUkIsquwiYQGc+V76hkAarsiKJDpH1m7XA4lvShJGHiLLP+2MnMAzeFt/IizMK4Yfs7WnV7xz/0
fhxjXkF4jUGN7K4kI8868kMMzrM+U4aTRdrYsY8SEsiVLJbKA4onzVuHswXb6kFKw1i6kILsarTc
aGUFHjSLyZhWfpsjAdm3kIQgM9XYP2WTwVjaQ1XQdCMIr02rgV9VZJEdhculfGUWbjB0MDbzAUVa
h8xDcJitvUqWxo3+1NXf1jmPPrXGbOk5SkvGjz6FrpyWhBamizG6iG6KUktok5YKHdNWvLEO16pd
nThpBmELmSsie57B5mG7kC9PaMI5qFEATbtuWeIbQb67Bv4fElEYAaPDgOuAvqGM7tW7aHrv1kDm
PZf1jzpxENtJLCnOK3sl+qhDlLFRnkcrZf8NMEgbrRLS8N8Vmoki8+7YyA9n8RXb+d+gywzje8Nr
MvAyuQAiB2yrmZEoh3ygShaa29TApP8nKaiuptBlDYxNcvQIjfAgibteAmuW/ZLJTT2MJHVTKBLd
FaWAFWaAhBe9OkgkkRBLywlbdXHviMxoXqxUrMpJOoF3bZOHsUsv5VetQRanWMM/CtRAJDLzLVGx
g1lBb0s/8LowH5Q6LhfZQPQ2RhrEVywmgfyxsHKPUd6T4OTfo3flW3CiLow00AF1Glp2Q+qZpILS
aqtfCCVtOipGvdSt/skUW6mY2zOyuiotC7/wvhmTPOQ6cIXD78chyz50BGJA5q4hSZNh1Rm1UnRR
mLIdTuCrAUqW9503C1dSnSHtJLOIY9yzCpmiz0pkuiAAqTQv8AOyL8CP6doojJ58sAyqierziTxi
aASklSxGkhKJI3MQLKZN0QB5Goci+q8oopOZPxsJV+ICrewT1HeVBJtbACnHQTNlg5/o8h1ruAhA
XprJtiwThEsnHdfHILERH2e6EZxU9OoHcBbFOkR3GU4gn6rYSDvDXsTfVNja31I6xJLuiOCMKQib
yPci8/Eg04wJwzjqFh6Tl4qdPY5ybEWbpZfSh2rGVyYdCsM4EkBtB5HRLLKOvPpIz4y2gQpcm4jV
W1BwhOuD+P7WA5F+hpmnGJ2X6Mhjnt+sPYojr4s36Aunv60tmsyYZjIRgwBWEaAiAQ3+VRL/n6I7
zt6RK+PPAxAB3ZIyqMWoo6+cw08k51IMkndcxzHpc+mqLSf7zXu44rNuQebMoyXOTkaa6/Mjn1um
xkjREz2Tt/ne7tAN7swWMoEm9UYqBDCs4nCSjRnPj4cHr/qHsq+D3plRIlBW1QnKVg4/JYEDtF4Z
VpItQ8CZqoJIUzb/aolFW0DrvYufF2LoJ9xPlD3E92Wtobz2St3etvDo8kXZ8rbU1x2PW1p9VgVg
DQBpS+tfuYbVUl1hiddLJwhQQEIk2yLV8Qjclx6mkO8zM+tsUf0lsbO4eUGA0wVCU0pr6f5q6IV6
t1HQuw3Su7fhGedcpTkLnBFB2VDk7qX86eWS3o0xOgPlIJVr1WFi47PmJrvjUBvULZlMsGZRlzuE
bYOljBIKCHsiaVWoSDdAwm4WcVWukNxUSo/hdAU0JQCs/G6qTxD38iluFcOUy911tqGEjg7VG6xa
IB36ZeNtvJxSUdzyzNZhbZuwlvVSgYB65wUEKQycIkT/pD962T89ALqFJpS6clswv1Psvyq8w8zE
5h+UbG5SMBXbJZYhyMCJP3ubLSCq5pqFITUwkC5fskvk3vrQKI1rFGVkhAbubDH4g2I19I6XzrxU
pkDpNSzoZzEaMhyjHOqBWo9i4dWcS2JZnWjjFkxrgPC1gnXVMA4tv9BbUfzSMS9wSX+Q3joi1PhW
1uCBSN1ZSKdbYuU5AYWBFqJbWK0hXQ5NImnGJCkelL/IotOZz2QgxwjQ4Ix03r3QvGcT8QzGy+h/
+Wlhfz6GDQMnYNjldt+mYzxMx3g46TRal3espr0AnrpD/i2meHkmUJAwca+UE03JfNvZNnaNPcup
4KyQoGInxbdTTJBHt4AOuQNdRoh7uHOBM9/HhEH8keNs5729gb7SnRTcW/lH83GL1paYMUYuLz3g
KGjxr1pd//K+BWZRvax4wlupT6xTqTmljxsyFs/M0lfqlOTfcDerdWzeGg6oU2Q7MDri0kBdxaap
2ZL/3aufvv9jgMwRU+eGWiqkbWpljDursP/mtUO2fEAgK0WCItkuY0DFea8gD/mzCUiAZo2odACR
HuQXtggACTWpQKOHgwIBoeMH+pEyIxGlMJlM7LCooKhIBpC3JHJRpATknlAkhF31BB+u0qeCmJLu
wuB0DmjAZ2VqXj4SJUVHPxeaMwNBzfsyyCWexsGi3sfxcuRNvy0NfRdvJzGvT9N+yqSlDhiNo+P5
vA+e8qm853x9+NmqPDrbD1xuKrX7D9MlWQZAYXXZlyAx7f5Vl4y6coHLA95Z1kfg1+9h5ynuIQ0u
Szwfy/1IpbxVPUpO94yV0aJWRAxwODr4RzG/fxHq3EerdaRZOe/SKOnqBUFLB9V5MtUMrS24QSIw
VZuxWWSn4FxzFc8Lbnf69wwbqgBe5XwvhFxhxNWSagEob91iM03xZHw+7vx0i8TlpHOrYpjuOm86
t62/te86551bNAHedY46t4vNij21/XcO8pkOv9yGN/cfNXbhnRa0333a2IfWj/ca7RyoOZPSmsJk
ugKm1YjXSx9ITsfIsRFqcdGaUA4FjGNU2fK2mKj43fbkot2ZsJ3qpzYJC+2MsSETkwJv6127Jb5J
+6AXuD1GoeXPk3qLC5dkrPFe6FzyeKRfqbgFvh05wdwzd+sCREoGGn0m7brYLYgnb72bOgpi0A/P
zZ/U1V/ftCfFs4KZj2gkOQHshNEvsti0SZpfA73lzxgxK/XmG3hT44zkVaHxOZRo63vn/B5ZjSfS
9fYh4x3xe2n82hY4iwJFSfNJtxb35CLdrkljsybnnGttjyKZAMmF06KFDohb99NjLfho0o/i4S0g
+t3D1Mo9g1nEFDcx4mDIyOTQq8ssCIaYTDjFXAaJDWQbt6kGhm3G3vIyT+zVm3nUiTdrDL9tpC/C
YFPQY7uFmhbYXyFeZfRXYGnnZaCYM6UghYEdvbNdFAbLQOlxPfeeERnfgtHhCAmGw6XxNUE4W3gz
FQ6dkv14Th61xs+hH5gXs0VkTjmEZ6qF8Ezui60Z9Q/ewKbg5mjUp5L66hVIuPhKzkOcBf3U7p25
zmcVFMf+NHKim4qaKMBUKJnLD1SFJ0Q9clG9P7YpV3anIjoJHaU6Bm6NYLqMvF/M/b29RstKhV34
Kk6OfhXmbIFEDOXyp4+fNlq0/v1NBNLC1v7itYxAtS+B78KZebK16fTafH7deP6D3d599Orop63t
ZiH1hznc5gH9PULf1sFo33609aW1IytdAe0FkoJ50d321taA47NoZuZxMEUptZcDrvrix1zTUS3T
+MUT8fwHmMQnk5NBuexK5ZZ+1nGaP/R750f9EcbEjYejtKJooAp87X+uYTRIeogkSudsgfrIX4uk
B5ano1nmTkOZsr/GclkYwcD2N8xJUkHhBoX/Yiwy5mcHqA0iAcJoO9HTysxRzqrWN1aBwKRiFynB
NK3jA98Bs/1LiuvxIs45xjxTFYuOOjgV5SDlHh5iHQit3yFoHTsnXuKFAoUKrNKAxSTCIB+t3cgC
Fz44OP69BHQBGlexMgBtArwQ3ZiqeEBdyBoDeYtYySC2qA622QI0+/+Tumhbedj5wQIf3H0oLdE0
O8KACdfpzUeCfqY0g9IbqMDM8bB3CMTDxD2M0+LFlclzny8dkcnUiGluOMtOB+j76MSkVI8WpXeg
5IMGxlNQiA2O1+IfO2L4SvxNkPUZ/sXn8M/r0ywPvpZmb9M4nDWiMlI+5POgkPatChKn/ZpACiiU
ACT61OGFkdyTT7CmPhAXmuPTm8XWhMQv9jOiZo9T7J0DBTwfnPRrmeONwhJlONtHDVgRv0gB86qC
ZOYHAjXoGmvBFnLWP36u+eR2JkU3XpKRan0I18e6sKqQH2bm2fMN1gbVsoPC6xRb1lE4Tb8E3rtk
dz/9SjU82EqF1hLS6gwQmjCsyGC9TKLdXa3slvjI1c2tLMUTe8E8WaRVMWebCDMtxexmtpSuWg4Z
s9GqZuUCvnMBXoPxOWXUXJK2aXz9487Xq52v08qEMjafAkDzoaP5bStzGYXSSkUlexV2F5tZKQXm
OwksrGlZ1SKkblyJK8Yp4482Ur56Qm609wxWhZbSBe7AVuaWJAePNjnOpqDxNGudWhjNYFoAs/q9
AkQXO/utVmdiWZ/ALF9ydYqJjGDAGVUFrMnwCTUs+URSOx1Fa2VhBrlT7ieo7GKVhOtYZpLJ484l
WjUjngp7xZ/ZhZyGOZiXWHQIThPgOJIt0hYrvCLsMVGeFGzJ0hi24dKOsBaBF81Re0mjxD7GBXSv
a7ZYlpMi5UzNAcPJcPkA4mI/33eLXtxasceKKo+wTzb1QoUoC3Yj3EH6vYA4WsxBPx9k0EFXOTup
Je3GCCC5LRiJlzlh5HY2xBAxAF6JvZQMswUlrTGCCJE6xKcIp8yAyacYovs/3RxFZLLJVYcFfCfc
hLYRrfda6+9Efl2waJjcSIyegpdgwfe0rfuIABbZz/bDkfcJ6Cz14leOLsr/qjzPdeQVGGzuJkoz
2hIPk27gAReYphPo6NVVY7Sqh1IxSfmjqnKiuAbH3v0cTksLX+CYMpSG2FYB+NSxg8CnAYbY9Fl+
iYvFaajisGumcpTOr1QwdIdDM2HKGE7D0Ke6Ag3SFI+JHbet9/ueVWA1CH+yPo9xZ1XBp6QEinuq
cCNm9Hr0+jR1JKo+0Y94i8CRd7/N3n3NrqEzEMmqKbdGAwBpkHL6ZLt9mGXpxBTdb3Kl/F/JZasI
n8pUq6prRipCFkgfJpkFH3HCQ/wv7FO2gXiQTO+C92tSF/AnbdIEjiF0dfGWLNdhQvj8NsVmaKZ2
LheXfJHuvJtt968V+7ySymLF7hHUJrz/KzRYEYcgOmMiGMp1KPm2KsG7lRrSJSnbKiEpooWUDLMX
PKCOVWWPmBnJqmtxh4PBZeVHetn6oLMmrR3FZ5UloGVbHYpthtmU8VeCTlkXXiLDjPVqTFR1W1kn
zvzvNOqh6LdR7U689rEeHOAdZbmvUC7RM9xdmQaTCuvGvQdSlV7mIEiYlsDqjXQWpuiArFweLgGO
peIe79Mvm2hJxVFVxTxn7cuKeVQmD3cpbt5qBajummpDm9vwg6e/cNnY0dskizDyf3VS92sSvkU/
s77ed5iP0ZuhxIZIDfRwKcusNq8CV0L0DXV+V5Yz44WjzKJpXJP+PM12yd/q0YCvlP9S+HnE/+LP
dXnTTdyF6Vh8+0a4Sbq7LUqKicpcjmFJ5W4zsi4M+M2YaNrANkE7J9njZxq6N7SI0u+La3PmdzSM
1K5UwvWv8Dgbcr/gXd73xvTxvixSqovU99JdrHWOxZG7u1mBU6sBCjz9dZeL4F4UPKU4CbkG6PpZ
OBpm/gK/3Lf6iGJdDUrsK4MgtztA9DzANLdrnL0+147Olj2H79l27rUsUojM95+4Kr9ASkoKlKJ4
kj9RM+GCvUiRquvDkaJClIooDJku00QbkttWaF1F97OziiWR54OfO/fYdIea0tm/ajcxs3YGwuOf
DWBe+jU5uIxyF7jXOqWfdI26nOp7jtt9J0nyRP0IKRmQtCA7M1VoHGuUq2xI9BlEeXlLmrpBTc2H
kmRyhRQBlfz5AsviTh26N+2GdK2fMQOPo6sA1a5u6JXq7DvUofTVvjXQl5dsXCoRk5YzNTD7Of/z
6ct75DWyrSxvKAwQgPfXsvRePffN1gC/L/LQkMaRUndIfzFsWtVzwcJnarWy3+7pWFV2xZ57se80
X4XLt1g17C5fEQHFZgwD4FlNOP/PmHyHHotZ8VkeRGoUTu9tlFuIiRYwcG3HxWzeLfae86+POq2W
HqrivpOqVtxAMvjOpO4sGX5lU+ICz42c5yvnndniiA/61RLNpsjCxIVmLUtjINhati1+BCCoU7c7
7TqBsyP2vwXyt9MuK2MmrseFP0H05cyk/UarzKfyQHCffqnVFKji2+xkFmyEyp4Fi9A1Xc4mVAhW
B2Ha4p/yu0hVdxhG9x1DCcLx+zUW3Ngu/p/+3vtfY0tlN96szNzKoEBeucp+gH8Avdvdl1E78JYF
n/ePpa1pV/u7LqQJtKup65/CEUaw/i9x+SeKFlaZqrRdwpi3q8xciTeQFLYwzRPPmWTmF6nVFu06
e+I/xN7jVtm8Q4avfAgvlv7iXAmmtJguSbZuvI/OQXDYU4ZEGsO0QT/zZ5oYo6tF5vyCTNATxOKK
armwffMLNkwjmI9b8Etuk/B9tlRnXRRr2nInugV7Ir4XhYK6ipty3EgSm/EWOzL6mrI04bhB5dZn
QF1+Qgr7DawhkpYCQ1OOkJwCJm+ozBef5lpSGFrkX94IP2lQYWiHC0tr9aH3n4qFMGFqmwDlGoo2
YUsH1uEP8ZJHElYTuiIJhqqsIf2dVnl/Z4apMUFaTdosVWm2BIntWIeL/cvVfPI+ySBXzvsbUBX+
bHdv0eyhHxzUFe6Xw91wVpDBMy6tVlNFi4WzLvyvLqRzrHxEC7nUiuTJbOou9sBkDteJqVtK+eiC
CYtv8eumDUnVp5LA95EUVXytSxbPe4fhpjbmHhnWJ1GWQ7ksE16HiigUXlO1fBfkWZzoOjf80Ckt
84VyMqLoz27GKr0UC8kt0KSS489KpGhskhlSpez0wVgXhrrRAchcYkxAHSFrkC3ttVTEFIlWo6Wg
5FGeiXLN9Vw8WwXsHKAK+G7cE8yvrSGeRj8WtzQiZ7Us8HgK85m4LQ8PT9lmRadQK6CPOXRn/cP6
FosCyAmGOo+3pfPYmZO1LauULo/n9pSAFDKea5q8l7PmFef0T4rjzoVslyD7Pbk1elh3vjJ/eW56
KH81TjNAtTTctaK+PKdK0QmRMpKzxHhAZK8yLt5639HRXe9GRZZ19VvDV7L4FbpbpVG5YOn7iRzg
SZjV1kFKkXeW43myJiANcR3uyNthVOKL8IR5BGIosQqJKHp9broemQ85FegmdQyLPoOsACwSNSu6
dQ9//8/x8BT0yJvMLBsn9RLlUROsV1IjVbdHN6Fnuf4UiVw8ySufnJq3srhER5QvLciXpLjDzsqN
MDeRFABpRiZ8kGPCTnAdAZiM/FdjBlTAkQAuQ0VWf4YMVvlXfgst2co2bGXQpb1ymc66uJhwARE5
llwvHs9QGQxGZr8J8b4YdPDiqL4mIac9x8iyAyrJAp1PyuBSNj2Gr2cvc68YIobf8PFdBQorWzj3
CdydHc259JiV6kBZtEuRyCsh3VcyfF/tanELO4V8FQyC0zwc0sHDytnKAgH4seIkOb/O70666ahc
GUy1WkmaLZNJ2BVczjbJp0o6a3mVrbNal0oRyHlsTYZWDVDtwT/rxdppO5WlL5STyyqtyO+px8AB
+UV3EXLSTcCirVdRoiG3GupGA7ps8K2/Lq9IaasB05QDjidG4UG5pH9oonL+nShh0hIXKelfKD4p
dxFKzDZ5ky1I+atXuOAOl8oNA7q3FpUz0oysYt3MrJptZUjjPTQvjT2pojaCSzgXTCJ0ocv3BXC3
uVsqGI88eSYSvboit9ZHdKATTLNA5HOCr5zOn0rEU+7blvghagw7pOQ0zbQKpxADpsyfwylvYOsJ
aGwC1yWrdnPt+Bgyi6wpeJiIKQ4l913NaqsSXjAdY1Z0ZrpMEYA9mGF2ow5XAXMocAAArM4NkNE5
JWZf04OQlNNS1ZzTF4biWBMMT03UnRiApku8O44w2FG3sdRKzlsNWfOu0mIOuBKTVUrbbbrBD/mX
hxOgAtAP3X+sCyZbJy1rYYCmj4cYh0/rUujrfC7XU7EwkCtByUQU5fySS+On21/vgADpFMVgxgFU
QXCRmDjV3okicleRqRfLxd8wRYBrSvDCo6OHEyypHATAKghqWd1IL1LCMydJwo9VgsiHUITKqiDl
E5cFUyrJtFTCUT24F41TYol2pXRsDIJo5fhCRTIoX4JQmadfjg9UoSL49weXF8B0Xtq6jJ8oHpGS
FB33gLg8nBBVeYjn46GVo038WGYFS5Xr4Z31R+g+uXRVqrfzD9N+lFskXQ+kOqX1MtKcVWyHVdVS
Y52G9a/Q2qCCafDqNIqlwkvdZ2/FZp0Ss3QsRDE62tCeMScT+ktMNj3p2oH7bMPZqYQ7bsVhWnqN
r5ydRr479z7j/BeKzsJKdUQwzMi51h18ieKzqu4bVZ2TVfJo0VSGId6SqC5rJUQjO2s9y23kZJIf
/EjFCNH1Dmhp+N4HuhNJVTTBsMpnVIfSn9NTvCRUv/w0hCP/zLj7jmlr9irelOrLWxkVuOslsDsj
iQ1hrmLBhJbq4nJUpEP3kIYrzNVBBatI8eV1o9viWXABJsUrVJV0k4WyMCjyalSsU+zLusauVa6f
l4ZU5MOZg6t8aDHuU0bwcRHqeCssNIKmF7CuGCGFf+Eq6oEQWA4WWi1WzqwReNemhLrOneh+fjgJ
aDCOFw6sOoio3jvXn3upoKstE3WGVVKcyLNlIx4JK55FeEGEZW1X0tRCqkt/R/3/7B+c9w8xesLN
7rqtrviWC0Fx84tEU8pB60zjQi1dVjcxuzrGWNmmaLda5Kl6JvK3yX4K/HwnIlDoyw0lWGU3zJp8
KeyfrQ+YFeEmX/CEgDKqTgq7gE0wfE67m/ZTIGawPJd7/ADg9AEv6J9JMXSRy8WhX09vjCv8qFVw
k+oNUKxBkwNdlltMrFZOoE8wvG+b+spZImHCI3rr3W07mkQ0Sa4IAxItAMtNvvyD7v+M0INUp6Jk
oNlHM4n3KAPOCrV6dFlKAUX9cb7fbXrxt56WykM14s0UQyynnpleJV1VCXLL1GkU6XEAuLq30Sz1
Zaq5yfinirlh3na+YAbfR6UqrBZqsZVvgZGsAEVv7FUkaJxUAcTFJEQsnSvk7UaY8Ij2TzZu8r0I
18iss5sx0vuP8vcYyGjHVISvDiYpMEOsoyH5W+60ueXbMypy77Eh68xp8XqqD2hYW6wzan9SGaQj
0tp+5JcoGpqK1RDfA41Wipnsi3kw9NtwgMpkbSdbFYASuHoft+mXO8N6X5URHDMzo+rRyjIxylVu
NzI/5OoTq0xQbro1G/RewLPqpHd848n911vk5/MgvaxAobyzvMa7Nq7REIsV+cjsxBchmGiE94Md
lMObbOjbmTnrQodUZBYOLfDh6YZrb3IqQ5xgvNkUI5OzGye3mxpVqKoseMz5EmG5GihXKhegZCwS
js2Kw0InhcsohBvRXbE+qyBA42cLqj7IRR2dODOyNu6rU0TuLvK3cm1UvXDlx+b087aeyII95RtZ
qMwE0rdCNlmqJhZTvbHDznszmmdUIufSWPs7dNvezi0FmQ0O7zIfon4vFP/NdPyA/pFEFivez/Qa
gJn+mjEb1UH2S3VzVbeoW6ToFc0x1YO6sl1EB+DCifmoLh63qkBRTZ34JpiZKZOqkz5gnw1H8Cfa
7BxMOu9W94GKqk1Kp2m9nx9Kq5TGEH/7+//9ZPy4p+ZD+fYuPXQ/ZUnFqH26fyy+iUFYkeQCoGWV
EmhJVs2bAxnYqoXZ+iSJFlSOCmvGNrzLirvkS3BlqQr8de3bfAtk3orBYQ0d9VXibKFNdlVAhy92
qi7OXwirMGTqvexdfiu0oUiNTjneo9AsY0QdnUsVWqWX5JUMNVvKk9FdsZIBsbtqS0UyeZGsbEjf
tjXVLivMyiXphfEnetGo6k6ooARhx9qLdoiYUhEj/bLHG6laIu3XifmWDpnEX4PmLQUl4JFnfqEC
dIGA+/GWzlKqjixClbpgFzXWnyDsJxd1xMUXMDZqS1fpGj2Mhcksc0eWrAMmqdI5La6KAbwL+7v2
tXjwgotBlxrlBczeDhIpyXpzNyOlGZ9benPmaFCDdfeu03xE/QZFmi5ZPOWKNKpxQt1Yiee1nEK5
BZFktYp83a0CInE1sC0dhAFeJEJdFPTdrf1p+aCW+F7sVQR63pV+yXt4c48LoD0QL5aOvG+MUwAz
ukimg8TnEOLatiMFn4sPOFYFmCZFgpbVO4E+JWWqXk29PkhGxfR6aoX22l4jsBX7/R7g1v4stNO9
A9HeLK34J2/nx+ydRs7vah9tTFcq06cy6DOZcvFvUUjlpDc4FcfD4dlnbGO248UmccPrgK6K3azq
4jLCC7NTKYplJ5c02qb0dwpzPHh53h+dUODlQRItdw5U6XgmwWwvYNvq0nNiT8u8XwNeHw9/UFfV
gX5D7mOOc1GMBC+TTVPzFZukzB9KOFw6FJ8q76wj2o8DrKNwDuCjpjlHu7YS4Aq2lucock3YfLkU
tzzzO5He08wBJCHWFKBwYFbiaToSWqPi1phqnf9jzpiCK99RhUAsKxRiDYPO9iQ/fNzgqj8mXXJI
L2Dhpjo/g12wym+guzTIldD7wBw9urNLjAlh+rBO6PhkuxVMSwnmvOgN/seU3yQ61TN0tO5tPDg9
L7XNbDX/2nNlFMERAuY26u0cDEd9FO5ol9mtB5iUqUuajgUiPygAYnAIm54qrP/tpim9PGl9yOw7
c9X52g/1X1m9l7PIqsekAcllH37eMK7ioyovqaYQJ7mmyDnpXhqtcGTZ+CoNe6rXsfYy38vN0UjY
Mci8VdkZMkU5i1yieiKYXuBWxSgbZXMrZSVUzcdqiBHGr1yGeIdmVe6D1llaq0HuCQkYqrXNMexd
0cqeKbWZQmRE/pmUpWy6nzv3JHVWi+I7KhMzfVLLnAvob+joqWeFaiRa7Tuq0aZKwzEj2WvF5fre
ZTif5QpKsGauFw/Mmxsrpol1QDRIKOFEz5VM567A4tiJqCJYSeaxyBtNQAGztsygsENaKP6o/2LU
Hx8VPVvlDJnKx3pGaHniJbwoTP2NVjNLK9ZiUkxOvkqXoEvUYuu7NBWXY3ZAL6M4ajJdavypquTM
lrXJYSjubvEihS01G8rzzfVUmi0cMv1AZ8kGvM+Pt6JfihCYaVVIuCkHhmzfhjJITJCyKzmRAOn3
+nAy9b2X+my5UiiP4bBdQLcyUiQxO75/R3AzqrdCs8Ft3YXibLVbwdsAno/1ntGOY9sU9mjbKDrY
tlFRTouFCl3GeuXd0PgDvNgv2qyTSnovhawxCFlrttOjoyvvuEjLP6PIl1Wbzc+byw0nHoyI8rNV
++rL5w/8vOmPkFb+U8dowefx/j79C5/iv63Wk3b2N/3+pNXe+0q0/ogF2KC4A0P+m+4/plVikauI
7rJPxG5r9/FOu72z2zpvP+7sfdtpt39iIQmtgD40+Hb6yH2y9+WYfvl8+Xz5fPl8+Xz5fPl8+Xz5
fPl8+Xz5fPl8+Xz5fPl8+Xz5fPl8+fz3/vw/gH0zaQDIAAA=
