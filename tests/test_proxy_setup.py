"""Test chat-proxy/setup.sh with a stub 'npx' (no Cloudflare, no key, no network): it must run the four steps in order,
never write a key anywhere, read the Worker's address from the deploy output and put it into farmer/chat-config.js.
Run: python3 tests/test_proxy_setup.py"""
import os, pathlib, shutil, stat, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
n = fail = 0
def check(name, ok):
    global n, fail
    n += 1; print(("ok   " if ok else "FAIL ") + name)
    if not ok: fail += 1

def run(deploy_output, logged_in=True):
    tmp = pathlib.Path(tempfile.mkdtemp())
    try:
        (tmp / "chat-proxy").mkdir(); (tmp / "farmer").mkdir(); (tmp / "chat-proxy/node_modules/wrangler").mkdir(parents=True)
        shutil.copy(ROOT / "chat-proxy/setup.sh", tmp / "chat-proxy/setup.sh")
        shutil.copy(ROOT / "farmer/chat-config.js", tmp / "farmer/chat-config.js")
        log = tmp / "calls.log"
        stub = tmp / "fakenpx"
        stub.write_text(f"""#!/usr/bin/env bash
echo "$@" >> "{log}"
case "$2" in
  whoami) {'echo "You are logged in as someone"' if logged_in else 'echo "You are not authenticated"'} ;;
  login) echo "logged in" ;;
  secret) cat > /dev/null; echo "secret stored" ;;
  deploy) printf '%s\\n' {deploy_output!r} ;;
esac
""")
        stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ, NPX=str(stub))
        r = subprocess.run(["bash", str(tmp / "chat-proxy/setup.sh")], input="FAKE-KEY-123\n", capture_output=True, text=True, env=env, timeout=60)
        cfg = (tmp / "farmer/chat-config.js").read_text()
        calls = log.read_text().splitlines() if log.exists() else []
        every_file = "".join(p.read_text(errors="ignore") for p in tmp.rglob("*") if p.is_file() and p.name != "fakenpx")
        return r, cfg, calls, every_file
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

r, cfg, calls, files = run("Uploaded agrisat-chat (1.2 sec)\nPublished agrisat-chat\n  https://agrisat-chat.test-user.workers.dev\nCurrent Version ID: abc")
check("script succeeds and prints the Worker address", r.returncode == 0 and "https://agrisat-chat.test-user.workers.dev" in r.stdout)
check("steps run in order: whoami, secret put GEMINI_API_KEY, deploy (no login needed when already logged in)", [c.split()[1] for c in calls] == ["whoami", "secret", "deploy"] and "put GEMINI_API_KEY" in calls[1])
check("the Worker address is written into farmer/chat-config.js", "window.AGRISAT_CHAT_URL = 'https://agrisat-chat.test-user.workers.dev';" in cfg and cfg.count("AGRISAT_CHAT_URL =") == 1)
check("the rest of chat-config.js is untouched", "AGRISAT_CHAT_NAME" in cfg and "Never put an API key in this file" in cfg)
check("the key typed at the prompt is never written to any file", "FAKE-KEY-123" not in files and "FAKE-KEY-123" not in cfg)
r, cfg, calls, _ = run("Published agrisat-chat\n  https://agrisat-chat.x.workers.dev", logged_in=False)
check("when not logged in, wrangler login runs first", [c.split()[1] for c in calls] == ["whoami", "login", "secret", "deploy"])
r, cfg, calls, _ = run("something went wrong, no address here")
check("no address in the deploy output -> clear message, non-zero exit, config left alone", r.returncode != 0 and "by hand" in r.stdout and "AGRISAT_CHAT_URL = ''" in cfg)
check("script text: key is only ever read by wrangler's prompt (no key variable, no echo of it)", "GEMINI_API_KEY=" not in (ROOT / "chat-proxy/setup.sh").read_text() and "read -s" not in (ROOT / "chat-proxy/setup.sh").read_text())
print(f"\n{n - fail}/{n} checks passed"); sys.exit(1 if fail else 0)
