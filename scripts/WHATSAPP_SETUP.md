# Daily WhatsApp advice in Gujarati

Two ways a farmer gets today's advice on WhatsApp. The first works today with no setup; the second needs a one-time WhatsApp Business setup.

| | What it does | Setup | Who it reaches |
|---|---|---|---|
| **Share button** (farmer app) | "Share today's advice on WhatsApp" opens WhatsApp with the advice already written, in the farmer's language (English, Hindi or Gujarati). The farmer chooses who gets it. Nothing is sent until they tap Send in WhatsApp. | none | any farmer, any field |
| **Daily message** (daily job) | Every morning after the analysis, one Gujarati message goes to each opted-in phone number. | WhatsApp Business account, below | the campus farm's decision, for the numbers you list |

**Not tested against the live WhatsApp service.** `tests/test_whatsapp.py` (26 checks) covers the message, the safety rules and the request shape with a fake server. A first real send, to your own number, is the first thing to do after setup.

## What the daily message says

Three lines of Gujarati, built from `data/decision.json`:

1. **Headline:** `આજે પિયત આપો` (irrigate today), `પિયતની જરૂર નથી` (no irrigation needed) or `વરસાદની રાહ જુઓ` (wait for the rain). The same wording as the farmer app.
2. **Detail:** which zones to water and for about how many minutes, or the rain forecast, or "the soil has enough water".
3. **Honesty note:** `આ અંદાજ છે; ખેતર જોઈને નક્કી કરો.` ("This is an estimate; decide by looking at the field.") The daily decision is flagged *not calibrated* until the campus farm is calibrated, so the message always says it is an estimate.

## Safety rules built in

- **Dry run by default.** Without all three secrets it only prints the message in the Actions log.
- **Never sends a stale decision** (not for today, or past its own `valid_until`).
- **At most once per decision date** (`data/whatsapp_state.json`). `FORCE=1` repeats a send.
- **Numbers stay secret:** they live only in the `WHATSAPP_TO` secret, are masked in logs, and are never written to the repository.
- **Cannot break the daily analysis:** the step is `continue-on-error` and the script always exits 0.

## Step 0: add the workflow step (once)

The sender is not part of the daily GitHub job until this step is added to `.github/workflows/ndvi.yml`, because changes to workflow files need a GitHub login with the `workflow` permission. Add the two steps below, one after "Model tests" and one after "Daily job (NDVI + water balance + decision)" and before "Commit results" (the same text is in `scripts/workflow_agrisat_daily.yml` in your working copy if it has not been committed yet):

```yaml
      - name: WhatsApp message tests
        run: python tests/test_whatsapp.py

      - name: WhatsApp advice (Gujarati)
        continue-on-error: true
        env:
          WHATSAPP_TOKEN: ${{ secrets.WHATSAPP_TOKEN }}
          WHATSAPP_PHONE_NUMBER_ID: ${{ secrets.WHATSAPP_PHONE_NUMBER_ID }}
          WHATSAPP_TO: ${{ secrets.WHATSAPP_TO }}
        run: python scripts/whatsapp_notify.py
```

## One-time setup (about an hour, plus Meta's template approval)

1. **Meta Business account** and a **WhatsApp Business app** at developers.facebook.com. Add a sending phone number. WhatsApp's test number can message up to five numbers you verify, which is enough for a first trial.
2. **Create the message template** in WhatsApp Manager. Category **Utility**, language **Gujarati (gu)**, name `daily_irrigation_advice`:

   ```
   નમસ્તે! {{1}} માટે આજની પિયત સલાહ: {{2}}. {{3}} (ઉપગ્રહ અને હવામાન ડેટા પરથી)
   ```

   Sample values Meta asks for: `{{1}}` = `CAET Dediapada`, `{{2}}` = `આજે પિયત આપો`, `{{3}}` = `ઝોન 1: લગભગ 45 મિનિટ પાણી આપો. આ અંદાજ છે; ખેતર જોઈને નક્કી કરો.` Wait until it shows **Approved**. A business can only start a conversation with an approved template.
3. **Permanent access token:** in Business Settings create a *System User*, give it the WhatsApp app, and generate a token with `whatsapp_business_messaging`. (The temporary token expires within a day.)
4. **GitHub secrets** (repository, Settings, Secrets and variables, Actions):
   - `WHATSAPP_TOKEN`: the permanent token
   - `WHATSAPP_PHONE_NUMBER_ID`: the sending number's ID from the Meta dashboard
   - `WHATSAPP_TO`: comma-separated numbers with country code, digits only, for example `919876543210,919812345678`
5. **Trial:** Actions, "AgriSat daily", Run workflow. Read the "WhatsApp advice (Gujarati)" step: it prints the message and, per number, `sent to +91••••••3210` or a warning with the status code. Check that your own phone received it.

## Rules to follow

- **Only message people who agreed.** Ask each person (in Gujarati, in writing or by voice) before adding their number, and remove a number the moment they ask. This is a legal requirement for WhatsApp Business as well as good practice (India's DPDP Act 2023 applies to phone numbers).
- **Cost:** business-initiated messages are charged per message by Meta, and the price depends on the category and country. Check Meta's current India price list before adding many numbers. The Share button is free.
- **Language:** the message text is built in Gujarati, so the template must be Gujarati (`WHATSAPP_LANG=gu`, the default). A Hindi or English version would need its own text in `scripts/whatsapp_notify.py`.

## Limits to know

- The daily message covers the **campus farm's** decision, the one the daily job calculates. A farmer's own field lives only on their phone (by design), so the server cannot message them about it. They get their own field's advice with the **Share button**. Messaging pilot farmers' own fields automatically would need each farmer's field outline stored somewhere the job can read, which is a deliberate privacy trade-off and is not built.
- The advice is an estimate until the campus farm is calibrated (see the main status notes).

## Files

- `scripts/whatsapp_notify.py`: builds and sends the message. Run `python3 scripts/whatsapp_notify.py` locally for a dry run.
- `tests/test_whatsapp.py`, `tests/test_share.js`: offline tests for the sender and the Share button.
- `farmer/share.js`: the WhatsApp text and link used by the Share button.
- `.github/workflows/ndvi.yml`: runs the tests and the sender after the daily analysis (once Step 0 is done).
