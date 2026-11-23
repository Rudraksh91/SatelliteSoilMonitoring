// Offline tests for farmer/diagnose.js (the plant-check helpers): sizing, request building, and cleaning of the AI service's reply.
// Run: node tests/test_diagnose.js
const D = require('../farmer/diagnose.js'), I = require('../farmer/i18n.js');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };

// sizing
check('fitSize: a large photo is scaled so the long side is 1024', JSON.stringify(D.fitSize(4000, 3000)) === JSON.stringify({ width: 1024, height: 768 }) && JSON.stringify(D.fitSize(3000, 4000)) === JSON.stringify({ width: 768, height: 1024 }));
check('fitSize: a small photo is never enlarged', JSON.stringify(D.fitSize(640, 480)) === JSON.stringify({ width: 640, height: 480 }));
check('fitSize: never returns 0 and survives odd input', D.fitSize(1, 5000).width === 1 && D.fitSize(0, 0).width === 1 && D.fitSize(1500.6, 900.2).width === 1024);

// data URLs
const sp = D.splitDataUrl('data:image/jpeg;base64,/9j/4AAQ');
check('splitDataUrl: reads mime and body', sp && sp.mime === 'image/jpeg' && sp.b64 === '/9j/4AAQ');
check('splitDataUrl: refuses other types and junk', D.splitDataUrl('data:text/html;base64,PGI+') === null && D.splitDataUrl('javascript:alert(1)') === null && D.splitDataUrl('data:image/svg+xml;base64,PHN2Zz4=') === null && D.splitDataUrl(null) === null);

// request
const rq = D.buildRequest({ b64: '/9j/AAAA', mime: 'image/jpeg', lang: 'gu', crop: 'Cotton\n<b>x</b>', note: 'spots '.repeat(100), context: { decision: 'OK' } });
check('buildRequest: task, photo, language and context go through', rq.task === 'diagnose' && rq.image === '/9j/AAAA' && rq.mime === 'image/jpeg' && rq.lang === 'gu' && rq.context.decision === 'OK');
check('buildRequest: crop and note are stripped of tags/newlines and length-limited', !/[<>\n]/.test(rq.crop) && rq.note.length <= 200 && rq.crop.length <= 60);
const keys = Object.keys(rq).sort().join();
check('buildRequest: carries nothing but the photo, crop, note, language and anonymous field facts (no location, no name)', keys === 'context,crop,image,lang,mime,note,task');
check('buildRequest: defaults are safe', D.buildRequest({}).lang === 'en' && D.buildRequest({}).mime === 'image/jpeg' && D.buildRequest({}).context === null);

// reply
const good = { kind: 'plant', healthy: false, problem: 'Early blight (Alternaria)', category: 'disease', confidence: 'medium', looks_like: 'Brown rings on lower leaves.', why: 'Target-like rings; could also be nutrient stress.',
  do_now: ['Remove badly spotted leaves', 'Avoid wetting leaves', 'Rotate crops', 'Keep tools clean', 'a fifth step that must be dropped'], chemical_note: 'A protectant fungicide may help; follow the label.', get_help: 'Show a leaf to the KVK.', retake: '' };
