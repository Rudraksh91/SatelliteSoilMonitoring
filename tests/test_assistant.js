// Checks for farmer/assistant.js: question understanding in English / Hindi / Gujarati, answers built only from the field data,
// and that what would go to the optional AI service carries no name or location.
const A = require('../farmer/assistant.js'), I = require('../farmer/i18n.js');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
const Tl = lang => (k, p) => I.t(lang, k, p);

// 1. the five quick-question buttons are understood in every language
const expect = { asQ1: 'irrigate', asQ2: 'howMuch', asQ3: 'rain', asQ4: 'ndvi', asQ5: 'whatNdvi' };
for (const lang of ['en', 'hi', 'gu']) {
  const bad = Object.entries(expect).filter(([k, v]) => A.classify(I.t(lang, k)) !== v).map(([k]) => k + '=' + A.classify(I.t(lang, k)));
  check(lang + ': quick questions are understood' + (bad.length ? ' — ' + bad.join(',') : ''), bad.length === 0);
}
// 2. free-typed questions, including Hinglish
const cases = [['should i water the field today', 'irrigate'], ['kitna paani dena hai', 'howMuch'], ['aaj barish hogi kya', 'rain'], ['what is et0', 'whatEt'],
  ['how do i add a field', 'howto'], ['is this accurate', 'trust'], ['thanks', 'thanks'], ['hello', 'greet'], ['what is autopilot', 'auto'],
  ['which pesticide for cotton bollworm', 'kPest'], ['tell me about tractor loans', 'other'], ['', 'other'], ['मिट्टी में कितनी नमी है', 'soil'], ['how much rain tomorrow', 'rain'], ['how much water does the crop need', 'howMuch'], ['how long should i run the pump to irrigate', 'howMuch'], ['ખેતરમાં માટી કેવી છે', 'soil']];
for (const [q, want] of cases) check('classify "' + q + '" -> ' + want, A.classify(q) === want);

// 3. answers come from the data
const adv = { decision: 'IRRIGATE', reasonKey: 'rIrrigate', rp: { dr: '38', raw: '26' }, grossMm: 43.2, litres: 22464, hours: 3.74, daysLeft: 0, dr: 38.1, raw: 26.4, taw: 52.8, pct: 72, needsSetup: false };
const ctx = { today: '2026-11-21', locale: 'en-IN', farms: [{ id: 'a', name: 'North field', ha: 0.52, cropId: 'cotton', last: { date: '2026-11-21', decision: 'IRRIGATE', litres: 22464, needsSetup: false } },
  { id: 'b', name: 'River plot', ha: 1.2, cropId: 'wheat', last: { date: '2026-11-20', decision: 'OK', needsSetup: false } }],
  open: { id: 'a', name: 'North field', ha: 0.52, cropId: 'cotton', adv, rain: [{ date: '2026-11-21', mm: 0 }, { date: '2026-11-22', mm: 4 }, { date: '2026-11-23', mm: 2 }], et0: [4.2, 4.3, 4.1], ndvi: { v: 0.41, date: '2026-11-17' }, flowLph: 6000 } };
