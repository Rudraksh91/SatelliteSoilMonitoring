/* AgriSat plant check: helpers for the "photograph a plant, get advice" card.
 *
 * What the page does with them:
 *   AgriDiagnose.prepareImage(file)      -> Promise<{ dataUrl, b64, mime, width, height }>  browser only: draws the photo smaller on a canvas and
 *                                           saves it again as JPEG. That drops all camera metadata (location, time, phone model) on purpose.
 *   AgriDiagnose.buildRequest(o)         -> the JSON body for the optional AI service (chat-proxy): photo, crop name, optional note, language, field facts
 *   AgriDiagnose.normalize(raw)          -> a clean, bounded result, or null when the service's reply is not usable (never trusted as HTML)
 *   AgriDiagnose.researchWanted(d)       -> true when the photo result names a problem worth looking up (not healthy, not a bad photo)
 *   AgriDiagnose.buildResearchRequest(o) -> the text-only body for the second step: the problem's NAME (never the photo) goes to a web search
 *   AgriDiagnose.normalizeResearch(raw)  -> clean treatment findings with their web sources (https links only), or null
 *   AgriDiagnose.fitSize(w, h, max)      -> new width and height with the long side at most `max`
 *   AgriDiagnose.tone(d)                 -> 'good' | 'warn' | 'bad' | 'none': how the result card is coloured
 *   AgriDiagnose.confKey(d)              -> the i18n key of the confidence label
 *
 * Nothing here reads a location or a name. The photo and a few anonymous field facts are the only things that leave the phone, and only after the
 * farmer taps Allow. This is an AI's reading of one photo: it is never presented as a laboratory result.
 * UMD: works in the browser (window.AgriDiagnose) and in Node (tests/test_diagnose.js).
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriDiagnose = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const MAX_SIDE = 1024;                 // long side of the photo that is sent: enough to see leaf spots, small enough for a village connection
  const JPEG_QUALITY = 0.82;
  const MAX_B64 = 1400000;               // must stay below chat-proxy's MAX_IMAGE_B64
  const KINDS = ['plant', 'not_plant', 'unclear_photo'];
  const CATS = ['disease', 'pest', 'nutrient', 'water', 'weather_damage', 'weed', 'other', 'none'];
  const CONFS = ['high', 'medium', 'low'];

  function fitSize(w, h, max) {
    max = max || MAX_SIDE; w = Math.max(1, Math.round(w)); h = Math.max(1, Math.round(h));
    const s = Math.min(1, max / Math.max(w, h));
    return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
  }

  const clean = (x, n) => (typeof x === 'string' ? x.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, n) : '');

  /** The photo as a data URL -> base64 body and mime. Returns null when it is not a JPEG/PNG/WebP data URL. */
  function splitDataUrl(u) {
    const m = /^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/.exec(String(u || ''));
    return m ? { mime: m[1], b64: m[2] } : null;
  }

  /** o: { b64, mime, lang, crop, note, context }. crop is the crop's plain name (not the field's name). */
  function buildRequest(o) {
    o = o || {};
    return { task: 'diagnose', image: String(o.b64 || ''), mime: o.mime || 'image/jpeg', lang: o.lang || 'en',
      crop: clean(o.crop, 60), note: clean(o.note, 200), context: o.context || null };
  }

  /** The AI service's reply -> what the card shows. Anything missing or oversized is dropped or cut; unknown values fall back to the cautious one. */
  function normalize(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const kind = KINDS.includes(raw.kind) ? raw.kind : 'plant';
    const d = {
      kind, healthy: kind === 'plant' && typeof raw.healthy === 'boolean' ? raw.healthy : null,
      problem: clean(raw.problem, 120), category: CATS.includes(raw.category) ? raw.category : 'none',
      confidence: CONFS.includes(raw.confidence) ? raw.confidence : 'low',
      looks_like: clean(raw.looks_like, 400), why: clean(raw.why, 500),
      do_now: (Array.isArray(raw.do_now) ? raw.do_now : []).map(x => clean(x, 220)).filter(Boolean).slice(0, 4),
      chemical_note: clean(raw.chemical_note, 300), get_help: clean(raw.get_help, 300), retake: clean(raw.retake, 300),
    };
    if (kind === 'plant' && d.healthy === null && !d.problem) return null;
    if (kind !== 'plant' && !d.retake) return null;
    return d;
  }

  /** The second step: once the photo has a named problem, the app looks up how it is treated. Only the NAME of the problem is sent (no photo). */
  const researchWanted = d => !!(d && d.kind === 'plant' && d.healthy === false && d.problem);
  function buildResearchRequest(o) {
    o = o || {};
    return { task: 'research', problem: clean(o.problem, 120), crop: clean(o.crop, 60), category: CATS.includes(o.category) ? o.category : 'other', lang: o.lang || 'en' };
  }
  /** Treatment findings from the AI service. Doses are only shown if the service says a source states them; links must be https. */
  function normalizeResearch(raw) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
    const list = (a, k, n) => (Array.isArray(a) ? a : []).map(x => clean(x, n)).filter(Boolean).slice(0, k);
    const meds = (Array.isArray(raw.medicines) ? raw.medicines : []).filter(m => m && typeof m === 'object').map(m => ({
      active_ingredient: clean(m.active_ingredient, 80), use: clean(m.use, 200), dose_in_source: clean(m.dose_in_source, 120), wait_note: clean(m.wait_note, 160),
    })).filter(m => m.active_ingredient).slice(0, 5);
    const seen = new Set(), sources = [];
    (Array.isArray(raw.sources) ? raw.sources : []).forEach(x => {
      const url = x && typeof x.url === 'string' ? x.url.trim() : '';
      if (!/^https:\/\/[^\s"'<>]+$/.test(url) || url.length > 600 || seen.has(url) || sources.length >= 6) return;
      seen.add(url); sources.push({ url, title: clean(x.title, 100) || url.replace(/^https:\/\//, '').split('/')[0] });
    });
    const r = { found: raw.found !== false, summary: clean(raw.summary, 500), cultural: list(raw.cultural, 4, 220), natural: list(raw.natural, 4, 220), medicines: meds,
      safety: list(raw.safety, 4, 200), agree: CONFS.includes(raw.agree) ? raw.agree : 'low', sources };
    if (!r.summary && !r.cultural.length && !r.natural.length && !r.medicines.length) { r.found = false; r.agree = 'low'; }
    return r;
  }

  const tone = d => !d ? 'none' : d.kind !== 'plant' ? 'none' : d.healthy ? 'good' : d.confidence === 'low' ? 'warn' : (d.category === 'water' || d.category === 'nutrient' || d.category === 'weather_damage') ? 'warn' : 'bad';
  const confKey = d => ({ high: 'pcConfHigh', medium: 'pcConfMed' }[d && d.confidence] || 'pcConfLow');

  /** Browser only. Reads the picked or captured file, draws it at most MAX_SIDE on the long side and returns it as JPEG. */
  async function prepareImage(file, doc) {
    doc = doc || (typeof document !== 'undefined' ? document : null);
    if (!file || !doc) throw new Error('no photo');
    if (!/^image\//.test(file.type || '')) throw new Error('not an image');
    let src, w, h, release = () => {};
    if (typeof createImageBitmap === 'function') {
      try { src = await createImageBitmap(file, { imageOrientation: 'from-image' }); w = src.width; h = src.height; release = () => src.close && src.close(); } catch (e) { src = null; }
    }
    if (!src) {                                   // older browsers: decode through an <img>
      const url = URL.createObjectURL(file);
      try {
        src = await new Promise((ok, no) => { const im = new Image(); im.onload = () => ok(im); im.onerror = () => no(new Error('unreadable photo')); im.src = url; });
        w = src.naturalWidth; h = src.naturalHeight;
      } finally { URL.revokeObjectURL(url); }
    }
    try {
      const s = fitSize(w, h, MAX_SIDE), c = doc.createElement('canvas');
      c.width = s.width; c.height = s.height;
      const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, s.width, s.height); g.drawImage(src, 0, 0, s.width, s.height);
      let q = JPEG_QUALITY, dataUrl = c.toDataURL('image/jpeg', q);
      while (dataUrl.length > MAX_B64 && q > 0.4) { q -= 0.12; dataUrl = c.toDataURL('image/jpeg', q); }
      const p = splitDataUrl(dataUrl);
      if (!p || p.b64.length > MAX_B64) throw new Error('photo too large');
      return { dataUrl, b64: p.b64, mime: p.mime, width: s.width, height: s.height };
    } finally { release(); }
  }

  return { MAX_SIDE, MAX_B64, KINDS, CATS, CONFS, fitSize, splitDataUrl, buildRequest, normalize, researchWanted, buildResearchRequest, normalizeResearch, tone, confKey, prepareImage };
});