const r = D.normalize(good);
check('normalize: a good reply is kept', r && r.problem === 'Early blight (Alternaria)' && r.category === 'disease' && r.confidence === 'medium' && r.healthy === false);
check('normalize: at most 4 steps', r.do_now.length === 4 && !r.do_now.includes('a fifth step that must be dropped'));
const h = D.normalize({ kind: 'plant', healthy: true, confidence: 'high', looks_like: 'Green and even.' });
check('normalize: a healthy plant needs no problem name', h && h.healthy === true && h.problem === '' && h.do_now.length === 0);
const bad = D.normalize({ kind: 'plant', healthy: false, problem: '<img src=x onerror=alert(1)>Rust', category: 'made-up', confidence: 'certain', do_now: 'not a list', looks_like: 5 });
check('normalize: tags are stripped, unknown values fall back to the cautious ones', bad && !/[<>]/.test(bad.problem) && bad.category === 'none' && bad.confidence === 'low' && bad.do_now.length === 0 && bad.looks_like === '');
check('normalize: long text is cut', D.normalize(Object.assign({}, good, { why: 'w'.repeat(5000), problem: 'p'.repeat(900) })).why.length <= 500);
check('normalize: not a plant / unclear photo need a retake hint', D.normalize({ kind: 'not_plant', retake: 'Show a leaf.' }) && D.normalize({ kind: 'unclear_photo', retake: 'Go closer.' }).kind === 'unclear_photo' && D.normalize({ kind: 'not_plant' }) === null);
check('normalize: an empty or non-object reply is refused', D.normalize(null) === null && D.normalize('text') === null && D.normalize([]) === null && D.normalize({}) === null && D.normalize({ kind: 'plant' }) === null);
check('normalize: a non-plant never claims healthy', D.normalize({ kind: 'not_plant', healthy: true, retake: 'x' }).healthy === null);

// treatment look-up
check('researchWanted: only a named problem on a plant', D.researchWanted(r) && !D.researchWanted(h) && !D.researchWanted(D.normalize({ kind: 'not_plant', retake: 'x' })) && !D.researchWanted(null) && !D.researchWanted(Object.assign({}, r, { problem: '' })));
const rq2 = D.buildResearchRequest({ problem: 'Leaf <b>curl</b>\nvirus', crop: 'Cotton', category: 'disease', lang: 'hi', b64: '/9j/AAAA', context: { a: 1 } });
check('buildResearchRequest: only the problem name, crop, kind and language travel (never the photo or field facts)', Object.keys(rq2).sort().join() === 'category,crop,lang,problem,task' && rq2.task === 'research' && !/[<>\n]/.test(rq2.problem) && D.buildResearchRequest({ category: 'x' }).category === 'other');
const rs = D.normalizeResearch({ found: true, summary: 'Managed by controlling whiteflies.', cultural: ['a', 'b', 'c', 'd', 'e'], natural: ['<b>neem</b> oil'], medicines: [{ active_ingredient: 'Imidacloprid', use: 'whitefly', dose_in_source: '0.5 ml/L', wait_note: 'Wait 40 days' }, { use: 'no name' }],
  safety: ['Gloves'], agree: 'medium', sources: [{ url: 'https://icar.org.in/p', title: 'ICAR' }, { url: 'http://insecure.example' }, { url: 'javascript:alert(1)' }, { url: 'https://x.example/"onmouseover="alert(1)' }, { url: 'https://icar.org.in/p' }] });
check('normalizeResearch: keeps good fields, at most 4 steps, strips tags, drops nameless medicines', rs.found && rs.cultural.length === 4 && !/[<>]/.test(rs.natural[0]) && rs.medicines.length === 1 && rs.medicines[0].dose_in_source === '0.5 ml/L' && rs.agree === 'medium');
check('normalizeResearch: links must be https, clean and unique', rs.sources.length === 1 && rs.sources[0].url === 'https://icar.org.in/p');
check('normalizeResearch: nothing useful -> found:false; non-objects refused', D.normalizeResearch({ found: true }).found === false && D.normalizeResearch(null) === null && D.normalizeResearch([]) === null && D.normalizeResearch('x') === null);
check('normalizeResearch: unknown agreement is the cautious one', D.normalizeResearch({ summary: 's', agree: 'certain' }).agree === 'low');

// card colour and labels
check('tone: healthy green, low confidence amber, water/nutrient amber, disease red, not a plant neutral',
  D.tone(h) === 'good' && D.tone(D.normalize(Object.assign({}, good, { confidence: 'low' }))) === 'warn' && D.tone(D.normalize(Object.assign({}, good, { category: 'water' }))) === 'warn' && D.tone(r) === 'bad' && D.tone(D.normalize({ kind: 'not_plant', retake: 'x' })) === 'none');
