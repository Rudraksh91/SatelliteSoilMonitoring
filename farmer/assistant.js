/* AgriSat farmer assistant — answers a farmer's questions from THEIR OWN field data (the advice the app already computed:
 * decision, litres, soil water, rain forecast, NDVI). Works in the browser with no server and no key.
 * Two kinds of answers:
 *   - about THE FARMER'S OWN FIELD: built only from the context passed in (nothing is invented; every figure comes from it);
 *   - GENERAL FARMING (sowing seasons, soil health, fertiliser, saving water, pests, rotation, seeds, compost): short, well-known
 *     guidance with no made-up numbers, so common questions get an answer even with no server.
 * Everything else, and every general question when an AI service is configured (see chat-proxy), is flagged `general: true` so
 * the page can send it to the AI, which can answer any agriculture question; without it, a polite pointer is shown.
 *
 *   AgriAssistant.reply(question, ctx, T)  ->  { intent, text }
 *   AgriAssistant.classify(question)       ->  intent id
 *   AgriAssistant.llmContext(ctx)          ->  compact summary for the optional AI service (no name, no location)
 *
 * T(key, params) is the page's translator (farmer/i18n.js). ctx = { today, locale, farms:[{id,name,ha,cropId,last}], open:{...}|null }
 */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriAssistant = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const norm = s => String(s || '').toLowerCase().replace(/[?!.,;:()"'“”‘’।|]/g, ' ').replace(/\s+/g, ' ').trim();
  // Latin keywords match at the start of a word; Indic-script keywords match anywhere (their word breaks differ)
  const has = (t, list) => list.some(k => /^[a-z0-9 ]+$/.test(k) ? new RegExp('(^|[^a-z0-9])' + k).test(t) : t.includes(k));

  const KW = {
    thanks: ['thank', 'dhanyavad', 'shukriya', 'धन्यवाद', 'शुक्रिया', 'आभार', 'આભાર', 'ધન્યવાદ'],
    greet: ['hi', 'hello', 'hey', 'good morning', 'namaste', 'namaskar', 'ram ram', 'नमस्ते', 'नमस्कार', 'राम राम', 'નમસ્તે', 'કેમ છો', 'જય'],
    what: ['what is', 'what are', 'what does', 'explain', 'meaning', 'kya hai', 'kya hota', 'क्या है', 'क्या होता', 'मतलब', 'अर्थ', 'શું છે', 'શું હોય', 'મતલબ', 'અર્થ'],
    tNdvi: ['ndvi', 'एनडीवीआई', 'એનડીવીઆઈ'],
    tEt: ['et0', 'et 0', 'evapotranspiration', 'evaporation', 'वाष्पीकरण', 'वाष्पोत्सर्जन', 'બાષ્પીભવન'],
    tBal: ['balance', 'depletion', 'fao', 'water account', 'बैलेंस', 'संतुलन', 'સંતુલન'],
    howQty: ['how much', 'how many', 'kitna', 'kitne', 'kitni', 'कितना', 'कितने', 'कितनी', 'કેટલું', 'કેટલા', 'કેટલી'],
    howUnit: ['litre', 'liter', 'hours', 'how long', 'volume', 'ghante', 'लीटर', 'घंटे', 'લિટર', 'કલાક'],
    rain: ['rain', 'shower', 'forecast', 'storm', 'barish', 'baarish', 'barsat', 'varsha', 'बारिश', 'बरसात', 'वर्षा', 'વરસાદ', 'માવઠ'],
    soil: ['soil', 'moisture', 'dry', 'wet', 'mitti', 'nami', 'मिट्टी', 'नमी', 'માટી', 'જમીન', 'ભેજ'],
    crop: ['crop', 'ndvi', 'green', 'health', 'fasal', 'hariyali', 'फसल', 'हरियाली', 'પાક', 'લીલ', 'હરિયાળી'],
    auto: ['autopilot', 'auto pilot', 'automatic', 'schedule', 'ऑटोपायलट', 'ऑटो', 'ઓટોપાયલટ', 'ઓટો'],
    trust: ['accurate', 'trust', 'reliable', 'correct', 'sure', 'estimate', 'sahi', 'bharosa', 'sateek', 'सही', 'भरोसा', 'सटीक', 'ચોક્કસ', 'ભરોસો', 'સાચ'],
    howto: ['how to', 'how do i', 'add field', 'add a field', 'start', 'use this', 'register', 'kaise', 'shuru', 'कैसे', 'शुरू', 'जोड़', 'કેવી રીતે', 'શરૂ', 'ઉમેર'],
    // general farming topics (checked before the field-data topics; specific phrases so "how is my soil" still means the field)
    kSeason: ['when to sow', 'sowing time', 'sowing season', 'when to plant', 'best time to plant', 'kharif', 'rabi', 'zaid', 'which season', 'kab boye', 'कब बोएँ', 'कब बोयें', 'बुवाई का समय', 'बोने का समय', 'खरीफ', 'रबी', 'જાયદ', 'ક્યારે વાવ', 'વાવણીનો સમય', 'ખરીફ', 'રવિ પાક', 'રવી'],
    kSoil: ['soil health', 'soil test', 'soil fertility', 'improve soil', 'improve my soil', 'health card', 'मिट्टी की जाँच', 'मिट्टी की सेहत', 'मिट्टी सुधार', 'उपजाऊ', 'मृदा', 'માટીની તપાસ', 'માટીનું સ્વાસ્થ્ય', 'ફળદ્રુપ', 'માટી સુધાર', 'સોઇલ હેલ્થ'],
    kFert: ['fertili', 'manure', 'npk', 'urea', 'dap', 'khad', 'खाद', 'उर्वरक', 'यूरिया', 'ખાતર', 'યુરિયા'],
    kWater: ['save water', 'saving water', 'water saving', 'drip', 'mulch', 'irrigation method', 'how to irrigate', 'pani bacha', 'paani bacha', 'पानी बचा', 'पानी की बचत', 'ड्रिप', 'मल्च', 'પાણી બચાવ', 'પાણીની બચત', 'ટપક', 'મલ્ચ'],
    kPest: ['pest', 'disease', 'insect', 'worm', 'weed', 'fungus', 'blight', 'spray', 'keet', 'कीट', 'रोग', 'इल्ली', 'खरपतवार', 'छिड़काव', 'દવા', 'જીવાત', 'રોગ', 'ઇયળ', 'નીંદણ', 'છંટકાવ', 'दवा'],
    kRot: ['crop rotation', 'rotate', 'intercrop', 'mixed crop', 'फसल चक्र', 'अंतरवर्ती', 'પાક ફેરબદલી', 'આંતરપાક'],
    kSeed: ['seed', 'germination', 'certified', 'बीज', 'अंकुरण', 'બિયારણ', 'બીજ', 'ઉગાવ'],
    kComp: ['compost', 'vermicompost', 'organic', 'green manure', 'gobar', 'कम्पोस्ट', 'जैविक', 'गोबर', 'वर्मी', 'हरी खाद', 'કમ્પોસ્ટ', 'જૈવિક', 'છાણ', 'અળસિયા'],
    irrigate: ['irrigat', 'water', 'paani', 'pani', 'sinchai', 'patwan', 'सिंचाई', 'पानी', 'पटवन', 'પિયત', 'સિંચાઈ', 'પાણી'],
  };

  function classify(question) {
    const t = norm(question);
    if (!t) return 'other';
    if (has(t, KW.thanks)) return 'thanks';
    if (t.split(' ').length <= 3 && has(t, KW.greet)) return 'greet';
    if (has(t, KW.what)) {
      if (has(t, KW.tNdvi)) return 'whatNdvi';
      if (has(t, KW.tEt)) return 'whatEt';
      if (has(t, KW.tBal)) return 'whatBalance';
    }
    for (const k of KB_ORDER) if (has(t, KW[k])) return k;                      // general farming topics
    if (has(t, KW.rain)) return 'rain';
    const qty = has(t, KW.howQty), unit = has(t, KW.howUnit);
    if ((qty || unit) && has(t, KW.irrigate)) return 'howMuch';   // "how much water", "kitna paani", "how long to irrigate"
    if (unit) return 'howMuch';
    if (has(t, KW.crop)) return 'ndvi';
    if (has(t, KW.soil)) return 'soil';
    if (qty) return 'howMuch';
    if (has(t, KW.auto)) return 'auto';
    if (has(t, KW.trust)) return 'trust';
    if (has(t, KW.howto)) return 'howto';
    if (has(t, KW.irrigate)) return 'irrigate';
    return 'other';
  }

  // general-farming intents -> the i18n key holding their answer
  const KB = { kSeason: 'asKSeason', kSoil: 'asKSoil', kComp: 'asKComp', kFert: 'asKFert', kWater: 'asKWater', kPest: 'asKPest', kRot: 'asKRot', kSeed: 'asKSeed' };   // order matters: manure/compost before fertiliser
  const KB_ORDER = Object.keys(KB);
  const GENERAL = new Set(['other'].concat(KB_ORDER));

  const fmt = (n, d) => Number(n).toFixed(d || 0);
  const lit = n => Number(Math.round(n)).toLocaleString('en-IN');

  function timeText(T, hours) {
    return hours < 1 ? T('tMin', { n: fmt(hours * 60) }) : T('tHour', { n: fmt(hours, 1) });
  }

  // ── answers about the field the farmer has open (full advice available) ──
  function openState(o, T) {            // returns an error text when the open field cannot be answered about yet
    if (!o || !o.adv) return T('asNoData', { name: o ? o.name : '' });
    if (o.adv.needsSetup) return T('asNeedSoil', { name: o.name });
    return null;
  }
  function decisionText(o, T) {
    const a = o.adv, reason = T(a.reasonKey, a.rp);
    if (a.decision === 'IRRIGATE') {
      let s = T('asIrr', { name: o.name, reason, mm: fmt(a.grossMm), litres: lit(a.litres) });
      if (a.hours) s += ' ' + T('asIrrHours', { time: timeText(T, a.hours) });
      return s;
    }
    if (a.decision === 'WAIT_RAIN') return T('asWait', { name: o.name, reason });
    return T('asOk', { name: o.name, reason }) + ' ' + (a.daysLeft < 1 ? T('asOkTomorrow') : T('asOkDays', { n: fmt(a.daysLeft) }));
  }

  // ── all fields at a glance (only the short summary stored on each field is available) ──
  function multiText(ctx, T) {
    const withLast = ctx.farms.filter(f => f.last);
    if (!withLast.length) return T('asNoData', { name: ctx.farms[0] ? ctx.farms[0].name : '' });
    const stat = { IRRIGATE: 'mIrrigate', OK: 'mOk', WAIT_RAIN: 'mWait' };
    const lines = withLast.map(f => {
      const l = f.last, st = l.needsSetup ? T('mSetup') : T(stat[l.decision] || 'mOk');
      return '• ' + f.name + ': ' + st + (l.decision === 'IRRIGATE' && !l.needsSetup && l.litres ? ' (' + lit(l.litres) + ' L)' : '');
    });
    const need = withLast.filter(f => f.last.decision === 'IRRIGATE' && !f.last.needsSetup);
    let s = T('asMultiHead') + '\n' + lines.join('\n') + '\n' + (need.length ? T('asMultiNeed', { n: need.length, m: withLast.length }) : T('totNone'));
    if (withLast.some(f => f.last.date !== ctx.today)) s += '\n' + T('asMultiStale');
    return s;
  }

  function rainText(o, ctx, T) {
    const r = (o.rain || []).slice(0, 3);
    if (!r.length) return T('asNoData', { name: o.name });
    const total = r.reduce((s, d) => s + (d.mm || 0), 0);
    if (total < 1) return T('asRainNone', { name: o.name });
    const list = r.map(d => new Date(d.date + 'T00:00:00').toLocaleDateString(ctx.locale || 'en-IN', { weekday: 'short' }) + ' ' + fmt(d.mm, 0) + ' mm').join(' · ');
    return T('asRain', { name: o.name, list, mm: fmt(total, 0) });
  }

  function ndviText(o, T) {
    const n = o.ndvi;
    if (!n || typeof n.v !== 'number') return T('asNdviNone', { name: o.name });
    const meaning = n.v < 0.25 ? T('asNdviLow') : n.v < 0.5 ? T('asNdviMid') : T('asNdviHigh');
    return T('asNdvi', { name: o.name, v: fmt(n.v, 2), date: n.date || '—', meaning });
  }

  function reply(question, ctx, T) {
    ctx = ctx || { farms: [], open: null };
    const intent = classify(question);
    const o = ctx.open, hasFarm = (ctx.farms || []).length > 0;
    let text;
    switch (intent) {
      case 'thanks': text = T('asThanks'); break;
      case 'greet': text = T('asHello'); break;
      case 'whatNdvi': text = T('asWhatNdvi'); break;
      case 'whatEt': text = T('asWhatEt'); break;
      case 'whatBalance': text = T('asWhatBalance'); break;
      case 'auto': text = T('asAuto'); break;
      case 'trust': text = T('asTrust'); break;
      case 'howto': text = T('asHowTo'); break;
      case 'irrigate':
      case 'howMuch':
      case 'rain':
      case 'soil':
      case 'ndvi': {
        if (!hasFarm) { text = T('asNoFarm'); break; }
        if (!o) {                       // no field open: only the all-fields summary can be given for irrigation questions
          if (ctx.loadingField) { text = T('asNoData'); break; }   // a field is open but its advice has not finished loading
          text = (intent === 'irrigate' || intent === 'howMuch') ? multiText(ctx, T) : T('asNeedOpen');
          break;
        }
        const err = openState(o, T);
        if (err) { text = err; break; }
        const a = o.adv;
        if (intent === 'irrigate') text = decisionText(o, T);
        else if (intent === 'howMuch') {
          if (a.decision === 'IRRIGATE') {
            text = T('asHow', { name: o.name, ha: fmt(o.ha, 2), mm: fmt(a.grossMm), litres: lit(a.litres) }) + (a.hours ? ' ' + T('asIrrHours', { time: timeText(T, a.hours) }) : '');
          } else text = T('asHowNone', { name: o.name });
        } else if (intent === 'rain') text = rainText(o, ctx, T);
        else if (intent === 'soil') text = T('asSoil', { name: o.name, pct: fmt(a.pct), dr: fmt(a.dr), taw: fmt(a.taw), raw: fmt(a.raw) });
        else text = ndviText(o, T);
        break;
      }
      default: text = KB[intent] ? T(KB[intent]) : T('asOther');
    }
    return { intent, text, general: GENERAL.has(intent) };
  }

  // Compact, anonymous summary for the optional AI service: no field name, no coordinates, no account details.
  function llmContext(ctx) {
    const o = ctx && ctx.open;
    if (!o || !o.adv || o.adv.needsSetup) return null;
    const a = o.adv;
    const r = n => (typeof n === 'number' && isFinite(n)) ? Math.round(n * 10) / 10 : null;
    return {
      crop: o.cropId || 'general', areaHa: r(o.ha), decision: a.decision,
      litresToday: a.decision === 'IRRIGATE' ? Math.round(a.litres) : 0, hoursToday: r(a.hours),
      soilWaterUsedMm: r(a.dr), comfortableLimitMm: r(a.raw), totalAvailableMm: r(a.taw), percentUsed: r(a.pct),
      rainNext3DaysMm: (o.rain || []).slice(0, 3).map(d => r(d.mm)), et0Next3DaysMm: (o.et0 || []).slice(0, 3).map(r),
      ndvi: o.ndvi && typeof o.ndvi.v === 'number' ? r(o.ndvi.v * 100) / 100 : null, ndviDate: o.ndvi ? o.ndvi.date || null : null,
    };
  }

  return { classify, reply, llmContext, KW };
});
