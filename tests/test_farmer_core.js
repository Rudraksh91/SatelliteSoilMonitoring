// Offline tests for farmer/core.js (no network). Run: node tests/test_farmer_core.js
const C = require('../farmer/core.js');
let n = 0, fail = 0;
const near = (a, b, t = 1e-6) => Math.abs(a - b) < t;
function check(name, cond) { n++; console.log((cond ? 'ok   ' : 'FAIL ') + name); if (!cond) fail++; }

// ── geometry ──
const sq = C.squareFromArea(21.6277, 73.5854, 1.0);                       // 1 ha square
check('square from 1 ha pin has area 1 ha (±1%)', Math.abs(C.polygonAreaM2(sq) - 10000) < 100);
check('0.15 ha farm area', Math.abs(C.polygonAreaM2(C.squareFromArea(21.6, 73.5, 0.15)) - 1500) < 20);
const tri = [[21.0, 73.0], [21.0, 73.001], [21.001, 73.0]];
check('triangle area ≈ half of its bounding box', Math.abs(C.polygonAreaM2(tri) - 0.5 * 111320 * Math.cos(21 * Math.PI / 180) * 0.001 * 110574 * 0.001) < 60);
check('point in polygon', C.pointInPolygon(1, 1, [[0, 0], [2, 0], [2, 2], [0, 2]]) && !C.pointInPolygon(3, 1, [[0, 0], [2, 0], [2, 2], [0, 2]]));
check('distance to edges', near(C.distToEdges(1, 1, [[0, 0], [4, 0], [4, 4], [0, 4]]), 1));
// ── UTM (checked against the georeferencing of a real Sentinel-2 tile, 43QCD: pixel (5338,767) covers this point) ──
const [E, N] = C.toUTM(21.6280, 73.5834, 43);
check('UTM zone for Gujarat is 43', C.utmZone(73.5834) === 43);
check('UTM easting/northing match the Sentinel-2 tile grid (±15 m)', Math.abs(E - 353388) < 15 && Math.abs(N - 2392321) < 15);
check('UTM central meridian gives easting 500000', near(C.toUTM(21, 75, 43)[0], 500000, 1e-3));
// ── farm pixels & edge shrink ──
const farm100 = C.squareFromArea(21.6277, 73.5854, 1.0);
const fp = C.farmPixels(farm100, 43);
check('1 ha plot: ~100 pixels, shrunk 10 m leaves ~64-81', fp.total > 90 && fp.total < 115 && fp.shrunk && fp.pixels.length > 55 && fp.pixels.length < 90);
const tiny = C.farmPixels(C.squareFromArea(21.6277, 73.5854, 0.05), 43);
check('0.05 ha plot: shrink would leave too few pixels -> falls back to unshrunk', tiny.shrunk === false && tiny.pixels.length >= 1);
// ── soil estimate ──
const sr = C.saxtonRawls(0.31, 0.40, 2.2);
check('Saxton-Rawls hand calc FC .376 / WP .244', near(sr.fc, 0.3763, 5e-4) && near(sr.wp, 0.2444, 5e-4));
const sgDoc = { properties: { layers: [
  { name: 'sand', depths: [{ label: '0-5cm', values: { mean: 300 } }, { label: '5-15cm', values: { mean: 296 } }, { label: '15-30cm', values: { mean: 300 } }, { label: '30-60cm', values: { mean: 338 } }] },
  { name: 'clay', depths: [{ label: '0-5cm', values: { mean: 397 } }, { label: '5-15cm', values: { mean: 413 } }, { label: '15-30cm', values: { mean: 406 } }, { label: '30-60cm', values: { mean: 355 } }] },
  { name: 'soc', depths: [{ label: '0-5cm', values: { mean: 128 } }, { label: '5-15cm', values: { mean: 104 } }, { label: '15-30cm', values: { mean: 64 } }, { label: '30-60cm', values: { mean: 47 } }] }] } };
