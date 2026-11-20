/* AgriSat autopilot — turns NDVI + weather into an irrigation SCHEDULE for every field of a farmer:
 *   plan()    today's runs: which fields, in what order, start time, minutes, litres, and why
 *   outlook() the next 7 days per field, simulating the soil-water balance forward with the weather forecast
 * Pure functions (browser + Node tests). Uses the same FAO-56 model as farmer/core.js and scripts/agrisat_model.py. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory(require('./core.js'));
  else root.AgriAutopilot = factory(root.AgriCore);
})(typeof self !== 'undefined' ? self : this, function (C) {
  'use strict';
  const DEFAULTS = {
    enabled: false,
    startHour: 6, startMinute: 0,       // first run starts here (early morning: less evaporation and wind loss)
    gapMin: 2,                          // pause between fields so only ONE valve/pump zone runs at a time
    rainGuard: true, rainSkipMm: 10,    // skip when more than this is forecast in the next 48 h
    et0Comp: true, heatEt0: 6.5,        // hot, dry-air day (ET0 >= this mm/day): irrigate a bit earlier (at 85 % of the comfortable limit)
    ndviBoost: true,                    // NDVI falling for 3 clear scenes while soil is >70 % depleted: irrigate
    efficiency: 0.88, minRunMin: 10, maxRunMin: 120, maxDailyMin: 240
  };
  const cfg = s => Object.assign({}, DEFAULTS, s || {});

  /** NDVI stress: fell across the last `scenes` clean scenes, each fall > noise, little rain, not senescent. Series newest-first or any order. */
  function ndviStress(ndviScenes, recentRainMm, noise, scenes) {
    noise = noise == null ? 0.03 : noise; scenes = scenes || 3;
    if (recentRainMm > 20) return false;
    const v = (ndviScenes || []).filter(s => s.ndvi != null).slice().sort((a, b) => (a.date < b.date ? -1 : 1)).slice(-scenes).map(s => s.ndvi);
    if (v.length < scenes) return false;
    for (let i = 1; i < v.length; i++) if (!((v[i - 1] - v[i]) > noise)) return false;
    return true;
  }

  function decideDay(dr, taw, raw, fwdRain, et0, stress, S, hotKey) {
    let frac = 1.0, why = 'base';
    if (S.et0Comp && et0 >= S.heatEt0) { frac = 0.85; why = 'heat'; }
    if (S.ndviBoost && stress) { frac = Math.min(frac, 0.70); why = 'stress'; }
    if (S.rainGuard && fwdRain > S.rainSkipMm && dr < 0.9 * taw) return { go: false, key: 'SKIP_RAIN', why: 'rain' };
    if (dr >= frac * raw) return { go: true, key: 'IRRIGATE', why };
    return { go: false, key: 'NO_RUN', why: 'ample' };
  }
  const minutesFor = (dr, area, flow, S) => {
    if (!flow || !area) return null;
    const litres = dr / S.efficiency * area, m = 60 * litres / flow;
    return { litres, raw: m };
  };
  const hhmm = m => String(Math.floor(m / 60) % 24).padStart(2, '0') + ':' + String(Math.round(m % 60)).padStart(2, '0');

  /** input: { today, now?:minutesSinceMidnightIST, settings, fields:[{id,name,areaM2,flowLph,zone?,adv,ndvi:[scenes],et0Today,rain14}] } */
  function plan(input) {
    const S = cfg(input.settings), items = [];
    input.fields.forEach(f => {
      const a = f.adv;
      if (!a || a.needsSetup) { items.push({ id: f.id, name: f.name, zone: f.zone, decision: 'SETUP', reasonKey: 'apSetup', minutes: 0, litres: 0 }); return; }
      const stress = ndviStress(f.ndvi, f.rain14 || 0);
      const d = decideDay(a.dr, a.taw, a.raw, a.forecastRain2d, f.et0Today || 0, stress, S);
      const it = { id: f.id, name: f.name, zone: f.zone, decision: d.key, why: d.why, dr: a.dr, raw: a.raw, taw: a.taw, pct: a.pct, kc: a.kc, ndvi: a.ndvi, stress, minutes: 0, litres: 0, urgency: a.dr / a.raw };
      it.reasonKey = d.key === 'SKIP_RAIN' ? 'apRain' : d.key === 'IRRIGATE' ? (d.why === 'stress' ? 'apStress' : d.why === 'heat' ? 'apHeat' : 'apDry') : 'apAmple';
      it.rp = { dr: Math.round(a.dr), raw: Math.round(a.raw), mm: Math.round(a.forecastRain2d), et0: (f.et0Today || 0).toFixed(1), days: a.daysLeft != null ? Math.round(a.daysLeft) : 0 };
      if (d.go) {
        const m = minutesFor(a.dr, f.areaM2, f.flowLph, S);
        if (!m) { it.decision = 'NEED_FLOW'; it.reasonKey = 'apNeedFlow'; it.litres = a.dr / S.efficiency * (f.areaM2 || 0); }
        else if (m.raw < S.minRunMin) { it.decision = 'NO_RUN'; it.reasonKey = 'apTiny'; it.litres = 0; }
        else { it.minutes = Math.min(Math.round(m.raw), S.maxRunMin); it.litres = it.minutes / 60 * f.flowLph; it.capped = m.raw > S.maxRunMin; }
      }
      items.push(it);
    });
    // order: thirstiest first; one at a time; honour the daily total
    const runs = items.filter(i => i.minutes > 0).sort((p, q) => q.urgency - p.urgency);
    let cursor = S.startHour * 60 + S.startMinute, used = 0;
    runs.forEach(r => {
      const room = S.maxDailyMin - used;
      if (room < S.minRunMin) { r.minutes = 0; r.decision = 'DAILY_LIMIT'; r.reasonKey = 'apDaily'; r.litres = 0; return; }
      if (r.minutes > room) { r.minutes = room; r.capped = true; r.litres = r.minutes / 60 * (input.fields.find(f => f.id === r.id).flowLph); }
      r.startMin = cursor; r.endMin = cursor + r.minutes; r.start = hhmm(r.startMin); r.end = hhmm(r.endMin);
      cursor = r.endMin + S.gapMin; used += r.minutes;
    });
    const sched = items.filter(i => i.minutes > 0).sort((a, b) => a.startMin - b.startMin);
    return { date: input.today, settings: S, items, schedule: sched, totalMinutes: used, totalLitres: sched.reduce((a, i) => a + i.litres, 0),
             firstStart: sched.length ? sched[0].start : null, lastEnd: sched.length ? sched[sched.length - 1].end : null };
  }

  /** Next `days` days for one field: irrigate when the rules say so, refill to field capacity, then step the balance with the forecast. */
  function outlook(f, weather, settings, today, days) {
    const S = cfg(settings), a = f.adv; days = days || 7;
    if (!a || a.needsSetup) return [];
    const idx = {}; weather.time.forEach((t, i) => { idx[t] = i; });
    const kc = a.kc != null ? a.kc : 0.6, stress0 = ndviStress(f.ndvi, f.rain14 || 0);
    let dr = a.dr; const out = [];
    for (let d = 0; d < days; d++) {
      const date = C.addDays(today, d), i = idx[date]; if (i === undefined) break;
      const rain = weather.rain[i] || 0, et0 = weather.et0[i] || 0;
      const fwd = (weather.rain[i] || 0) + (weather.rain[i + 1] || 0);
      const dec = decideDay(dr, a.taw, a.raw, fwd, et0, d === 0 ? stress0 : false, S);
      let minutes = 0, mm = 0;
      if (dec.go) {
        const m = minutesFor(dr, f.areaM2, f.flowLph, S);
        if (m && m.raw >= S.minRunMin) { minutes = Math.min(Math.round(m.raw), S.maxRunMin); mm = (minutes / 60 * f.flowLph) / f.areaM2 * S.efficiency; dr = Math.max(0, dr - mm); }
        else if (!m) { mm = dr; dr = 0; }   // no flow known: still show the day water is needed
        else dec.go = false;
      }
      out.push({ date, irrigate: dec.go, minutes, mm, rain, et0, drStart: dr, skipRain: dec.key === 'SKIP_RAIN' });
      dr = C.step(dr, et0, kc, rain, 0, a.taw, 0.5 * 0 + (a.raw / a.taw)).dr;   // p = RAW/TAW
    }
    return out;
  }

  return { DEFAULTS, cfg, plan, outlook, ndviStress, decideDay, hhmm };
});
