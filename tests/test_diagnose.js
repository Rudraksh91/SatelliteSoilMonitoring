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

// card colour and labels
check('tone: healthy green, low confidence amber, water/nutrient amber, disease red, not a plant neutral',
  D.tone(h) === 'good' && D.tone(D.normalize(Object.assign({}, good, { confidence: 'low' }))) === 'warn' && D.tone(D.normalize(Object.assign({}, good, { category: 'water' }))) === 'warn' && D.tone(r) === 'bad' && D.tone(D.normalize({ kind: 'not_plant', retake: 'x' })) === 'none');
check('confKey: three labels', D.confKey({ confidence: 'high' }) === 'pcConfHigh' && D.confKey({ confidence: 'medium' }) === 'pcConfMed' && D.confKey({ confidence: 'low' }) === 'pcConfLow' && D.confKey(null) === 'pcConfLow');

// wording exists in every language for every state the card can show
const need = ['pcTitle', 'pcHelp', 'pcTips', 'pcTake', 'pcPick', 'pcNotePh', 'pcCheck', 'pcConsent', 'pcAllow', 'pcCancel', 'pcBusy', 'pcAgain', 'pcHealthy', 'pcConfHigh', 'pcConfMed', 'pcConfLow', 'pcLooks', 'pcWhy', 'pcDo', 'pcChem', 'pcHelpHdr', 'pcRetake', 'pcAi', 'pcDisclaimer', 'pcOff', 'pcFail', 'pcBad', 'pcNotPlant', 'pcUnclear']
  .concat(D.CATS.filter(c => c !== 'none').map(c => 'pcCat_' + c));
for (const l of ['en', 'hi', 'gu']) check('wording: every plant-check string exists in ' + l, need.every(k => I.dict[l][k] && I.dict[l][k].length > 1));
check('wording: consent names the service and the disclaimer names the Kisan Call Centre number in all languages', ['en', 'hi', 'gu'].every(l => /\{service\}/.test(I.dict[l].pcConsent) && /1800-180-1551/.test(I.dict[l].pcDisclaimer) && /1800-180-1551/.test(I.dict[l].pcOff)));

console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