check('confKey: three labels', D.confKey({ confidence: 'high' }) === 'pcConfHigh' && D.confKey({ confidence: 'medium' }) === 'pcConfMed' && D.confKey({ confidence: 'low' }) === 'pcConfLow' && D.confKey(null) === 'pcConfLow');

// wording exists in every language for every state the card can show
const need = ['pcTitle', 'pcHelp', 'pcTips', 'pcTake', 'pcPick', 'pcNotePh', 'pcCheck', 'pcConsent', 'pcAllow', 'pcCancel', 'pcBusy', 'pcAgain', 'pcCamHint', 'pcShoot', 'pcCamDenied', 'pcCamNone', 'pcCamBusy', 'pcCamFail', 'pcKeyHelp', 'pcKeyPh', 'pcKeySave', 'pcKeyUsing', 'pcKeyRemove', 'pcKeyBad', 'pcKeyRejected', 'pcSearching', 'pcResTitle', 'pcCultural', 'pcNatural', 'pcSpray', 'pcDoseSrc', 'pcWaitLbl', 'pcSafety', 'pcSources', 'pcResFail', 'pcResNone', 'pcMedWarn', 'pcAgreeHigh', 'pcAgreeMed', 'pcAgreeLow', 'pcHealthy', 'pcConfHigh', 'pcConfMed', 'pcConfLow', 'pcLooks', 'pcWhy', 'pcDo', 'pcChem', 'pcHelpHdr', 'pcRetake', 'pcAi', 'pcDisclaimer', 'pcOff', 'pcFail', 'pcBad', 'pcNotPlant', 'pcUnclear']
  .concat(D.CATS.filter(c => c !== 'none').map(c => 'pcCat_' + c));
for (const l of ['en', 'hi', 'gu']) check('wording: every plant-check string (photo and treatment) exists in ' + l, need.every(k => I.dict[l][k] && I.dict[l][k].length > 1));
check('wording: the dose line carries its placeholder in every language and the medicine warning mentions the label', ['en', 'hi', 'gu'].every(l => /\{dose\}/.test(I.dict[l].pcDoseSrc) && I.dict[l].pcMedWarn.length > 60));
check('wording: consent names the service and the disclaimer names the Kisan Call Centre number in all languages', ['en', 'hi', 'gu'].every(l => /\{service\}/.test(I.dict[l].pcConsent) && /1800-180-1551/.test(I.dict[l].pcDisclaimer) && /1800-180-1551/.test(I.dict[l].pcOff)));

