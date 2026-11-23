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
 *   AgriDiagnose.direct.diagnose / .research(body, key, opts) -> own-key mode: the same Gemini calls the proxy makes, run in this page with the user's OWN
 *                                        key (kept on this device, sent only to Google). Uses chat-proxy/lib.mjs so the prompts and checks are identical.
 *   AgriDiagnose.camConstraints(deviceId) / camErrorKey(e) -> getUserMedia settings for the live camera window, and the wording key for a camera error
 *   AgriDiagnose.lookupReference(o, deps) / normalizeReference(raw) -> the no-key route: Wikipedia lookup of the symptom, translated in the background, all shown inside the app
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

  /** Live camera (computers and any device with getUserMedia): the back camera if there is one, else the default; a chosen camera by id. */
  const camConstraints = deviceId => ({ audio: false, video: deviceId
    ? { deviceId: { exact: deviceId }, width: { ideal: 1920 }, height: { ideal: 1080 } }
    : { facingMode: { ideal: 'environment' }, width: { ideal: 1920 }, height: { ideal: 1080 } } });
  /** getUserMedia failure -> the i18n key that tells the farmer what to do. */
  const camErrorKey = e => {
    const n = e && e.name;
    if (n === 'NotAllowedError' || n === 'SecurityError' || n === 'PermissionDeniedError') return 'pcCamDenied';
    if (n === 'NotFoundError' || n === 'DevicesNotFoundError' || n === 'OverconstrainedError') return 'pcCamNone';
    if (n === 'NotReadableError' || n === 'TrackStartError' || n === 'AbortError') return 'pcCamBusy';
    return 'pcCamFail';
  };

  /** No key, no account, no Google page: the app looks the symptom up in Wikipedia in the background (English, CORS-open, no key), picks the sections on
   *  symptoms and control, and, for Hindi or Gujarati, translates them with Google Translate's public endpoint, also in the background. Everything is shown
   *  inside the app. This is a general reference, not a diagnosis: it never sees the photo. */
  const SYMPTOMS = { spots: 'leaf spots', curl: 'leaf curling', yellow: 'yellowing leaves', wilt: 'wilting plant', holes: 'holes in leaves insects', powder: 'white powdery coating on leaves', fruit: 'fruit rot and fruit drop' };
  // Wikipedia's search is word matching, not understanding, so each symptom is turned into the names of the diseases and pests it usually points to.
  const SYMPTOM_TERMS = { spots: ['leaf spot disease', 'anthracnose'], curl: ['leaf curl virus', 'whitefly'], yellow: ['chlorosis', 'mosaic virus'], wilt: ['wilt disease', 'fusarium wilt'],
    holes: ['caterpillar pest', 'armyworm'], powder: ['powdery mildew', 'downy mildew'], fruit: ['fruit rot', 'fruit fly'] };
  const WIKI = 'https://en.wikipedia.org/';
  const wikiApi = p => WIKI + 'w/api.php?' + Object.keys(p).map(k => k + '=' + encodeURIComponent(p[k])).join('&');
  const wikiUrl = title => WIKI + 'wiki/' + encodeURIComponent(String(title).replace(/ /g, '_')).replace(/%2F/g, '/');
  function referenceQuery(o) {
    o = o || {};
    const what = Object.prototype.hasOwnProperty.call(SYMPTOMS, o.symptom) ? SYMPTOMS[o.symptom] : (clean(o.note, 120) || 'leaf disease');
    return [clean(o.crop, 60), what, 'disease'].filter(Boolean).join(' ');
  }
  /** Up to three searches: the crop with each disease name, and the first disease name on its own. */
  function referenceQueries(o) {
    o = o || {};
    const crop = clean(o.crop, 60), note = clean(o.note, 120);
    const terms = Object.prototype.hasOwnProperty.call(SYMPTOM_TERMS, o.symptom) ? SYMPTOM_TERMS[o.symptom] : [note ? note + ' disease' : 'leaf disease', note ? note + ' pest' : 'crop pest'];
    return [[crop, terms[0]], [terms[0]], [crop, terms[1]]].map(a => a.filter(Boolean).join(' ')).filter((q, i, all) => q && all.indexOf(q) === i);
  }
  /** Article titles to try directly (Wikipedia has articles named exactly like most diseases and pests): the crop with the disease name, and the disease name alone. */
  function directTitles(o) {
    o = o || {};
    const crop = clean(o.crop, 60), cap = t => t.charAt(0).toUpperCase() + t.slice(1);
    const terms = Object.prototype.hasOwnProperty.call(SYMPTOM_TERMS, o.symptom) ? SYMPTOM_TERMS[o.symptom] : [];
    const out = []; terms.forEach(t => { if (crop) out.push(cap(crop.toLowerCase()) + ' ' + t); out.push(cap(t)); });
    return out.filter((t, i, a) => a.indexOf(t) === i);
  }
  /** Search results -> up to 3 pages most likely to be a plant disease or pest, best first. */
  function rankSearch(json, crop) {
    const list = json && json.query && Array.isArray(json.query.search) ? json.query.search : [];
    const bad = /institute|university|college|list of|glossary|disambiguation|company|society|association|ministry|journal|\bact\b|policy|national|research|fungicide use|agriculture in|deaths in|\bplant$/i;
    const good = /disease|virus|blight|rust|wilt|mildew|rot\b|spot|pest|insect|mosaic|curl|fung|bacteri|nematode|mite|aphid|whitefly|borer|caterpillar|weevil|smut|scab|canker|pathogen/i;
    const c = clean(crop, 60).toLowerCase().split(' ')[0];
    return list.map((r, i) => {
      const title = clean(r.title, 120), snip = clean(r.snippet, 400);
      let sc = (good.test(title) ? 3 : 0) + (good.test(snip) ? 2 : 0) + (c && (title.toLowerCase().includes(c) || snip.toLowerCase().includes(c)) ? 2 : 0) - (bad.test(title) ? 5 : 0) - i * 0.1;
      return { title, score: sc };
    }).filter(x => x.title && x.score >= 2).sort((a, b) => b.score - a.score).slice(0, 3);
  }
  function clip(t, n) {
    t = String(t || '').replace(/\[\d+\]/g, '').replace(/\s+/g, ' ').trim();
    if (t.length <= n) return t;
    const cut = t.slice(0, n), i = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('; '));
    return (i > n * 0.5 ? cut.slice(0, i + 1) : cut.replace(/\s+\S*$/, '') + '…');
  }
  /** Plain-text page (explaintext) -> { intro, signs, control }. Section names are matched, not trusted. */
  function pageFacts(title, text) {
    const parts = String(text || '').replace(/\s*$/, '\n').split(/\n(?=={2,}\s*[^=\n]+?\s*={2,}\s*\n)/);
    const body = p => p.split('\n').slice(1).join('\n');
    const head = p => p.split('\n')[0].replace(/=/g, '').trim();
    const find = re => parts.slice(1).find(p => re.test(head(p)));
    const sec = find(/symptom|sign|appearance|description/i), ctl = find(/control|management|prevention|treatment/i) || find(/resistance/i);
    const f = { title: clean(title, 120), url: wikiUrl(title), intro: clip(parts[0], 380), signs: sec ? clip(body(sec), 600) : '', control: ctl ? clip(body(ctl), 700) : '' };
    return f;
  }
  const gjson = async (fn, url) => { const r = await fn(url); if (!r.ok) throw new Error('http ' + r.status); return r.json(); };
  /** Google Translate's public endpoint (no key). Throws when it refuses; the caller then shows the English text. */
  async function translateText(text, lang, fetchFn) {
    const fn = fetchFn || ((...a) => fetch(...a));
    if (!text || !/^(hi|gu)$/.test(lang)) return text;
    const j = await gjson(fn, 'https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=' + lang + '&dt=t&q=' + encodeURIComponent(String(text).slice(0, 1800)));
    const out = Array.isArray(j && j[0]) ? j[0].map(x => (x && x[0]) || '').join('') : '';
    if (!out) throw new Error('no translation');
    return clean(out, 2000);
  }
  /** o = { crop, symptom, note, lang }; deps = { fetchFn }. -> { found, pages:[{title,url,intro,signs,control}], translated } */
  async function lookupReference(o, deps) {
    const fn = (deps && deps.fetchFn) || ((...a) => fetch(...a));
    const found = await Promise.all(referenceQueries(o).map(async (q, i) => {
      try { const j = await gjson(fn, wikiApi({ action: 'query', list: 'search', srsearch: q, srlimit: 6, format: 'json', origin: '*' })); return (j.query && j.query.search) || []; }
      catch (e) { if (i === 0) throw e; return []; }               // the first search must work; the others only add candidates
    }));
    const seen = new Set(), merged = [];
    found.forEach(list => list.forEach(r => { if (r && !seen.has(r.title)) { seen.add(r.title); merged.push(r); } }));
    const ranked = rankSearch({ query: { search: merged } }, o && o.crop).map(p => p.title);
    const candidates = directTitles(o).concat(ranked).filter((t, i, a) => a.indexOf(t) === i).slice(0, 6);      // exact-name articles first, then the best search hits
    let pages = (await Promise.all(candidates.map(async title => {
      try {
        const j = await gjson(fn, wikiApi({ action: 'query', prop: 'extracts', explaintext: 1, titles: title, redirects: 1, format: 'json', origin: '*' }));
        const pg = Object.values((j.query && j.query.pages) || {})[0] || {};
        return pg.missing !== undefined ? null : pageFacts(pg.title || title, pg.extract || '');
      } catch (e) { return null; }
    }))).filter((x, i, a) => x && (x.signs || x.control) && a.findIndex(y => y && y.title === x.title) === i).slice(0, 3);
    const lang = (o && o.lang) || 'en';
    let translated = false;
    if (pages.length && /^(hi|gu)$/.test(lang)) {
      try {
        pages = await Promise.all(pages.map(async x => Object.assign({}, x, {
          intro: await translateText(x.intro, lang, fn), signs: x.signs ? await translateText(x.signs, lang, fn) : '', control: x.control ? await translateText(x.control, lang, fn) : '' })));
        translated = true;
      } catch (e) { translated = false; }                    // the English text is shown instead, and the note says so
    }
    return { found: pages.length > 0, pages, translated };
  }
  /** What the card shows: only Wikipedia links, text without tags, bounded. */
  function normalizeReference(raw) {
    if (!raw || typeof raw !== 'object') return null;
    const pages = (Array.isArray(raw.pages) ? raw.pages : []).map(p => ({ title: clean(p && p.title, 120), url: typeof (p && p.url) === 'string' && /^https:\/\/en\.wikipedia\.org\/wiki\/[^\s"'<>]+$/.test(p.url) ? p.url : '',
      intro: clean(p && p.intro, 450), signs: clean(p && p.signs, 700), control: clean(p && p.control, 800) })).filter(p => p.title && p.url && (p.signs || p.control)).slice(0, 3);
    return { found: pages.length > 0, pages, translated: raw.translated === true };
  }

  /** A pasted API key: no spaces or quotes, 20 to 200 characters. (Google decides if it is really valid.) */
  const validKey = k => typeof k === 'string' && /^[A-Za-z0-9_.\-]{20,200}$/.test(k.trim());

  // Own-key mode. The page imports the proxy's shared library and makes the same Gemini request the Worker would make. opts: { model, fetchFn }.
  let libP = null;
  const lib = () => libP || (libP = import('../chat-proxy/lib.mjs'));   // resolved against this script's URL
  const direct = {
    async diagnose(body, key, opts) {
      const L = await lib(), p = L.parseDiagnoseRequest(body); if (!p.ok) throw new Error(p.error);
      return L.geminiDiagnose(p.value, Object.assign({ key }, opts));
    },
    async research(body, key, opts) {
      const L = await lib(), p = L.parseResearchRequest(body); if (!p.ok) throw new Error(p.error);
      return L.geminiResearch(p.value, Object.assign({ key }, opts));
    },
  };

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

  return { MAX_SIDE, MAX_B64, KINDS, CATS, CONFS, fitSize, splitDataUrl, buildRequest, normalize, researchWanted, buildResearchRequest, normalizeResearch, camConstraints, camErrorKey, SYMPTOMS, SYMPTOM_TERMS, referenceQuery, referenceQueries, directTitles, rankSearch, pageFacts, translateText, lookupReference, normalizeReference, wikiUrl, validKey, direct, tone, confKey, prepareImage };
});
