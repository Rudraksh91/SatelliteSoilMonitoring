#!/usr/bin/env python3
"""Build the one-file Pi installer: hardware/raspberry_pi/install_agrisat_pi.sh
(installer script + gzip'd tar of brain.py, base64-appended after the __PAYLOAD__ marker).
Run after changing brain.py:   python3 scripts/build_pi_installer.py

--personal   also generate a command key, store it as the GitHub secret AGRISAT_CMD_KEY (via gh), and write an
             installer with that key baked in to hardware/raspberry_pi/dist/ (git-ignored, contains a secret!)
             plus a one-click dashboard link file  ~/.agrisat/dashboard_link.txt  (mode 600).
--auto       with --personal: the installer starts in AUTO and does not require calibrated values."""
import argparse, base64, io, os, secrets, subprocess, sys, tarfile, time
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--personal", action="store_true")
ap.add_argument("--auto", action="store_true")
args = ap.parse_args()
ROOT = Path(__file__).resolve().parent.parent
PI = ROOT / "hardware" / "raspberry_pi"
template = (PI / "installer" / "install.sh.template").read_text()
assert template.rstrip().endswith("__PAYLOAD__"), "template must end with the __PAYLOAD__ marker"

try:
    rev = subprocess.check_output(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
except Exception:
    rev = "unknown"
version = f"installer built {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from commit {rev}\n"

files = [("brain.py", (PI / "brain.py").read_bytes()), ("VERSION", version.encode())]
key = None
if args.personal:
    key = secrets.token_hex(32)
    preset = f"AGRISAT_CMD_KEY={key}\n" + ("AGRISAT_AUTO_START=1\nAGRISAT_REQUIRE_CALIBRATED=0\n" if args.auto else "")
    files.append(("preset.env", preset.encode()))
buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for name, data in files:
        info = tarfile.TarInfo(name); info.size = len(data); info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
payload = base64.encodebytes(buf.getvalue()).decode()

if args.personal:
    (PI / "dist").mkdir(exist_ok=True)
    out = PI / "dist" / "install_agrisat_pi_PERSONAL.sh"
else:
    out = PI / "install_agrisat_pi.sh"
out.write_text(template.rstrip("\n") + "\n" + payload)
out.chmod(0o700 if args.personal else 0o755)
print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB)")

if args.personal:
    # 1) the daily job verifies the Pi's signed confirmed-runs with this same key
    if os.environ.get("AGRISAT_NO_GH") == "1":          # test hook: do not touch GitHub
        r = subprocess.CompletedProcess([], 0, "", "")
    else:
      r = subprocess.run([os.path.expanduser("~/bin/gh") if os.path.exists(os.path.expanduser("~/bin/gh")) else "gh",
                          "secret", "set", "AGRISAT_CMD_KEY", "--repo", "Rudraksh91/SatelliteSoilMonitoring"],
                         input=key, text=True, capture_output=True)
    print("GitHub secret AGRISAT_CMD_KEY:", "set" if r.returncode == 0 else f"NOT set ({r.stderr.strip()[:120]})")
    # 2) one-click dashboard link (fragment is never sent to a server; the page stores it and clears the URL)
    d = Path.home() / ".agrisat"; d.mkdir(mode=0o700, exist_ok=True)
    link = d / ("dashboard_link.txt" if os.environ.get("AGRISAT_NO_GH") != "1" else "dashboard_link.test.txt")
    link.write_text(f"https://rudraksh91.github.io/SatelliteSoilMonitoring/#cmdkey={key}\n")
    link.chmod(0o600)
    print(f"dashboard link (contains the key): {link}")