const T = Tl('en');
let r = A.reply('Should I irrigate today?', ctx, T);
check('irrigate: says yes with the real litres', r.intent === 'irrigate' && /Yes/.test(r.text) && r.text.includes('22,464') && r.text.includes('43 mm') && /3\.7 hours/.test(r.text));
r = A.reply('How much water?', ctx, T);
check('how much: litres and area from the data', r.text.includes('0.52 ha') && r.text.includes('22,464'));
r = A.reply('Will it rain?', ctx, T);
check('rain: lists the forecast days and the total', /4 mm/.test(r.text) && /6 mm/.test(r.text));
r = A.reply('How is my crop?', ctx, T);
check('crop: NDVI value, date and meaning', r.text.includes('0.41') && r.text.includes('2026-11-17') && /moderate/i.test(r.text));
r = A.reply('how is the soil', ctx, T);
check('soil: percent used from the data', r.text.includes('72%') && r.text.includes('38 mm of 53 mm'));
const ok = JSON.parse(JSON.stringify(ctx)); ok.open.adv = Object.assign({}, adv, { decision: 'OK', reasonKey: 'rOk', daysLeft: 3.4, litres: 0, hours: null });
check('OK: says no irrigation and when to check again', /No irrigation needed/.test(A.reply('irrigate today?', ok, T).text) && /3 days/.test(A.reply('irrigate today?', ok, T).text));
check('OK: how much is 0 litres', /0 litres/.test(A.reply('how much water', ok, T).text));
const wait = JSON.parse(JSON.stringify(ctx)); wait.open.adv = Object.assign({}, adv, { decision: 'WAIT_RAIN', reasonKey: 'rWait', rp: { mm: '14' } });
check('WAIT_RAIN: tells the farmer to wait', /Wait for the rain/.test(A.reply('should i water', wait, T).text) && /14 mm/.test(A.reply('should i water', wait, T).text));
const setup = JSON.parse(JSON.stringify(ctx)); setup.open.adv.needsSetup = true;
check('needs soil answer first: never gives litres', /how the soil/.test(A.reply('how much water', setup, T).text) && !/22,464/.test(A.reply('how much water', setup, T).text));
const closed = JSON.parse(JSON.stringify(ctx)); closed.open = null;
r = A.reply('which field needs water', closed, T);
check('no field open: all-fields summary from each field\'s own result', /North field: Irrigate \(22,464 L\)/.test(r.text) && /River plot: OK/.test(r.text) && /1 of 2 fields/.test(r.text) && /earlier day/.test(r.text));
check('field open but still loading: says advice is not ready, not "open a field"', /do not have today/.test(A.reply('how is my crop', Object.assign({}, closed, { loadingField: true }), T).text));
check('no field open + rain question: asks to open a field', /open one of your fields/i.test(A.reply('will it rain', closed, T).text));
check('no fields at all: asks to add one', /add your field/i.test(A.reply('should i irrigate', { farms: [], open: null }, T).text));
check('unknown topic: polite pointer, no invented advice', /agriculture officer/.test(A.reply('tell me about tractor loans', ctx, T).text) && /1800-180-1551/.test(A.reply('tell me about tractor loans', ctx, T).text) && A.reply('tell me about tractor loans', ctx, T).general === true);
check('missing NDVI: says there is no clear picture, no number', /no clear satellite picture/.test(A.reply('how is my crop', Object.assign({}, ctx, { open: Object.assign({}, ctx.open, { ndvi: null }) }), T).text));

// 3b. general farming questions (not about the farmer's own field) are answered too, in every language
const topics = { kSeason: ['when to sow wheat', 'asKSeason'], kSoil: ['how to improve soil health', 'asKSoil'], kComp: ['how to make compost', 'asKComp'], kFert: ['which fertilizer for wheat', 'asKFert'],
  kWater: ['how to save water', 'asKWater'], kPest: ['my cotton has worms', 'asKPest'], kRot: ['what is crop rotation', 'asKRot'], kSeed: ['which seed is best', 'asKSeed'] };
for (const [intent, [q, key]] of Object.entries(topics)) {
  const r = A.reply(q, ctx, T);
  check('general farming "' + q + '" -> ' + intent + ', answered with no field data needed', r.intent === intent && r.text === I.t('en', key) && r.general === true && r.text.length > 120);
}
for (const lang of ['hi', 'gu']) {
  const miss = Object.values(topics).filter(([, key]) => { const t = A.reply(I.t(lang, 'asQ1'), ctx, Tl(lang)); return I.t(lang, key) === I.t('en', key) || /\{\w+\}/.test(I.t(lang, key)); });
  check(lang + ': every general farming answer is translated', miss.length === 0);
}
check('field questions are not swallowed by the general topics', A.classify('how is my soil') === 'soil' && A.classify('should i irrigate today') === 'irrigate' && A.classify('how much water') === 'howMuch' && A.classify('will it rain') === 'rain');
check('field-data answers are not flagged general (they never go to the AI)', A.reply('should i irrigate today', ctx, T).general === false && A.reply('how much water', ctx, T).general === false);
check('general farming answers still work with no farm at all', /Kharif/.test(A.reply('when to sow wheat', { farms: [], open: null }, T).text));
check('general answers invent no field numbers and give no pesticide doses', Object.values(topics).every(([, key]) => !/\d+\s?(ml|g|kg|litre|L)\/?/i.test(I.t('en', key).replace(/2 to 3 (years|months)/g, ''))));

// 4. other languages give real answers with the same numbers
for (const lang of ['hi', 'gu']) {
  const t = A.reply(I.t(lang, 'asQ2'), ctx, Tl(lang)).text;
  check(lang + ': "how much water" answer has the litres and no placeholder left', t.includes('22,464') && !/\{\w+\}/.test(t));
  check(lang + ': answer is in the language, not English', !/needs about|Apply about/.test(t));
}

// 5. what may go to an AI service
const lc = A.llmContext(ctx), s = JSON.stringify(lc);
check('llmContext: no field name, no coordinates', !/North|River|lat|lng|lon/i.test(s) && !('name' in lc));
check('llmContext: carries the decision and numbers', lc.decision === 'IRRIGATE' && lc.litresToday === 22464 && lc.rainNext3DaysMm.length === 3 && lc.ndvi === 0.41);
check('llmContext: nothing when the field still needs a soil answer', A.llmContext(setup) === null && A.llmContext(closed) === null);

console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
