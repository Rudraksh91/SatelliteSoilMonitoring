#!/usr/bin/env python3
"""
Daily WhatsApp advice in Gujarati — the last step of the daily job.

Reads data/decision.json (written by scripts/run_daily.py) and sends ONE short message per opted-in phone number through the
WhatsApp Business Cloud API, using an approved *template* (business-initiated messages must use a template).

Safe by default:
  * Does nothing but PRINT the message (a dry run) unless WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_TO are all set.
  * Never sends a stale decision (older than its own valid_until) or one that is not for today.
  * Sends at most once per decision date (data/whatsapp_state.json), so a manual re-run does not message people twice. FORCE=1 overrides.
  * Phone numbers come only from the WHATSAPP_TO secret, are never printed in full and never written to the repository.
  * A failure here only prints a warning: it must never fail the daily analysis.

It is advice from satellite and weather estimates. The message says so, because the daily decision is flagged "not calibrated" until
the campus farm has been calibrated.

Environment:
  WHATSAPP_TOKEN            permanent access token of the WhatsApp Business app           (secret)
  WHATSAPP_PHONE_NUMBER_ID  the sending number's ID from the Meta dashboard
  WHATSAPP_TO               comma-separated numbers WITH country code, digits only, e.g. 919876543210,919812345678   (secret)
  WHATSAPP_TEMPLATE         approved template name   (default: daily_irrigation_advice)
  WHATSAPP_LANG             template language code   (default: gu)
  WHATSAPP_DRY_RUN=1        print only, even when everything is configured
  FORCE=1                   send even if this decision date was already sent
"""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DECISION = ROOT / "data" / "decision.json"
STATE = ROOT / "data" / "whatsapp_state.json"
CONFIG = ROOT / "config" / "farm_config.json"
API = "https://graph.facebook.com/v21.0/{pid}/messages"
IST = timezone(timedelta(hours=5, minutes=30))

# Same Gujarati wording as the farmer app (farmer/i18n.js: hIrrigate / hOk / hWait), so a farmer sees one vocabulary.
GU = {
    "IRRIGATE": "આજે પિયત આપો",
    "NO_RUN": "પિયતની જરૂર નથી",
    "SKIP_RAIN": "વરસાદની રાહ જુઓ",
    "zone_run": "ઝોન {id}: લગભગ {min} મિનિટ પાણી આપો.",
    "ample": "માટીમાં પૂરતું પાણી છે.",
    "rain": "આગામી 48 કલાકમાં લગભગ {mm} મિમી વરસાદની આગાહી છે.",
    "note": "આ અંદાજ છે; ખેતર જોઈને નક્કી કરો.",
}


def log(msg):
    print(f"[whatsapp] {msg}", flush=True)


def warn(msg):
    print(f"::warning::WhatsApp: {msg}", flush=True)


def mask(number):
    d = "".join(c for c in str(number) if c.isdigit())
    return ("+" + d[:2] + "•" * max(0, len(d) - 6) + d[-4:]) if len(d) > 6 else "•••"


def one_line(s):
    """WhatsApp template parameters must not contain newlines, tabs or runs of 4+ spaces, and are capped at 1024 characters."""
    return " ".join(str(s).split())[:1000]


def overall(decision):
    codes = [z.get("decision") for z in decision.get("zones", [])]
    if "IRRIGATE" in codes:
        return "IRRIGATE"
    if "SKIP_RAIN" in codes:
        return "SKIP_RAIN"
    return "NO_RUN"


def build_message(decision, farm_name):
    """-> {'code', 'params': [farm, headline, detail]} : the three template variables, one line each, in Gujarati."""
    code = overall(decision)
    zones = decision.get("zones", [])
    if code == "IRRIGATE":
        detail = " ".join(GU["zone_run"].format(id=z.get("id"), min=int(round(z.get("run_minutes") or 0)))
                          for z in zones if z.get("decision") == "IRRIGATE")
    elif code == "SKIP_RAIN":
        mm = (decision.get("farm") or {}).get("forecast_rain_mm_48h")
        detail = GU["rain"].format(mm=int(round(mm))) if mm is not None else GU["rain"].format(mm="?")
    else:
        detail = GU["ample"]
    return {"code": code, "params": [one_line(farm_name), one_line(GU[code]), one_line(detail + " " + GU["note"])]}


