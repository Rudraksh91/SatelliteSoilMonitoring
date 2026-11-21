// Offline tests for the Sentinel-1 radar check in farmer/core.js (no network: fake STAC, token and GeoTIFF). Run: node tests/test_radar.js
const C = require('../farmer/core.js');
let n = 0, fail = 0;
const near = (a, b, t = 1e-6) => Math.abs(a - b) < t;
function check(name, cond) { n++; console.log((cond ? 'ok   ' : 'FAIL ') + name); if (!cond) fail++; }

// ── pure helpers ──
check('linear power 0.1 -> -10 dB', near(C.toDb(0.1), -10, 1e-9));
check('RVI: no cross-pol -> 0, equal VV and VH -> 2/1 scaled to 4*vh/(vv+vh)', near(C.radarRvi(0.1, 0), 0) && near(C.radarRvi(0.1, 0.1), 2));
check('RVI of a typical vegetated pixel (VV .12, VH .03) is 0.8', near(C.radarRvi(0.12, 0.03), 0.8, 1e-9));
check('speckle noise band shrinks as more pixels are averaged', C.radarNoiseDb(4) > C.radarNoiseDb(16) && C.radarNoiseDb(16) > C.radarNoiseDb(64));
check('noise band never below 0.7 dB', C.radarNoiseDb(100000) === 0.7);
check('4 pixels: band is wide (> 2 dB); 144 pixels: tight (0.7 dB)', C.radarNoiseDb(4) > 2 && C.radarNoiseDb(144) === 0.7);

const P = (date, vh, nPix = 64) => ({ date, vhDb: vh, vvDb: vh + 8, rvi: 0.6, n: nPix });
check('trend needs two passes', C.radarTrend([P('2026-11-17', -16)]) === null && C.radarTrend([]) === null && C.radarTrend(null) === null);
let t = C.radarTrend([P('2026-11-17', -13), P('2026-11-05', -16), P('2026-10-24', -16)]);
check('stronger signal than the earlier passes -> up', t.dir === 'up' && near(t.delta, 3, 1e-9) && t.since === '2026-10-24');
t = C.radarTrend([P('2026-11-17', -19), P('2026-11-05', -16), P('2026-10-24', -16)]);
check('weaker signal -> down', t.dir === 'down' && near(t.delta, -3, 1e-9));
t = C.radarTrend([P('2026-11-17', -16.3), P('2026-11-05', -16), P('2026-10-24', -16)]);
check('change inside the noise band -> flat', t.dir === 'flat');
t = C.radarTrend([P('2026-10-24', -16), P('2026-11-17', -14), P('2026-11-05', -16)]);
check('input order does not matter; newest is the last by date', t.date === '2026-11-17' && t.dir === 'up');
t = C.radarTrend([P('2026-11-17', -18.5, 4), P('2026-11-05', -16, 4)]);
check('the same 2.5 dB drop is flat on a tiny field but a real drop on a big one',
  t.dir === 'flat' && !t.reliable && C.radarTrend([P('2026-11-17', -18.5, 100), P('2026-11-05', -16, 100)]).dir === 'down');
check('baseline uses at most the two passes before the newest', C.radarTrend([P('2026-11-17', -16), P('2026-11-05', -16), P('2026-10-24', -16), P('2026-10-12', -30)]).base === -16);

// ── bridge: cloud gap between optical and radar ──
let b = C.radarBridge([P('2026-11-17', -16)], [{ date: '2026-10-20', ndvi: 0.5 }], '2026-11-21');
check('optical 32 days old, radar 4 days old: optical old and radar newer', b.opticalAge === 32 && b.radarAge === 4 && b.opticalOld && b.radarNewer);
b = C.radarBridge([P('2026-11-10', -16)], [{ date: '2026-11-18', ndvi: 0.5 }], '2026-11-21');
check('fresh optical picture: radar is not needed to bridge', !b.opticalOld && !b.radarNewer);
b = C.radarBridge([P('2026-11-10', -16)], [], '2026-11-21');
check('no clear optical picture at all counts as old', b.opticalAge === null && b.opticalOld && b.radarNewer);
b = C.radarBridge([], [{ date: '2026-11-18', ndvi: null }], '2026-11-21');
check('an optical scene without an NDVI value does not count', b.opticalDate === null && b.opticalOld);

