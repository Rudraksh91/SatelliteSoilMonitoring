// AgriSat chat proxy (Cloudflare Worker) using Google Gemini. The browser cannot hold an API key safely, so the farmer app sends
// questions here and this server adds the key, calls Gemini and returns { answer }. See README.md.
// Secret: GEMINI_API_KEY (never in the repo). Model: GEMINI_MODEL (default in lib.mjs). No npm packages are needed.
// A Claude version is in worker-claude.mjs.
import { HttpError, makeHandler, buildGeminiBody, buildGeminiDiagnoseBody, buildGeminiResearchBody, readGeminiAnswer, readDiagnosis, readResearch, sourcesFromGemini, GEMINI_DEFAULT_MODEL } from './lib.mjs';

async function callGemini(body, env, ms, modelName) {
  if (!env.GEMINI_API_KEY) throw new HttpError(500, 'server not configured');
  const model = modelName || env.GEMINI_MODEL || GEMINI_DEFAULT_MODEL;
  return fetch('https://generativelanguage.googleapis.com/v1beta/models/' + encodeURIComponent(model) + ':generateContent', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-goog-api-key': env.GEMINI_API_KEY },   // header, not the URL, so the key never lands in logs
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(ms),
  });
}

async function geminiAnswer(v, env) {
  const res = await callGemini(buildGeminiBody(v), env, 25000);
  if (res.status === 429) throw new HttpError(429, 'busy, try again');
  if (!res.ok) throw new HttpError(502, 'upstream error');
  const out = readGeminiAnswer(await res.json());
  if (out.error) throw new HttpError(out.status, out.error);
  return out.answer;
}

// Plant photo check: the photo and a few anonymous facts go to Gemini, the reply must be the JSON described in lib.mjs (DIAGNOSE_SYSTEM).
async function geminiDiagnose(v, env) {
  const res = await callGemini(buildGeminiDiagnoseBody(v), env, 40000);
  if (res.status === 429) throw new HttpError(429, 'busy, try again');
  if (!res.ok) throw new HttpError(502, 'upstream error');
  const out = readGeminiAnswer(await res.json());
  if (out.error) throw new HttpError(out.status, out.error);
  const d = readDiagnosis(out.answer);
  if (!d) throw new HttpError(502, 'unreadable answer');
  return d;
}

// Treatment look-up: Gemini searches Google for the named problem (grounding) and reports what the sources say. Sources are read from the
// search metadata. GEMINI_RESEARCH_MODEL may name a different model if the default does not support Google Search grounding.
async function geminiResearch(v, env) {
  const res = await callGemini(buildGeminiResearchBody(v), env, 60000, env.GEMINI_RESEARCH_MODEL);
  if (res.status === 429) throw new HttpError(429, 'busy, try again');
  if (!res.ok) throw new HttpError(502, 'upstream error');
  const j = await res.json();
  const out = readGeminiAnswer(j);
  if (out.error) throw new HttpError(out.status, out.error);
  const r = readResearch(out.answer);
  if (!r) throw new HttpError(502, 'unreadable answer');
  return Object.assign(r, { sources: sourcesFromGemini(j) });
}

export default { fetch: makeHandler(geminiAnswer, geminiDiagnose, geminiResearch) };
