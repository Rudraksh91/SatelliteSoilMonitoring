// Claude version of the chat proxy. Use it instead of worker.mjs by setting  main = "worker-claude.mjs"  in wrangler.toml and
// installing the SDK:  npm install @anthropic-ai/sdk . Secret: ANTHROPIC_API_KEY. Model: CHAT_MODEL (default claude-opus-5-5).
import Anthropic from '@anthropic-ai/sdk';
import { HttpError, makeHandler, buildMessages, SYSTEM, DIAGNOSE_SYSTEM, buildDiagnoseText, readDiagnosis } from './lib.mjs';

async function claudeAnswer(v, env) {
  if (!env.ANTHROPIC_API_KEY) throw new HttpError(500, 'server not configured');
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
  const model = env.CHAT_MODEL || 'claude-opus-5-5';
  const params = { model, max_tokens: 1500, system: SYSTEM, messages: buildMessages(v), output_config: { effort: 'low' } };
  try {
    // Models that can decline a request get the server-side fallback, so a false-positive refusal is answered by another model.
    const res = /^claude-(opus-5|sonnet-5-5|fable)/.test(model)
      ? await client.beta.messages.create({ ...params, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' })
      : await client.messages.create(params);
    if (res.stop_reason === 'refusal') throw new HttpError(422, 'declined');
    const answer = res.content.filter(b => b.type === 'text').map(b => b.text).join('').trim();
    if (!answer) throw new HttpError(502, 'empty answer');
    return answer;
  } catch (e) {
    if (e instanceof HttpError) throw e;
    if (e instanceof Anthropic.RateLimitError) throw new HttpError(429, 'busy, try again');
    throw new HttpError(502, 'upstream error');
  }
}

// Plant photo check with Claude: the photo goes in an image block, the reply must be the JSON described in lib.mjs (DIAGNOSE_SYSTEM).
async function claudeDiagnose(v, env) {
  if (!env.ANTHROPIC_API_KEY) throw new HttpError(500, 'server not configured');
  const client = new Anthropic({ apiKey: env.ANTHROPIC_API_KEY });
  const model = env.CHAT_MODEL || 'claude-opus-5-5';
  const params = { model, max_tokens: 1500, system: DIAGNOSE_SYSTEM, output_config: { effort: 'low' },
    messages: [{ role: 'user', content: [{ type: 'image', source: { type: 'base64', media_type: v.mime, data: v.image } }, { type: 'text', text: buildDiagnoseText(v) }] }] };
  try {
    const res = /^claude-(opus-5|sonnet-5-5|fable)/.test(model)
      ? await client.beta.messages.create({ ...params, betas: ['server-side-fallback-2026-07-01'], fallbacks: 'default' })
      : await client.messages.create(params);
    if (res.stop_reason === 'refusal') throw new HttpError(422, 'declined');
    const d = readDiagnosis(res.content.filter(b => b.type === 'text').map(b => b.text).join(''));
    if (!d) throw new HttpError(502, 'unreadable answer');
    return d;
  } catch (e) {
    if (e instanceof HttpError) throw e;
    if (e instanceof Anthropic.RateLimitError) throw new HttpError(429, 'busy, try again');
    throw new HttpError(502, 'upstream error');
  }
}

export default { fetch: makeHandler(claudeAnswer, claudeDiagnose) };
