#!/usr/bin/env bash
# One-step setup for the AgriSat chat proxy (Google Gemini). Run it yourself, in a terminal:   bash chat-proxy/setup.sh
#   1. installs wrangler (Cloudflare's tool) in this folder
#   2. logs you in to Cloudflare (a browser window opens)
#   3. asks for YOUR Gemini API key and stores it as an encrypted secret on Cloudflare (it is typed at a prompt, never written to a file or the repository)
#   4. deploys the Worker and puts its URL into farmer/chat-config.js
# After that the camera check and the treatment search run in the background for every farmer: the app calls your Worker, the Worker calls
# Gemini (and Google Search) with the key, and the farmer only sees the answer. No Google page is opened and no key is ever on a phone.
set -euo pipefail
cd "$(dirname "$0")"
NPX="${NPX:-npx}"
CONFIG="${CONFIG:-../farmer/chat-config.js}"

command -v node >/dev/null || { echo "Node.js is missing. Install it from https://nodejs.org and run this again."; exit 1; }
command -v "$NPX" >/dev/null || { echo "'$NPX' is missing. Install Node.js from https://nodejs.org and run this again."; exit 1; }

echo "1/4  Installing wrangler (Cloudflare's tool) ..."
[ -d node_modules/wrangler ] || npm install --no-audit --no-fund wrangler

echo "2/4  Cloudflare login (a browser window opens; a free account is enough) ..."
if "$NPX" wrangler whoami 2>&1 | grep -qi "not authenticated"; then "$NPX" wrangler login; fi

echo "3/4  Paste your Gemini API key at the prompt and press Enter. It is stored encrypted on Cloudflare; it is not saved anywhere else."
"$NPX" wrangler secret put GEMINI_API_KEY

echo "4/4  Deploying the Worker ..."
OUT="$("$NPX" wrangler deploy 2>&1 | tee /dev/stderr)"
URL="$(printf '%s\n' "$OUT" | grep -Eo 'https://[A-Za-z0-9.-]+\.workers\.dev' | head -1 || true)"
if [ -z "$URL" ]; then
  echo "Could not read the Worker's address from the output above. Copy it into $CONFIG by hand (window.AGRISAT_CHAT_URL = '...')."
  exit 1
fi

python3 - "$CONFIG" "$URL" <<'PY'
import re, sys
path, url = sys.argv[1], sys.argv[2]
text = open(path).read()
new = re.sub(r"window\.AGRISAT_CHAT_URL\s*=\s*'[^']*';", "window.AGRISAT_CHAT_URL = '" + url + "';", text, count=1)
if "window.AGRISAT_CHAT_URL" not in text:
    sys.exit("Could not find AGRISAT_CHAT_URL in " + path)
open(path, "w").write(new)
PY

echo
echo "Done. The Worker is at: $URL"
echo "It is written into farmer/chat-config.js. Commit and push that file: the address is public by design, the key is not in it."
echo "If you also use the desktop app or test on your computer, add its address to ALLOWED_ORIGINS in chat-proxy/wrangler.toml (for example http://localhost:7439) and run: npx wrangler deploy"