const soil = C.soilFromSoilGrids(sgDoc, 0.4);
check('SoilGrids profile -> FC .371 / WP .239 (same as the Python job)', near(soil.theta_fc, 0.371, 2e-3) && near(soil.theta_wp, 0.239, 2e-3));
// ── model parity with scripts/agrisat_model.py (same hand-calculated vectors) ──
const taw = C.tawMm(0.28, 0.13, 0.40);
check('TAW = 60', near(taw, 60));
const s1 = C.step(20, 5.0, 0.8, 12.0, 2.0, 60, 0.5);
check('hand calc: Dr = 20 + 4.0 - 9.6 - 2 = 12.4', near(s1.dr, 12.4) && near(s1.etc, 4.0));
check('Ks = 0.5 at Dr 45', near(C.ks(45, 60, 0.5), 0.5));
check('rain < 5 mm is ignored, 20 mm -> 16 effective', C.effRain(4.9) === 0 && near(C.effRain(20), 16));
check('depletion never negative / above TAW', C.step(5, 0, 1, 50, 0, 60, .5).dr === 0 && C.step(59.9, 50, 1.2, 0, 0, 60, .5).dr === 60);
check('Kc from NDVI .5 -> .725', near(C.kcFromNdvi(0.5, C.DEFAULT_CROP), 0.725));
const ser = [{ date: '2026-11-10', ndvi: 0.30 }, { date: '2026-11-20', ndvi: 0.60 }];
const ip = C.interpNdvi(ser, '2026-11-15'); check('NDVI linear interpolation', near(ip.ndvi, 0.45, 1e-9) && ip.age === 5);
// ── advice scenarios ──
function weather(from, days, et0, rain) { const t = [], r = [], e = []; for (let i = 0; i < days; i++) { t.push(C.addDays(from, i)); r.push(rain(i)); e.push(et0); } return { time: t, rain: r, et0: e }; }
const today = '2026-11-20', w0 = weather('2026-10-21', 33, 6, () => 0);       // Oct 21 .. Nov 22, dry
const a1 = C.advise({ today, weather: w0, soil, ndvi: [{ date: '2026-11-17', ndvi: 0.5 }], areaM2: 10000, flowLph: 5000, start: { date: '2026-11-01', depletion_mm: 0 } });
check('19 dry days at 6 mm ET0: irrigate, with litres and hours', a1.decision === 'IRRIGATE' && a1.litres > 0 && a1.hours > 0 && a1.dr >= a1.raw);
check('gross depth = Dr / 0.88 and litres = depth x area', near(a1.grossMm, a1.dr / 0.88, 1e-9) && near(a1.litres, a1.grossMm * 10000, 1e-6));
const wRain = weather('2026-10-21', 33, 6, i => (i === 31 ? 25 : 0));               // 25 mm on Nov 21 (tomorrow)
check('25 mm forecast tomorrow: wait for the rain', C.advise({ today, weather: wRain, soil, ndvi: [], start: { date: '2026-11-01', depletion_mm: 0 } }).decision === 'WAIT_RAIN');
const fresh = C.advise({ today, weather: w0, soil, ndvi: [], start: { date: '2026-11-19', depletion_mm: 0 } });
check('soil just refilled yesterday: OK with days left', fresh.decision === 'OK' && fresh.daysLeft > 1);
const withIrr = C.advise({ today, weather: w0, soil, ndvi: [{ date: '2026-11-17', ndvi: 0.5 }], areaM2: 10000, start: { date: '2026-11-01', depletion_mm: 0 }, events: [{ date: '2026-11-18', type: 'irrigation_mm', value: 25 }] });
check('farmer-logged irrigation lowers depletion', withIrr.dr < a1.dr - 15);
const withRain = C.advise({ today, weather: w0, soil, ndvi: [], start: { date: '2026-11-01', depletion_mm: 0 }, events: [{ date: '2026-11-19', type: 'rain_mm', value: 40 }] });
const noRain = C.advise({ today, weather: w0, soil, ndvi: [], start: { date: '2026-11-01', depletion_mm: 0 } });
check('farmer-reported local rain counts (40 mm -> 32 mm effective)', noRain.dr - withRain.dr > 25 && noRain.dr - withRain.dr < 40);
check('stale / missing NDVI is flagged and a fallback Kc is used', C.advise({ today, weather: w0, soil, ndvi: [], start: { date: '2026-11-01', depletion_mm: 0 } }).ndviStale === true);
check('start date older than the weather window is flagged as assumed', C.advise({ today, weather: w0, soil, ndvi: [] }).startAssumed === true);
const anch = C.advise({ today, weather: w0, soil, ndvi: [], start: { date: today, fracRaw: 0.1 } });
check('farmer says soil is WET today: anchored at 0.1 x RAW, no setup prompt', anch.needsSetup === false && near(anch.dr, 0.1 * anch.raw, 1e-6) && anch.decision === 'OK');
const dryA = C.advise({ today, weather: w0, soil, ndvi: [], start: { date: today, fracRaw: 1.2 }, areaM2: 5000 });
check('farmer says soil is DRY today: irrigate', dryA.needsSetup === false && dryA.decision === 'IRRIGATE' && dryA.litres > 0);
check('no starting information -> needsSetup (the page asks instead of guessing litres)', C.advise({ today, weather: w0, soil, ndvi: [] }).needsSetup === true);
// ── crops ──
const cot = C.cropParams('cotton', '2026-08-01');
check('cotton: Kcb .15..1.10, p .65, root 1.0 m, stage curve to 195 days', near(cot.kcb_min, 0.15) && near(cot.kcb_max, 1.10) && near(cot.p, 0.65) && near(cot.root_depth_m, 1.0) && cot.stage_curve[4][0] === 195);
check('unknown crop id falls back to general', near(C.cropParams('xyz').kcb_max, 1.15) && C.cropParams('xyz').stage_curve === undefined);
check('stage curve interpolates (cotton at day 55 = midway .15 -> 1.10)', near(C.stageKc(55, cot.stage_curve), 0.15 + (1.10 - 0.15) * 25 / 50));
const staleCotton = C.advise({ today, weather: w0, soil, ndvi: [], crop: cot, start: { date: '2026-11-01', depletion_mm: 0 } });
check('NDVI missing + planting date: uses the crop-stage curve (day ~110 -> Kc 1.10)', staleCotton.ndviStale && near(staleCotton.kc, 1.10, 0.02));
const wheatNoDate = C.advise({ today, weather: w0, soil, ndvi: [], crop: C.cropParams('wheat'), start: { date: '2026-11-01', depletion_mm: 0 } });
check('NDVI missing + no planting date: mid-value fallback (not the stage curve)', wheatNoDate.ndviStale && near(wheatNoDate.kc, (0.15 + 1.10) / 2, 1e-9));
const deepRoot = C.advise({ today, weather: w0, soil, ndvi: [], crop: C.cropParams('sugarcane'), start: { date: today, fracRaw: 0.2 } });
const shallow = C.advise({ today, weather: w0, soil, ndvi: [], crop: C.cropParams('vegetables'), start: { date: today, fracRaw: 0.2 } });
check('deeper-rooted crop has a bigger water store (TAW) than vegetables', deepRoot.taw > 2.5 * shallow.taw);
check('reason carries a translation key and parameters', typeof a1.reasonKey === 'string' && a1.rp && a1.rp.dr !== undefined);
check('Kc and NDVI are reported even when the start is today (no days balanced)', anch.kc != null && anch.ndvi === null && anch.ndviStale === true && C.advise({ today, weather: w0, soil, ndvi: [{ date: '2026-11-17', ndvi: 0.5 }], start: { date: today, fracRaw: 0.5 } }).ndvi > 0.4);
// ── NDVI map support ──
const [E2, N2] = C.toUTM(21.6280, 73.5834, 43), [la2, ln2] = C.fromUTM(E2, N2, 43);
check('inverse UTM round trip is exact to < 1 cm', Math.hypot((la2 - 21.6280) * 110574, (ln2 - 73.5834) * 103600) < 0.01);
const fpAll = C.farmPixels(C.squareFromArea(21.6277, 73.5854, 1.0), 43);
check('farmPixels returns every pixel inside the outline (all) and the used subset', fpAll.all.length >= fpAll.pixels.length && fpAll.all.length > 90 && fpAll.shrunk);
const cell = C.fromUTM(fpAll.all[0][0] - 5, fpAll.all[0][1] - 5, 43), cell2 = C.fromUTM(fpAll.all[0][0] + 5, fpAll.all[0][1] + 5, 43);
check('a pixel corner pair maps to a ~10 m square on the ground', Math.abs((cell2[0] - cell[0]) * 110574 - 10) < 0.2 && Math.abs((cell2[1] - cell[1]) * 111320 * Math.cos(21.6 * Math.PI / 180) - 10) < 0.2);
const mkL = (nm, a) => ({ name: nm, depths: ['0-5cm', '5-15cm', '15-30cm', '30-60cm'].map((l, i) => ({ label: l, values: { mean: a[i] } })) });
let threw = false; try { C.soilFromSoilGrids({ properties: { layers: [mkL('sand', [null, null, null, null]), mkL('clay', [null, null, null, null]), mkL('soc', [null, null, null, null])] } }, 1.0); } catch (e) { threw = /no soil data/.test(e.message); }
check('SoilGrids nulls (no data at this place) are rejected, not modelled as zero sand/clay', threw);
const partial = C.soilFromSoilGrids({ properties: { layers: [mkL('sand', [null, 300, 300, 338]), mkL('clay', [null, 413, 406, 355]), mkL('soc', [null, 104, 64, 47])] } }, 0.4);
check('a missing top layer is skipped (not averaged in as 0)', partial.clay > 0.37 && partial.clay < 0.42 && partial.theta_fc > 0.3);
// ── crop list: every Indian crop has sane FAO-56 style parameters and names in all three languages ──
{
  const I = require('../farmer/i18n.js'), ids = Object.keys(C.CROPS);
  let bad = [];
  ids.forEach(k => { const c = C.CROPS[k];
    if (!(c.kcb_ini > 0 && c.kcb_mid >= c.kcb_ini && c.kcb_mid <= 1.25 && c.kcb_end > 0 && c.p >= 0.1 && c.p <= 0.8 && c.zr >= 0.2 && c.zr <= 2 && C.CROP_CATS.includes(c.cat))) bad.push(k);
    if (c.stages && !(c.stages.length === 4 && c.stages.every(d => d > 0))) bad.push(k + ' stages'); });
  check('at least 60 crops in the list (cereals, pulses, oilseeds, vegetables, fruit, spices, fodder ...)', ids.length >= 60);
  check('every crop has sane Kcb / depletion fraction / root depth / stages', bad.length === 0);
  check('every crop and category has a name in English, Hindi and Gujarati', ['en', 'hi', 'gu'].every(l => ids.every(k => I.dict[l]['crop_' + k]) && C.CROP_CATS.every(c => I.dict[l]['cat_' + c])));
  check('key Indian crops are present', ['rice', 'bajra', 'sorghum', 'ragi', 'tur', 'mung', 'urad', 'chickpea', 'mustard', 'groundnut', 'cotton', 'sugarcane', 'onion', 'mango', 'turmeric', 'cumin', 'tea', 'coconut'].every(k => C.CROPS[k]));
  check('a perennial (mango) has no stage curve but a stage crop (rice) does', !C.cropParams('mango', '2026-01-01').stage_curve && C.cropParams('rice', '2026-07-01').stage_curve.length === 5);
}

