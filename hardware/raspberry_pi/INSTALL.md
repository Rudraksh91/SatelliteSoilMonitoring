# Installing the AgriSat Pi gateway (one file)

The whole gateway is packed into **`install_agrisat_pi.sh`** — copy that single file to the Raspberry Pi (USB stick,
`scp`, or download it) and run it once:

```bash
sudo bash install_agrisat_pi.sh
```

If the Pi is online you can also fetch and run it in two commands:

```bash
curl -fsSLO https://raw.githubusercontent.com/Rudraksh91/SatelliteSoilMonitoring/main/hardware/raspberry_pi/install_agrisat_pi.sh
sudo bash install_agrisat_pi.sh
```

It needs Raspberry Pi OS / Debian with internet access during the install only.

## What it sets up automatically
| Item | Details |
|---|---|
| Packages | python3, venv, pip, GPIO/SPI bindings, curl, openssl |
| Program | `/opt/agrisat/brain.py` in its own Python environment |
| Service | `agrisat.service` — starts at boot, restarts after a crash, runs as the unprivileged user `agrisat` |
| Self-update | `agrisat-update.timer` — nightly ~03:30: downloads the newest `brain.py`, keeps it only if it compiles **and** the service starts with it, otherwise rolls back |
| Command key | random 64-hex secret created once and shown at the end — type it into the dashboard's **CMD KEY** button (`sudo agrisat key` shows it again) |
| Settings | `/etc/agrisat/agrisat.env` (kept when you re-run the installer) |
| Helper | `agrisat status | logs | follow | restart | key | auto on|off | update | edit | uninstall` |

## Safety defaults
- Starts in **MANUAL** mode. Turn automatic irrigation on from the dashboard, or `sudo agrisat auto on`.
- Refuses to run valves while `config/farm_config.json` still has estimate values (`calibrated:false`).
- Stops model-based decisions when the daily decision file is older than 48 h.
- Stopping/restarting the service (including the nightly update) closes every valve and drops the relay pin LOW.

## Wiring the installer assumes
Valve relay on **GPIO23** (active-HIGH), LoRa module on SPI0 (pins in `brain.py` / `../WIRING_GUIDE.txt`),
optional DHT22 on GPIO4.

## For the developer
`brain.py` changes reach installed Pis by themselves (nightly self-update). Rebuild the installer file only when
the installer itself changes or you want a fresh first-install image:

```bash
python3 scripts/build_pi_installer.py
```