// ── radarSeries against a fake Planetary Computer ──
const farm = C.squareFromArea(21.62707, 73.5854, 1.0);
const [E, N] = C.toUTM(21.62707, 73.5854, 43);
const ORX = E - 3000, ORY = N + 3000, SIZE = 600;
const VH_BY_DATE = { '2026-11-17': 0.02, '2026-11-05': 0.03, '2026-10-24': 0.03 };
const feat = (date, orbit, rel, epsg = 32643) => ({ properties: { datetime: date + 'T01:02:00Z', 'sat:orbit_state': orbit, 'sat:relative_orbit': rel, 'proj:epsg': epsg, 'sar:instrument_mode': 'IW' },
  assets: { vv: { href: `https://blob/${date}/${orbit}/vv.tif` }, vh: { href: `https://blob/${date}/${orbit}/vh.tif` } } });
const FEATS = [feat('2026-11-17', 'descending', 34), feat('2026-11-12', 'ascending', 70), feat('2026-11-05', 'descending', 34), feat('2026-10-24', 'descending', 34)];
let calls = { stac: 0, token: 0 };
function makeFetch(opts = {}) {
  return async (url, o) => {
    if (url.indexOf('/search') > 0) { calls.stac++; return { ok: true, json: async () => ({ features: FEATS }) }; }
    if (url.indexOf('/token/sentinel-1-rtc') > 0) { calls.token++; if (opts.tokenFail) return { ok: false, status: 429 }; return { ok: true, json: async () => ({ token: 'sv=abc&sig=xyz', 'msft:expiry': new Date(Date.now() + 3600e3).toISOString() }) }; }
    throw new Error('unexpected url ' + url);
  };
}
const fakeTiff = (opts = {}) => ({ fromUrl: async url => {
  if (url.indexOf('sv=abc&sig=xyz') < 0) throw new Error('unsigned url: ' + url);          // every read must carry the token
  const date = url.match(/blob\/([\d-]+)\//)[1], isVh = /vh\.tif/.test(url);
  return { getImage: async () => ({
    getOrigin: () => [ORX, ORY], getResolution: () => [10, -10], getWidth: () => SIZE, getHeight: () => SIZE,
    readRasters: async ({ window: w }) => { const len = (w[2] - w[0]) * (w[3] - w[1]); const v = isVh ? (VH_BY_DATE[date] || 0.03) : 0.12; return [new Float32Array(len).fill(opts.nodata ? 0 : v)]; } }) };
} });
(async () => {
  calls = { stac: 0, token: 0 };
  C.resetRadarToken();
  let ser = await C.radarSeries(farm, fakeTiff(), makeFetch());
  check('uses the orbit track with most passes (3 descending, not the lone ascending pass)', ser.length === 3 && ser.every(s => s.orbit === 'descending' && s.rel === 34));
  check('newest first, one row per date', ser[0].date === '2026-11-17' && ser[2].date === '2026-10-24');
  check('VV 0.12 and VH 0.02 average to -9.2 dB and -17.0 dB', near(ser[0].vvDb, 10 * Math.log10(0.12), 1e-3) && near(ser[0].vhDb, 10 * Math.log10(0.02), 1e-3));
  check('cross-pol difference and RVI are derived from the linear means', near(ser[0].crossDb, ser[0].vhDb - ser[0].vvDb, 1e-9) && near(ser[0].rvi, 4 * 0.02 / 0.14, 1e-4));
  check('a 1 ha field averages ~64 pixels', ser[0].n > 55 && ser[0].n < 90);
  check('one token request serves every file in every pass', calls.token === 1 && calls.stac === 1);
  const tr = C.radarTrend(ser);
  check('end to end: VH 0.02 vs 0.03 earlier is a real drop', tr.dir === 'down' && tr.delta < -1.5);

  ser = await C.radarSeries(farm, fakeTiff({ nodata: true }), makeFetch());
  check('nodata (zeros) everywhere gives no passes, not zeros', ser.length === 0);
  C.resetRadarToken();
  ser = await C.radarSeries(farm, fakeTiff(), makeFetch({ tokenFail: true }));
  C.resetRadarToken();
  check('token service refusing (HTTP 429) gives an empty result instead of throwing', Array.isArray(ser) && ser.length === 0);
  ser = await C.radarSeries(C.squareFromArea(21.9, 73.9, 1.0), fakeTiff(), makeFetch());
  check('a field outside the pass footprint is skipped', ser.length === 0);

  console.log(`\n${n - fail}/${n} checks passed`);
  process.exit(fail ? 1 : 0);
})();