// ── pump size per field ──
{
  check('litres per hour, litres per minute and m3 per hour convert', C.pumpFlowLph(3000, 'lph') === 3000 && C.pumpFlowLph(50, 'lpm') === 3000 && C.pumpFlowLph(3, 'm3h') === 3000);
  const hp1 = C.pumpFlowLph(1, 'hp', 20), hp5 = C.pumpFlowLph(5, 'hp', 20);
  check('a 1 HP pump at 20 m lift is estimated at about 5,000 L/h (physically plausible)', hp1 > 4500 && hp1 < 6500);
  check('HP estimate scales with power and falls with lift', Math.abs(hp5 / hp1 - 5) < 0.1 && C.pumpFlowLph(1, 'hp', 40) < hp1 * 0.55);
  check('empty, zero, negative or absurd pump sizes are rejected, not guessed', [C.pumpFlowLph('', 'lph'), C.pumpFlowLph(0, 'hp'), C.pumpFlowLph(-5, 'lpm'), C.pumpFlowLph(1e9, 'lph'), C.pumpFlowLph('abc', 'lph')].every(v => v === null));
  check('hours for 25 mm on 1 ha with a 5,000 L/h pump = 25 x 10,000 / 0.88 / 5,000', Math.abs(C.pumpHoursFor(25, 10000, 5000, 0.88) - 56.82) < 0.05 && C.pumpHoursFor(25, 10000, null) === null);
}

