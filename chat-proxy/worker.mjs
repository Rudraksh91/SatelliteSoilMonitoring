// AgriSat chat proxy (Cloudflare Worker) using Google Gemini. The browser cannot hold an API key safely, so the farmer app sends
// questions here and this server adds the key, calls Gemini and returns { answer } (or { diagnosis } for a plant photo, { research } for a
// treatment look-up). See README.md. The Gemini calls themselves live in lib.mjs (geminiAsk, geminiDiagnose, geminiResearch), shared with the
// app's own-key mode.
// Secret: GEMINI_API_KEY (never in the repo). Model: GEMINI_MODEL (default in lib.mjs); GEMINI_RESEARCH_MODEL may name another model for the
// web-search step if the default does not support Google Search grounding. No npm packages are needed. A Claude version is in worker-claude.mjs.
import { makeHandler, geminiAsk, geminiDiagnose, geminiResearch } from './lib.mjs';

const opts = (env, model) => ({ key: env.GEMINI_API_KEY, model: model || env.GEMINI_MODEL });

export default {
  fetch: makeHandler(
    (v, env) => geminiAsk(v, opts(env)),
    (v, env) => geminiDiagnose(v, opts(env)),
    (v, env) => geminiResearch(v, opts(env, env.GEMINI_RESEARCH_MODEL)),
  ),
};
