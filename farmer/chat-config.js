/* Optional AI service for the farmer assistant.
 *
 * The assistant (farmer/assistant.js) already answers questions about a farmer's own field in the browser, with no server and
 * no key. For OTHER questions it cannot answer, it can ask an AI service, but only if you set this URL to a deployed copy of
 * chat-proxy (a small server that keeps the API key secret; see chat-proxy/README.md). The proxy talks to Google Gemini by
 * default (chat-proxy/worker.mjs) or to Claude (chat-proxy/worker-claude.mjs).
 *
 * The same URL also serves the Plant health check (camera) card (photo reading, then a web search for the treatment of the problem it names): with no URL the card just points the farmer to a KVK / the Kisan Call
 * Centre, it never invents a diagnosis.
 *
 * Leave the URL empty to keep everything in the browser. Never put an API key in this file: it is public.
 */
window.AGRISAT_CHAT_URL = '';
// Name shown to the farmer in the consent message and on AI answers: say who receives the question. Change it if you switch provider.
window.AGRISAT_CHAT_NAME = 'Google Gemini';