// ── soil backup: OpenLandMap copy of SoilGrids, and the "ask the main service, fall back after 6 s" logic ──
{
  const o = C.soilFromOlm({ sand: { 0: 31, 10: 31, 30: 30 }, clay: { 0: 38, 10: 38, 30: 40 }, soc: { 0: 2, 10: 2, 30: 1 } }, 0.4);
  check('olm soil: campus values give a field capacity and wilting point close to the SoilGrids 2.0 estimate (0.371 / 0.239)', Math.abs(o.theta_fc - 0.371) < 0.01 && Math.abs(o.theta_wp - 0.239) < 0.01 && /OpenLandMap/.test(o.source) && /ESTIMATE/.test(o.source));
  check('olm soil: only layers inside the root zone are read (0.4 m -> 3 files per property; 1.0 m -> 5)', C.olmLayersFor(0.4).length === 3 && C.olmLayersFor(1.0).length === 5 && C.olmLayersFor(0.04).length === 1);
  check('olm soil: no-data layers are skipped, never counted as zero', Math.abs(C.soilFromOlm({ sand: { 0: 31, 10: null, 30: null }, clay: { 0: 38, 10: null, 30: null }, soc: { 0: 2, 10: null, 30: null } }, 0.4).sand - 0.31) < 1e-9);
  let threw = 0; try { C.soilFromOlm({ sand: {}, clay: {}, soc: {} }, 0.4); } catch (e) { threw++; } try { C.soilFromOlm({ sand: { 0: 90 }, clay: { 0: 80 }, soc: { 0: 1 } }, 0.4); } catch (e) { threw++; }
  check('olm soil: no data, or sand + clay above 100 %, is refused instead of modelled', threw === 2);
  const fakeTiff = (log) => ({ fromUrl: async url => { log.push(url); const val = /sand/.test(url) ? 31 : /clay/.test(url) ? 38 : 2;
    return { getImage: async () => ({ getOrigin: () => [-180, 87.37], getResolution: () => [0.002083333, -0.002083333], getWidth: () => 172800, getHeight: () => 71698, readRasters: async () => [[val]] }) }; } });
  const sg = { properties: { layers: ['sand', 'clay', 'soc'].map(nm => ({ name: nm, depths: ['0-5cm', '5-15cm', '15-30cm', '30-60cm'].map(label => ({ label, values: { mean: nm === 'soc' ? 78 : nm === 'sand' ? 308 : 394 } })) })) } };
  const okFetch = async () => ({ ok: true, json: async () => sg }), badFetch = async () => ({ ok: false, status: 503 }), hang = () => new Promise(() => {});
  (async () => {
    let log = [];
    let r = await C.fetchSoilOlm(21.627, 73.585, 0.4, fakeTiff(log));
    check('fetchSoilOlm: reads 9 files (3 properties x 3 layers), all from the OpenLandMap bucket', log.length === 9 && log.every(u => u.startsWith('https://s3.eu-central-1.wasabisys.com/openlandmap/predicted250m/')) && r.theta_fc > r.theta_wp);
    log = []; r = await C.fetchSoilAny(21.627, 73.585, 0.4, { fetchFn: okFetch, GeoTIFF: fakeTiff(log), backupDelayMs: 30 });
    await new Promise(x => setTimeout(x, 80));
    check('fetchSoilAny: when the main service answers, its answer is used and the backup is never read', /SoilGrids 250 m \+/.test(r.source) && !/OpenLandMap/.test(r.source) && log.length === 0);
    log = []; r = await C.fetchSoilAny(21.627, 73.585, 0.4, { fetchFn: badFetch, GeoTIFF: fakeTiff(log), backupDelayMs: 5000 });
    check('fetchSoilAny: when the main service fails (503), the backup is read at once', /OpenLandMap/.test(r.source) && log.length === 9);
    log = []; const t0 = Date.now(); r = await C.fetchSoilAny(21.627, 73.585, 0.4, { fetchFn: hang, GeoTIFF: fakeTiff(log), backupDelayMs: 40, mainMs: 5000 });
    check('fetchSoilAny: when the main service hangs, the backup starts after the delay and wins', /OpenLandMap/.test(r.source) && Date.now() - t0 < 2000 && log.length === 9);
    let err = null; try { await C.fetchSoilAny(21.627, 73.585, 0.4, { fetchFn: badFetch, GeoTIFF: { fromUrl: async () => { throw new Error('bucket down'); } }, backupDelayMs: 5 }); } catch (e) { err = e; }
    check('fetchSoilAny: both sources failing -> an error (the page then shows the general-value notice)', err && /SoilGrids 503/.test(err.message));
    err = null; try { await C.fetchSoilAny(21.627, 73.585, 0.4, { fetchFn: badFetch }); } catch (e) { err = e; }
    check('fetchSoilAny: no backup available (no GeoTIFF reader) -> the main service\'s error', err && /SoilGrids 503/.test(err.message));
    console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
  })().catch(e => { console.error(e); process.exit(1); });
}