// live camera helpers
check('camConstraints: back camera preferred by default, a chosen camera by exact id, no audio', D.camConstraints().video.facingMode.ideal === 'environment' && D.camConstraints('abc').video.deviceId.exact === 'abc' && !('facingMode' in D.camConstraints('abc').video) && D.camConstraints().audio === false && D.camConstraints().video.width.ideal === 1920);
check('camErrorKey: blocked / no camera / busy / anything else', D.camErrorKey({ name: 'NotAllowedError' }) === 'pcCamDenied' && D.camErrorKey({ name: 'SecurityError' }) === 'pcCamDenied' && D.camErrorKey({ name: 'NotFoundError' }) === 'pcCamNone' && D.camErrorKey({ name: 'OverconstrainedError' }) === 'pcCamNone' && D.camErrorKey({ name: 'NotReadableError' }) === 'pcCamBusy' && D.camErrorKey(new Error('x')) === 'pcCamFail' && D.camErrorKey(null) === 'pcCamFail');
// own-key mode: the same Gemini calls, made from the page with the user's own key (mocked endpoint, a made-up key)
check('validKey: long token-like text passes; spaces, quotes, short or empty fail', D.validKey('abcDEF123_-abcDEF123456') && !D.validKey('short') && !D.validKey('has space in it 12345678') && !D.validKey('"quoted_key_value_1234567"') && !D.validKey('') && !D.validKey(null));
(async () => {
  const JPG = '/9j/4AAQSkZJRgAB' + 'AAAA'.repeat(10);
  const calls = [];
  const gem = (text, extra) => async (url, init) => { calls.push({ url: String(url), init }); return new Response(JSON.stringify({ candidates: [Object.assign({ content: { parts: [{ text }], role: 'model' }, finishReason: 'STOP' }, extra || {})] }), { status: 200 }); };
  const body = D.buildRequest({ b64: JPG, mime: 'image/jpeg', lang: 'en', crop: 'Cotton', note: '', context: { decision: 'OK' } });
  const diag = { kind: 'plant', healthy: false, problem: 'Leaf curl', category: 'disease', confidence: 'medium', looks_like: 'Curled leaves.', why: 'Typical of leaf curl.', do_now: ['Remove infected plants'], chemical_note: '', get_help: 'Ask the KVK.', retake: '' };
  let d = await D.direct.diagnose(body, 'test-key-abcdefghij', { fetchFn: gem(JSON.stringify(diag)) });
  const c = calls[0], sent = JSON.parse(c.init.body);
  check('direct diagnose: returns a clean diagnosis', d.problem === 'Leaf curl' && d.confidence === 'medium');
  check('direct diagnose: goes to Google with the key in a header (not the URL or body) and the photo as inline data', /generativelanguage\.googleapis\.com\/v1beta\/models\/[\w.-]+:generateContent$/.test(c.url) && c.init.headers['x-goog-api-key'] === 'test-key-abcdefghij' && !c.url.includes('test-key') && !c.init.body.includes('test-key') && sent.contents[0].parts[0].inlineData.data === JPG);
  const rs = await D.direct.research(D.buildResearchRequest({ problem: 'Leaf curl', crop: 'Cotton', category: 'disease', lang: 'en' }), 'test-key-abcdefghij',
    { fetchFn: gem(JSON.stringify({ found: true, summary: 'Control whiteflies.', cultural: ['Remove plants'], natural: [], medicines: [], safety: [], agree: 'medium' }), { groundingMetadata: { groundingChunks: [{ web: { uri: 'https://icar.example/p', title: 'ICAR' } }] } }) });
  check('direct research: treatment object with the search tool\'s sources, request is text only with Google Search on', rs.found && rs.sources[0].url === 'https://icar.example/p' && JSON.parse(calls[1].init.body).tools[0].google_search && !calls[1].init.body.includes('inlineData'));
  const rejects = async (fn, re) => { try { await fn(); return false; } catch (e) { return re.test(String(e.message)); } };
  check('direct: Google refusing the key (403, or 400 "API key not valid") is reported as "key rejected"',
    await rejects(() => D.direct.diagnose(body, 'bad-key-abcdefghijklmn', { fetchFn: async () => new Response('{}', { status: 403 }) }), /key rejected/) &&
    await rejects(() => D.direct.diagnose(body, 'bad-key-abcdefghijklmn', { fetchFn: async () => new Response('{"error":{"message":"API key not valid. Please pass a valid API key."}}', { status: 400 }) }), /key rejected/));
  check('direct: busy -> "busy", unreadable reply -> error, never an invented diagnosis',
    await rejects(() => D.direct.diagnose(body, 'k-abcdefghijklmnopqrs', { fetchFn: async () => new Response('{}', { status: 429 }) }), /busy/) &&
    await rejects(() => D.direct.diagnose(body, 'k-abcdefghijklmnopqrs', { fetchFn: gem('sorry, no json') }), /unreadable/));
  check('direct: a bad photo or empty problem is refused before anything is sent', await (async () => { const n0 = calls.length; const a = await rejects(() => D.direct.diagnose(Object.assign({}, body, { image: 'nope' }), 'k-abcdefghijklmnopqrs', { fetchFn: gem('{}') }), /bad image/);
    const b = await rejects(() => D.direct.research({ task: 'research', problem: '' }, 'k-abcdefghijklmnopqrs', { fetchFn: gem('{}') }), /problem required/); return a && b && calls.length === n0; })());
  check('direct: no key -> refused, nothing sent', await rejects(() => D.direct.diagnose(body, '', { fetchFn: gem('{}') }), /not configured/));
  console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
})().catch(e => { console.error(e); process.exit(1); });
