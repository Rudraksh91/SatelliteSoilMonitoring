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
console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
