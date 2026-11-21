// Tests for farmer/autopilot.js (node tests/test_autopilot.js)
const C = require('../farmer/core.js'), A = require('../farmer/autopilot.js');
let n = 0, fail = 0; const near = (a, b, t = 1e-6) => Math.abs(a - b) < t;
const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
const today = '2026-11-20';
const days = [...Array(24)].map((_, i) => C.addDays('2026-11-14', i));            // 11-14 .. 11-25
const wx = (et0, rain) => ({ time: days, et0: days.map(() => et0), rain: days.map((d, i) => (rain ? rain(d, i) : 0)) });
const soil = { theta_fc: 0.30, theta_wp: 0.15 };                                    // 150 mm/m
function field(id, o) {
  o = Object.assign({ drFrac: 0.8, crop: 'general', area: 10000, flow: 5000, w: wx(5) }, o || {});
  const crop = C.cropParams(o.crop);
  const adv = C.advise({ today, weather: o.w, soil, ndvi: o.ndvi || [{ date: '2026-11-18', ndvi: 0.5 }], crop, start: { date: today, fracRaw: o.drFrac }, areaM2: o.area, flowLph: o.flow });
  return { id, name: 'F' + id, areaM2: o.area, flowLph: o.flow, zone: +id, adv, ndvi: o.ndvi || [{ date: '2026-11-18', ndvi: 0.5 }], et0Today: o.w.et0[days.indexOf(today)], rain14: 0 };
}
// ── decisions ──
const dry = field('1', { drFrac: 1.2 }), wet = field('2', { drFrac: 0.3 });
let p = A.plan({ today, fields: [dry, wet], settings: {} });
check('dry field irrigates, wet field does not', p.items.find(i => i.id === '1').decision === 'IRRIGATE' && p.items.find(i => i.id === '2').decision === 'NO_RUN');
const it1 = p.items.find(i => i.id === '1');
check('run time = 60 x (Dr/0.88 x area) / flow, within limits', it1.minutes > 0 && it1.minutes <= 120 && near(it1.litres, it1.minutes / 60 * 5000, 1) );
check('first run starts at 06:00 and ends after its minutes', it1.start === '06:00' && it1.endMin - it1.startMin === it1.minutes);
// ── ordering and one-at-a-time ──
const f3 = field('3', { drFrac: 1.6 }), f4 = field('4', { drFrac: 1.1 });
p = A.plan({ today, fields: [f4, f3], settings: { maxRunMin: 40 } });
check('thirstiest field first', p.schedule[0].id === '3' && p.schedule[1].id === '4');
check('fields never overlap and keep the 2-minute gap', p.schedule[1].startMin === p.schedule[0].endMin + 2);
// ── daily cap ──
p = A.plan({ today, fields: [f3, f4, dry], settings: { maxRunMin: 100, maxDailyMin: 150 } });
check('daily total capped at 150 min, later field limited/dropped', p.totalMinutes <= 150 && p.items.some(i => i.decision === 'DAILY_LIMIT' || i.capped));
// ── rules ──
const hotW = wx(7.5), hot = field('5', { drFrac: 0.9, w: hotW }), base = field('6', { drFrac: 0.9 });
check('0.9 x RAW: no irrigation on a normal day', A.plan({ today, fields: [base], settings: {} }).items[0].decision === 'NO_RUN');
p = A.plan({ today, fields: [hot], settings: {} });
check('same soil on a hot day (ET0 7.5): heat compensation irrigates earlier', p.items[0].decision === 'IRRIGATE' && p.items[0].why === 'heat');
check('heat compensation can be switched off', A.plan({ today, fields: [hot], settings: { et0Comp: false } }).items[0].decision === 'NO_RUN');
const strNdvi = [{ date: '2026-11-04', ndvi: 0.62 }, { date: '2026-11-11', ndvi: 0.52 }, { date: '2026-11-18', ndvi: 0.43 }];
const stressed = field('7', { drFrac: 0.8, ndvi: strNdvi });
check('NDVI stress flag recognised (3 falling scenes)', A.ndviStress(strNdvi, 5) && !A.ndviStress(strNdvi, 80) && !A.ndviStress([{ date: 'a', ndvi: .5 }, { date: 'b', ndvi: .49 }, { date: 'c', ndvi: .48 }], 5));
p = A.plan({ today, fields: [stressed], settings: {} });
check('NDVI falling + soil 80 % of RAW: irrigate (NDVI boost)', p.items[0].decision === 'IRRIGATE' && p.items[0].why === 'stress');
check('NDVI boost can be switched off', A.plan({ today, fields: [stressed], settings: { ndviBoost: false } }).items[0].decision === 'NO_RUN');
const rainW = wx(5, (d) => (d === C.addDays(today, 1) ? 25 : 0)), rainy = field('8', { drFrac: 1.2, w: rainW });
check('25 mm forecast tomorrow: skip (rain guard)', A.plan({ today, fields: [rainy], settings: {} }).items[0].decision === 'SKIP_RAIN');
check('rain guard off: irrigates anyway', A.plan({ today, fields: [rainy], settings: { rainGuard: false } }).items[0].decision === 'IRRIGATE');
check('no pump flow known: says NEED_FLOW with litres, not a made-up time', (() => { const q = A.plan({ today, fields: [field('9', { drFrac: 1.2, flow: null })], settings: {} }).items[0]; return q.decision === 'NEED_FLOW' && q.litres > 0 && q.minutes === 0; })());
check('soil not set up yet -> SETUP, never a guessed run', A.plan({ today, fields: [{ id: 'x', name: 'x', adv: { needsSetup: true } }], settings: {} }).items[0].decision === 'SETUP');
check('tiny need (< minimum run) is carried over', A.plan({ today, fields: [field('10', { drFrac: 1.0, flow: 4000000 })], settings: {} }).items[0].decision === 'NO_RUN');
// ── outlook ──
const ow = wx(6), of = field('11', { drFrac: 0.5, w: ow });
const out = A.outlook(of, ow, {}, today, 7);
check('outlook covers 7 days', out.length === 7);
check('dry week: irrigation is scheduled every few days (not daily, not never)', out.filter(d => d.irrigate).length >= 1 && out.filter(d => d.irrigate).length <= 4);
const rw = wx(6, (d) => (d === C.addDays(today, 2) ? 30 : 0)), orf = field('12', { drFrac: 1.1, w: rw });
const o2 = A.outlook(orf, rw, {}, today, 6);
check('the day before a big forecast rain is skipped (rain guard)', o2[1].skipRain && !o2[1].irrigate);
check('after the rain no irrigation is needed for a while', o2.slice(3, 5).every(d => !d.irrigate));
check('outlook day 0 agrees with today\'s plan', out[0].irrigate === (A.plan({ today, fields: [of], settings: {} }).items[0].decision === 'IRRIGATE'));
// ── multi-day schedule the Pi runs ──
{
  const fa = field('1', { drFrac: 1.3, w: ow }), fb = field('2', { drFrac: 1.1, w: ow }), fc = Object.assign(field('3', { drFrac: 1.05, w: ow }), { zone: null });
  const fields = [fa, fb, fc], outl = {}; fields.forEach(f => { outl[f.id] = A.outlook(f, ow, {}, today, 7); });
  const pl = A.plan({ today, fields, settings: { maxRunMin: 60 } });
  const sc = A.buildSchedule(pl, fields, outl, null);
  check('schedule has 7 consecutive days starting today', Object.keys(sc.days).length === 7 && sc.days[today] && sc.days[C.addDays(today, 6)]);
  check('today\'s runs keep the plan\'s order and start times', sc.days[today].length === 2 && sc.days[today][0].start === '06:00' && sc.days[today][0].zone === 1);
  check('a field without a valve is left out and reported', !sc.days[today].some(r => r.zone === 3) && sc.unassigned.length === 1 && sc.unassigned[0] === 'F3');
  check('runs on later days are sequential and within the daily limit', Object.values(sc.days).every(d => d.reduce((a, r) => a + r.minutes, 0) <= 240));
  const late = A.buildSchedule(pl, fields, outl, 14 * 60 + 2);
  check('planned time already past: first run moves to a few minutes from now, not the past', late.days[today][0].start === '14:10' && late.days[today][1].start > late.days[today][0].start);
  const ev = A.buildSchedule(pl, fields, outl, 23 * 60 + 50);
  check('a run that would pass midnight is left for tomorrow\'s plan', ev.days[today].length === 0);
}
// ── multi-index: NDRE when NDVI saturates, NDMI anomaly flags ──
{
  const sc = (arr) => arr.map((o, i) => Object.assign({ date: C.addDays('2026-11-05', i * 5) }, o));
  const dense = sc([{ ndvi: 0.86, ndre: 0.52 }, { ndvi: 0.86, ndre: 0.46 }, { ndvi: 0.87, ndre: 0.40 }]);    // NDVI flat (saturated), NDRE falling
  check('dense canopy: NDVI flat but NDRE falling -> stress is seen through NDRE', A.stressCheck(dense, 0).stress === true && A.stressCheck(dense, 0).index === 'ndre');
  check('the same scenes judged by NDVI alone would have missed it', A.ndviStress(dense, 0) === false);
  const young = sc([{ ndvi: 0.60, ndre: 0.30 }, { ndvi: 0.52, ndre: 0.31 }, { ndvi: 0.44, ndre: 0.30 }]);
  check('young canopy: NDVI is still the index and its fall is stress', A.stressCheck(young, 0).index === 'ndvi' && A.stressCheck(young, 0).stress === true);
  check('dense canopy but too few NDRE scenes: falls back to NDVI', A.stressCheck(sc([{ ndvi: 0.86 }, { ndvi: 0.86 }, { ndvi: 0.87, ndre: 0.4 }]), 0).index === 'ndvi');
  const wetMon = sc([{ ndvi: 0.86, ndre: 0.52 }, { ndvi: 0.86, ndre: 0.46 }, { ndvi: 0.87, ndre: 0.40 }]);
  check('big recent rain cancels the stress reading', A.stressCheck(wetMon, 40).stress === false);
  const f1 = field('21', { drFrac: 0.5 }); f1.ndvi = sc([{ ndvi: .6, ndmi: 0.30 }, { ndvi: .6, ndmi: 0.31 }, { ndvi: .6, ndmi: 0.18 }]);
  let p1 = A.plan({ today, fields: [f1], settings: {} }).items[0];
  check('NDMI drop with water available (Dr < RAW) -> flag "check the field"', p1.flags.length === 1 && p1.flags[0].key === 'ndmiDrop' && p1.flags[0].drop > 0.1);
  check('the flag never changes the irrigation decision (NDMI is a check, not a trigger)', p1.decision === A.plan({ today, fields: [Object.assign({}, f1, { ndvi: sc([{ ndvi: .6 }, { ndvi: .6 }, { ndvi: .6 }]) })], settings: {} }).items[0].decision);
  const f2 = field('22', { drFrac: 1.2 }); f2.ndvi = f1.ndvi;
  check('no flag when the model already says the soil is dry (nothing to doubt)', A.plan({ today, fields: [f2], settings: {} }).items[0].flags.length === 0);
  const mk3 = (vals) => vals.map((v, i) => { const f = field('3' + i, { drFrac: 0.5 }); f.crop = 'wheat'; f.ndvi = sc([{ ndvi: .6, ndmi: v }]); return f; });
  const peers = A.plan({ today, fields: mk3([0.30, 0.31, 0.12]), settings: {} }).items;
  check('three fields, same crop: the one far below the others is flagged', peers[2].flags.some(x => x.key === 'ndmiPeer') && !peers[0].flags.some(x => x.key === 'ndmiPeer'));
  check('only two fields: no neighbour comparison is made', !A.plan({ today, fields: mk3([0.30, 0.12]), settings: {} }).items.some(i => i.flags.some(x => x.key === 'ndmiPeer')));
  check('plan items carry the latest NDMI and NDRE for display', (() => { const f = field('23', { drFrac: .5 }); f.ndvi = sc([{ ndvi: .6, ndmi: .25, ndre: .4 }]); const it = A.plan({ today, fields: [f], settings: {} }).items[0]; return it.ndmi === .25 && it.ndre === .4; })());
}
console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
