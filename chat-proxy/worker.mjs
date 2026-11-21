// AgriSat chat proxy (Cloudflare Worker) using Google Gemini. The browser cannot hold an API key safely, so the farmer app sends
// questions here and this server adds the key, calls Gemini and returns { answer }. See README.md.
// Secret: GEMINI_API_KEY (never in the repo). Model: GEMINI_MODEL (default in lib.mjs). No npm packages are needed.
// A Claude version is in worker-claude.mjs.
import { HttpError, makeHandler, buildGeminiBody, readGeminiAnswer, GEMINI_DEFAULT_MODEL } from './lib.mjs';

async function geminiAnswer(v, env) {
  if (!env.GEMINI_API_KEY) throw new HttpError(500, 'server not configured');
  const model = env.GEMINI_MODEL || GEMINI_DEFAULT_MODEL;
  const res = await fetch('https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(model) + ':generateContent', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-goog-api-key': env.GEMINI_API_KEY },   // header, not the URL, so the key never lands in logs
    body: JSON.stringify(buildGeminiBody(v)),
    signal: AbortSignal.timeout(25000),
  });
  if (res.status === 429) throw new HttpError(429, 'busy, try again');
  if (!res.ok) throw new HttpError(502, 'upstream error');
  const out = readGeminiAnswer(await res.json());
  if (out.error) throw new HttpError(out.status, out.error);
  return out.answer;
}

export default { fetch: makeHandler(geminiAnswer) };
