// Tests for NDMI / NDRE (farmer/core.js) with a synthetic Sentinel-2 tile: node tests/test_indices.js
const C = require('../farmer/core.js');
let n = 0, fail = 0; const near = (a, b, t = 0.01) => a != null && Math.abs(a - b) < t;
const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
const lat = 21.6277, lng = 73.5854, d = 0.001, pts = [[lat, lng], [lat + d, lng], [lat + d, lng + d], [lat, lng + d]];
const [E, N] = C.toUTM(lat, lng, 43), ox = Math.floor(E / 20) * 20 - 600, oy = Math.ceil(N / 20) * 20 + 600;
// reflectance -> DN (BOA offset already applied, as flagged by Earth Search)
const dn = r => Math.round(r / 1e-4);
function image(res, value, o) {   // 1200 m wide tile, constant value
  o = o || {}; const size = 1200 / res;
  return { getOrigin: () => [ox, oy], getResolution: () => [res, -res], getWidth: () => size, getHeight: () => size,
    readRasters: async ({ window: w }) => { if (o.fail) throw new Error('read failed'); return [new Uint16Array((w[2] - w[0]) * (w[3] - w[1])).fill(value)]; } };
}
const GeoTIFF = { fromUrl: async (href) => ({ getImage: async () => IMG[href] }) };
let IMG = {};
function feature(drop, flt) {
  const refl = { red: 0.05, nir: 0.40, scl: 4, swir16: 0.20, nir08: 0.38, rededge1: 0.15 };
  const res = { red: 10, nir: 10, scl: 20, swir16: 20, nir08: 20, rededge1: 20 };
  IMG = {}; const assets = {};
  Object.keys(refl).forEach(k => { if (drop && drop.includes(k)) return; assets[k] = { href: k, 'raster:bands': [{ scale: 1e-4 }] };
    IMG[k] = image(res[k], k === 'scl' ? 4 : dn(refl[k]), { fail: flt && flt.includes(k) }); });
  return { assets, bbox: null, properties: { 'proj:epsg': 32643, 'earthsearch:boa_offset_applied': true, datetime: '2026-11-10T05:00:00Z' } };
}
const farm = { zone: 43, latlngs: pts, opts: { minPixels: 8 } };
(async () => {
  // reach sceneNdvi through ndviSeries with a fake STAC response
  const run = async (feat) => C.ndviSeries(pts, GeoTIFF, async () => ({ ok: true, json: async () => ({ features: [feat] }) }), { maxScenes: 1, minPixels: 8 });
  let s = (await run(feature()))[0];
  check('NDVI from B8/B4 = (0.40-0.05)/0.45', s && near(s.ndvi, 0.778));
  check('NDMI from B8/B11 = (0.40-0.20)/0.60', s && near(s.ndmi, 0.333));
  check('NDRE from B8A/B5 = (0.38-0.15)/0.53', s && near(s.ndre, 0.434));
  check('NDMI and NDRE grids are saved beside the NDVI grid', s && s.grid.vm && s.grid.vr && s.grid.vm.length === s.grid.v.length && s.grid.vm.some(v => v === 33));
  s = (await run(feature(['swir16'])))[0];
  check('no SWIR band: NDVI still returned, NDMI empty (not guessed)', s && near(s.ndvi, 0.778) && s.ndmi === null && s.ndre != null);
  s = (await run(feature(['rededge1'])))[0];
  check('no red-edge band: NDVI and NDMI still returned, NDRE empty', s && near(s.ndvi, 0.778) && near(s.ndmi, 0.333) && s.ndre === null);
  s = (await run(feature(null, ['swir16'])))[0];
  check('a failed SWIR read never loses the NDVI', s && near(s.ndvi, 0.778) && s.ndmi === null);
  // ── model helpers ──
  const ser = [{ date: '2026-11-01', ndmi: 0.30, ndre: 0.40 }, { date: '2026-11-06', ndmi: 0.32, ndre: 0.41 }, { date: '2026-11-11', ndmi: 0.20, ndre: 0.30 }];
  const t = C.indexTrend(ser, 'ndmi');
  check('index trend: latest, previous and direction', near(t.value, 0.20, 1e-9) && near(t.prev, 0.32, 1e-9) && t.dir === 'down');
  const an = C.ndmiAnomaly(ser);
  check('NDMI well below the field\'s own earlier trend is flagged', an.flag && near(an.base, 0.31, 1e-9) && near(an.drop, 0.11, 1e-9));
  check('a small NDMI dip (within noise) is not flagged', !C.ndmiAnomaly([{ date: '2026-11-01', ndmi: 0.30 }, { date: '2026-11-06', ndmi: 0.31 }, { date: '2026-11-11', ndmi: 0.28 }]).flag);
  check('fewer than 3 clear scenes: no anomaly verdict at all', C.ndmiAnomaly(ser.slice(1)) === null && C.ndmiAnomaly([]) === null);
  check('missing NDMI values in old cached scenes are ignored', C.indexTrend([{ date: '2026-11-01', ndvi: 0.5 }], 'ndmi') === null);
  console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
})();
