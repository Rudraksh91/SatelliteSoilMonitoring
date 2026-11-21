# AgriSat chat proxy (optional): Google Gemini, or Claude

The farmer app's **Ask** button already answers questions about a farmer's own field **in the browser**, with no server and
no key (`farmer/assistant.js`). It uses the advice the app has already computed: today's decision, litres, soil water, rain
forecast and NDVI.

This proxy is what lets the Ask button answer **any agriculture question**, for example "how far apart should I sow cotton?" or "my
brinjal leaves are curling, what is wrong?". Without it the app still answers the field questions and eight general farming topics
(sowing seasons, soil health, compost, fertiliser, saving water, pests, crop rotation, seeds) from short built-in answers. It exists because an API key can never be placed in a public web page: the page sends the
question here, and this small server adds the key, calls the AI model and returns the answer.

| File | Provider | Needs |
|---|---|---|
| `worker.mjs` (default) | **Google Gemini** | a Gemini API key; no npm packages |
| `worker-claude.mjs` | Claude (Anthropic) | an Anthropic API key and `npm install @anthropic-ai/sdk` |

Both share the same checks and the same system prompt (`lib.mjs`). **It costs money or uses a quota, and it sends questions to a
third-party AI service.** It is off until you set it up.

## What gets sent

Only for questions that are **not about the farmer's own field** (general farming questions and anything the built-in assistant has no answer for), and only after the farmer taps **Yes** on the consent message (which names the service):

- the question text (max 500 characters) and the app language
- a short anonymous summary of the open field: crop, area, today's decision, litres, soil-water numbers, 3-day rain and ET0
  forecast, NDVI. **No field name, no coordinates, no account details** (`AgriAssistant.llmContext`).
- up to the last 6 turns of that AI conversation

## Set up with Gemini (about 10 minutes)

1. Get a key from Google AI Studio (aistudio.google.com, "Get API key"). Check Google's current free-tier limits and terms there.
   **Read the terms on how prompts are handled on the free tier versus a paid project**: farmers' questions are sent to Google.
2. In this folder:

   ```bash
   npm install wrangler
   npx wrangler login
   npx wrangler secret put GEMINI_API_KEY     # paste the key; it is stored encrypted, not in the repo
   npx wrangler deploy
   ```

3. Wrangler prints the Worker URL, e.g. `https://agrisat-chat.<your-name>.workers.dev`. Put it in
   [`farmer/chat-config.js`](../farmer/chat-config.js) and keep `AGRISAT_CHAT_NAME = 'Google Gemini'`:

   ```js
   window.AGRISAT_CHAT_URL = 'https://agrisat-chat.<your-name>.workers.dev';
   ```

4. Commit and push `farmer/chat-config.js`. That URL is public by design; the key is not in it.

## Use Claude instead

```bash
npm install @anthropic-ai/sdk wrangler
npx wrangler secret put ANTHROPIC_API_KEY
```

Then set `main = "worker-claude.mjs"` in `wrangler.toml`, run `npx wrangler deploy`, and set `AGRISAT_CHAT_NAME = 'Claude'`
in `farmer/chat-config.js`. The default model is `claude-opus-5-5`; `CHAT_MODEL = "claude-haiku-5-5"` is far cheaper.

## Settings (`wrangler.toml`)

| Setting | Meaning |
|---|---|
| `ALLOWED_ORIGINS` | Websites allowed to call the proxy. Default is the GitHub Pages site. Add `http://localhost:7439` only while testing. |
| `GEMINI_MODEL` | Gemini model ID. The default in `lib.mjs` is `gemini-3.5-flash-lite` (a stable, low-cost model at the time of writing). **Model IDs change: check Google AI Studio's model list before deploying and set this if the default is gone.** |
| `CHAT_MODEL` | Claude model ID, for `worker-claude.mjs` only. |

## Protecting your bill and quota

- The proxy rejects other websites and caps the question (500 chars), the field summary (3000 chars), the answer (1500 tokens)
  and the wait for the AI (25 s).
- It has **no per-user rate limit of its own**. Before sharing the app widely, add a Cloudflare rate-limiting rule for this Worker
  (Cloudflare dashboard, Security, WAF, Rate limiting) and a spend limit or quota alert on the AI account.
- Origin checks stop other websites in browsers, not scripts that fake the header. Treat the Worker URL as something that can be
  abused and keep the limits in place.

## Behaviour

The system prompt (`lib.mjs`) tells the model to: answer **any question about agriculture** (crops, sowing, soil, fertiliser,
irrigation, weeds, pests and diseases, harvest and storage, horticulture, dairy and animal husbandry, tools, markets, schemes) in the
farmer's language with practical, specific answers; use only the numbers in the field summary for the farmer's own field and never
contradict the app's irrigation decision; for pests and diseases give likely causes and integrated management first, follow the product
label for any chemical and **never invent exact doses**; treat prices, subsidies and rules as changeable and send the farmer to the
local agriculture office, a Krishi Vigyan Kendra or the Kisan Call Centre (1800-180-1551) to confirm; and politely decline questions
that are not about farming. The app labels these replies "AI answer · <service name>" and reminds the farmer they can be wrong. If
the AI declines a question or is unreachable, the farmer sees a polite message plus the built-in pointer to the agriculture officer.

## Tests

`node tests/test_chat_proxy.js` (from the repository root) checks validation, the system prompt, message building and the whole
Gemini request/response handling against a **mocked** Google endpoint: the key goes in a header, roles map to user/model, blocked
or empty answers become polite errors, and upstream details are never leaked. **It has not been run against the live Gemini API**,
because that needs a real key: please send one real question after deploying.