def usable(decision, now):
    """(ok, why): only a decision that is for today and still valid is worth sending."""
    if not decision or not decision.get("zones"):
        return False, "no decision to send"
    if str(decision.get("date")) != now.astimezone(IST).date().isoformat():
        return False, f"decision is for {decision.get('date')}, not today"
    try:
        if datetime.fromisoformat(decision["valid_until"]) < now:
            return False, "decision is past its valid_until"
    except Exception:
        return False, "decision has no readable valid_until"
    return True, ""


def payload(to, template, lang, params):
    return {"messaging_product": "whatsapp", "to": to, "type": "template",
            "template": {"name": template, "language": {"code": lang},
                         "components": [{"type": "body", "parameters": [{"type": "text", "text": p} for p in params]}]}}


def send_one(token, pid, body, opener=urllib.request.urlopen):
    req = urllib.request.Request(API.format(pid=pid), data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with opener(req, timeout=20) as r:
            return True, r.status
    except urllib.error.HTTPError as e:
        return False, e.code
    except Exception as e:                     # network problem: never include the request (it carries the token)
        return False, type(e).__name__


def run(env=None, now=None, opener=urllib.request.urlopen, decision=None, state=None, farm_name=None):
    """Returns a dict describing what happened (used by the tests). Writes the state file only after a real send."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    if decision is None:
        decision = json.loads(DECISION.read_text()) if DECISION.exists() else None
    if farm_name is None:
        try:
            farm_name = json.loads(CONFIG.read_text())["farm"]["name"]
        except Exception:
            farm_name = "ખેતર"
    ok, why = usable(decision, now)
    if not ok:
        warn(f"not sent: {why}")
        return {"sent": 0, "skipped": why}
    msg = build_message(decision, farm_name)
    log(f"message ({msg['code']}): " + " | ".join(msg["params"]))

    token, pid = env.get("WHATSAPP_TOKEN", "").strip(), env.get("WHATSAPP_PHONE_NUMBER_ID", "").strip()
    numbers = [n for n in ("".join(c for c in x if c.isdigit()) for x in env.get("WHATSAPP_TO", "").split(",")) if 8 <= len(n) <= 15]
    if env.get("WHATSAPP_DRY_RUN") == "1" or not (token and pid and numbers):
        log("dry run: nothing sent (set WHATSAPP_TOKEN, WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_TO to send)")
        return {"sent": 0, "skipped": "dry run", "message": msg}

    if state is None:
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            state = {}
    if state.get("last_sent_date") == decision["date"] and env.get("FORCE") != "1":
        log(f"already sent for {decision['date']}; nothing to do (FORCE=1 to repeat)")
        return {"sent": 0, "skipped": "already sent", "message": msg}

    template, lang = env.get("WHATSAPP_TEMPLATE", "daily_irrigation_advice"), env.get("WHATSAPP_LANG", "gu")
    sent = failed = 0
    for n in numbers:
        good, status = send_one(token, pid, payload(n, template, lang, msg["params"]), opener)
        if good:
            sent += 1
            log(f"sent to {mask(n)}")
        else:
            failed += 1
            warn(f"could not send to {mask(n)} (status {status})")
    if sent:
        state = {"last_sent_date": decision["date"], "recipients": sent, "failed": failed}
        STATE.write_text(json.dumps(state, indent=2) + "\n")
    return {"sent": sent, "failed": failed, "message": msg}


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:                   # never fail the daily analysis because of a message
        warn(f"unexpected error, nothing sent: {type(exc).__name__}: {exc}")
    sys.exit(0)
