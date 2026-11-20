#!/usr/bin/env python3
"""Build the one-file Pi installer: hardware/raspberry_pi/install_agrisat_pi.sh
(installer script + gzip'd tar of brain.py, base64-appended after the __PAYLOAD__ marker).
Run after changing brain.py:   python3 scripts/build_pi_installer.py"""
import base64, io, subprocess, tarfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PI = ROOT / "hardware" / "raspberry_pi"
template = (PI / "installer" / "install.sh.template").read_text()
assert template.rstrip().endswith("__PAYLOAD__"), "template must end with the __PAYLOAD__ marker"

try:
    rev = subprocess.check_output(["/usr/bin/git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], text=True).strip()
except Exception:
    rev = "unknown"
version = f"installer built {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} from commit {rev}\n"

buf = io.BytesIO()
with tarfile.open(fileobj=buf, mode="w:gz") as tar:
    for name, data in (("brain.py", (PI / "brain.py").read_bytes()), ("VERSION", version.encode())):
        info = tarfile.TarInfo(name); info.size = len(data); info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
payload = base64.encodebytes(buf.getvalue()).decode()

out = PI / "install_agrisat_pi.sh"
out.write_text(template.rstrip("\n") + "\n" + payload)
out.chmod(0o755)
print(f"wrote {out.relative_to(ROOT)}  ({out.stat().st_size // 1024} KB, brain.py {len(buf.getvalue())//1024+1} KB compressed)")
