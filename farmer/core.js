/* AgriSat farmer core — geometry, Sentinel-2 NDVI (STAC + COG, in the browser), soil estimate, FAO-56 balance.
 * UMD: works in the browser (window.AgriCore) and in Node (tests/test_farmer_core.js). No network code here
 * except via the fetch / GeoTIFF objects passed in. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriCore = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const DEG = Math.PI / 180;

  // ───────────────────────── geometry ─────────────────────────
  function centroid(pts) {
    let la = 0, ln = 0;
    pts.forEach(p => { la += p[0]; ln += p[1]; });
    return [la / pts.length, ln / pts.length];
  }
  function bbox(pts) {
    const la = pts.map(p => p[0]), ln = pts.map(p => p[1]);
    return [Math.min(...ln), Math.min(...la), Math.max(...ln), Math.max(...la)];   // [w,s,e,n]
  }
  // local equirectangular metres about the centroid (accurate to <0.1% at farm scale)
  function toLocalM(pts) {
    const [la0, ln0] = centroid(pts), kx = 111320 * Math.cos(la0 * DEG), ky = 110574;
    return pts.map(p => [(p[1] - ln0) * kx, (p[0] - la0) * ky]);
  }
  function polygonAreaM2(pts) {
    const m = toLocalM(pts); let a = 0;
    for (let i = 0; i < m.length; i++) { const j = (i + 1) % m.length; a += m[i][0] * m[j][1] - m[j][0] * m[i][1]; }
    return Math.abs(a) / 2;
  }
  function squareFromArea(lat, lng, ha) {            // pin + area -> square plot (clearly an approximation)
    const half = Math.sqrt(ha * 10000) / 2, dLat = half / 110574, dLng = half / (111320 * Math.cos(lat * DEG));
    return [[lat - dLat, lng - dLng], [lat - dLat, lng + dLng], [lat + dLat, lng + dLng], [lat + dLat, lng - dLng]];
  }
  function pointInPolygon(x, y, poly) {
    let inside = false;
    for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      const xi = poly[i][0], yi = poly[i][1], xj = poly[j][0], yj = poly[j][1];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }
  function distToSegment(px, py, a, b) {
    const dx = b[0] - a[0], dy = b[1] - a[1], l2 = dx * dx + dy * dy;
    let t = l2 ? ((px - a[0]) * dx + (py - a[1]) * dy) / l2 : 0; t = Math.max(0, Math.min(1, t));
    return Math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy));
  }
  function distToEdges(x, y, poly) {
    let d = Infinity;
    for (let i = 0; i < poly.length; i++) d = Math.min(d, distToSegment(x, y, poly[i], poly[(i + 1) % poly.length]));
    return d;
  }

  // ───────────────────────── UTM (WGS84, Krüger series) ─────────────────────────
  function utmZone(lng) { return Math.floor((lng + 180) / 6) + 1; }
  function toUTM(lat, lng, zone) {
    const a = 6378137, f = 1 / 298.257223563, k0 = 0.9996, n = f / (2 - f);
    const A = a / (1 + n) * (1 + n * n / 4 + n ** 4 / 64);
    const al = [0, n / 2 - 2 * n * n / 3 + 5 * n ** 3 / 16, 13 * n * n / 48 - 3 * n ** 3 / 5, 61 * n ** 3 / 240];
    const lam = (lng - ((zone - 1) * 6 - 180 + 3)) * DEG, phi = lat * DEG, s = Math.sin(phi);
    const q = Math.atanh(s) - 2 * Math.sqrt(n) / (1 + n) * Math.atanh(2 * Math.sqrt(n) / (1 + n) * s);
    const t = Math.sinh(q), xi = Math.atan2(t, Math.cos(lam)), eta = Math.atanh(Math.sin(lam) / Math.sqrt(1 + t * t));
    let E = eta, N = xi;
    for (let j = 1; j <= 3; j++) { E += al[j] * Math.cos(2 * j * xi) * Math.sinh(2 * j * eta); N += al[j] * Math.sin(2 * j * xi) * Math.cosh(2 * j * eta); }
    return [500000 + k0 * A * E, (lat < 0 ? 1e7 : 0) + k0 * A * N];
  }

  /** 10 m pixel centres (in UTM metres) inside the farm, >= shrinkM from the boundary (headland/road pixels).
   *  Falls back to no shrink when too few pixels remain. Returns {pixels, shrunk, zone}. */
  function farmPixels(pts, zone, opts) {
    const o = Object.assign({ res: 10, shrink: 10, minPixels: 8 }, opts || {});
    const poly = pts.map(p => toUTM(p[0], p[1], zone));
    const xs = poly.map(p => p[0]), ys = poly.map(p => p[1]);
    const x0 = Math.floor(Math.min(...xs) / o.res) * o.res, x1 = Math.max(...xs), y0 = Math.floor(Math.min(...ys) / o.res) * o.res, y1 = Math.max(...ys);
    const all = [], inner = [];
    for (let x = x0 + o.res / 2; x <= x1; x += o.res) for (let y = y0 + o.res / 2; y <= y1; y += o.res) {
      if (!pointInPolygon(x, y, poly)) continue;
      all.push([x, y]);
      if (distToEdges(x, y, poly) >= o.shrink) inner.push([x, y]);
    }
    if (inner.length >= o.minPixels) return { pixels: inner, shrunk: true, zone, total: all.length };
    return { pixels: all, shrunk: false, zone, total: all.length };
  }

  // ───────────────────────── Sentinel-2 via STAC + COG ─────────────────────────
  const STAC = 'https://earth-search.aws.element84.com/v1/search';
  async function searchScenes(bboxLL, days, maxCloud, fetchFn) {
    const end = new Date(), start = new Date(end.getTime() - days * 864e5);
    const body = { collections: ['sentinel-2-l2a'], bbox: bboxLL, limit: 40,
      datetime: start.toISOString().slice(0, 10) + 'T00:00:00Z/' + end.toISOString().slice(0, 10) + 'T23:59:59Z',
      sortby: [{ field: 'properties.datetime', direction: 'desc' }], query: { 'eo:cloud_cover': { lt: maxCloud } } };
    const r = await fetchFn(STAC, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    if (!r.ok) throw new Error('STAC ' + r.status);
    return (await r.json()).features || [];
  }
  function readSample(img, window) { return img.readRasters({ window, interleave: false, samples: [0] }); }

  /** Median NDVI over the farm's valid pixels in one scene (SCL-masked, reflectance offset applied as EE "HARMONIZED"). */
  async function sceneNdvi(feature, farm, GeoTIFF) {
    const a = feature.assets, epsg = feature.properties['proj:epsg'];
    if (!a.red || !a.nir || !a.scl) return null;
    if (epsg !== 32600 + farm.zone) {                      // scene tile in another UTM zone: re-project pixels
      farm = Object.assign({}, farm); farm.reproject = epsg - 32600;
    }
    const zone = farm.reproject || farm.zone;
    const pts = farm.latlngs;                              // re-derive the pixel grid in the scene's own zone
    const fp = farmPixels(pts, zone, farm.opts); const px = fp.pixels;
    if (!px.length) return null;
    const open = async (asset) => { const t = await GeoTIFF.fromUrl(asset.href); const img = await t.getImage(); return img; };
    const [red, nir, scl] = await Promise.all([open(a.red), open(a.nir), open(a.scl)]);
    const xs = px.map(p => p[0]), ys = px.map(p => p[1]);
    const bb = [Math.min(...xs) - 10, Math.min(...ys) - 10, Math.max(...xs) + 10, Math.max(...ys) + 10];
    const win = (img) => {
      const [ox, oy] = img.getOrigin(), [rx, ry] = img.getResolution();
      const x0 = Math.floor((bb[0] - ox) / rx), x1 = Math.ceil((bb[2] - ox) / rx);
      const y0 = Math.floor((bb[3] - oy) / ry), y1 = Math.ceil((bb[1] - oy) / ry);
      return { ox, oy, rx, ry, x0, y0, x1, y1, w: img.getWidth(), h: img.getHeight() };
    };
    const wr = win(red), wn = win(nir), ws = win(scl);
    if (wr.x0 < 0 || wr.y0 < 0 || wr.x1 > wr.w || wr.y1 > wr.h) return null;   // farm not fully inside this tile
    const [R, N, S] = await Promise.all([
      red.readRasters({ window: [wr.x0, wr.y0, wr.x1, wr.y1], samples: [0] }),
      nir.readRasters({ window: [wn.x0, wn.y0, wn.x1, wn.y1], samples: [0] }),
      scl.readRasters({ window: [ws.x0, ws.y0, ws.x1, ws.y1], samples: [0] })]);
    const rb = feature.assets.red['raster:bands'] ? feature.assets.red['raster:bands'][0] : null;
    const nb = feature.assets.nir['raster:bands'] ? feature.assets.nir['raster:bands'][0] : null;
    // Earth Search COGs flagged boa_offset_applied already contain the -1000 DN shift (the raster:bands offset of -0.1 must NOT be
    // applied again — verified against Earth Engine S2_SR_HARMONIZED: mean |dNDVI| 0.02 vs 0.56 when applied twice).
    const applied = feature.properties['earthsearch:boa_offset_applied'] === true;
    const offR = applied ? 0 : (rb && rb.offset !== undefined ? rb.offset : (feature.properties.datetime >= '2022-01-25' ? -0.1 : 0)), sclR = rb && rb.scale ? rb.scale : 1e-4;
    const offN = applied ? 0 : (nb && nb.offset !== undefined ? nb.offset : offR), sclN = nb && nb.scale ? nb.scale : sclR;
    const at = (arr, w, i, j) => arr[0][j * (w.x1 - w.x0) + i];
    const vals = []; let cloudy = 0;
    for (const [x, y] of px) {
      const i = Math.floor((x - wr.ox) / wr.rx) - wr.x0, j = Math.floor((y - wr.oy) / wr.ry) - wr.y0;
      const k = Math.floor((x - wn.ox) / wn.rx) - wn.x0, l = Math.floor((y - wn.oy) / wn.ry) - wn.y0;
      const m = Math.floor((x - ws.ox) / ws.rx) - ws.x0, n = Math.floor((y - ws.oy) / ws.ry) - ws.y0;
      const sc = at(S, ws, m, n);
      if (sc === 3 || sc === 8 || sc === 9 || sc === 10 || sc === 11 || sc === 0) { cloudy++; continue; }   // shadow, cloud, cirrus, snow, no data
      const dr = at(R, wr, i, j), dn = at(N, wn, k, l);
      if (!dr || !dn) continue;
      const r1 = Math.max(0, dr * sclR + offR), n1 = Math.max(0, dn * sclN + offN);   // like EE HARMONIZED: reflectance never below 0
      if (r1 + n1 <= 0) continue;
      vals.push(Math.max(-1, Math.min(1, (n1 - r1) / (n1 + r1))));
    }
    if (!vals.length) return { date: feature.properties.datetime.slice(0, 10), ndvi: null, n: 0, cloudy, total: px.length };
    vals.sort((p, q) => p - q);
    return { date: feature.properties.datetime.slice(0, 10), ndvi: vals[Math.floor(vals.length / 2)], n: vals.length, cloudy, total: px.length, shrunk: fp.shrunk };
  }

  /** Clean-scene NDVI series (newest first, one value per date). Only tiles that fully cover the farm are read, and
   *  scenes are read in parallel batches so a phone gets the answer in seconds, not minutes. */
  async function ndviSeries(latlngs, GeoTIFF, fetchFn, opts) {
    const o = Object.assign({ days: 45, maxCloud: 70, maxScenes: 3, minPixels: 8, concurrency: 3 }, opts || {});
    const zone = utmZone(centroid(latlngs)[1]);
    const bb = bbox(latlngs);
    const covers = (f) => !f.bbox || (f.bbox[0] <= bb[0] && f.bbox[1] <= bb[1] && f.bbox[2] >= bb[2] && f.bbox[3] >= bb[3]);
    const feats = (await searchScenes(bb, o.days, o.maxCloud, fetchFn)).filter(covers);
    const seenDate = new Set(), cand = [];
    for (const f of feats) { const d = f.properties.datetime.slice(0, 10); if (!seenDate.has(d)) { seenDate.add(d); cand.push(f); } }   // one tile per date
    const out = [];
    for (let i = 0; i < cand.length && out.length < o.maxScenes; i += o.concurrency) {
      const batch = cand.slice(i, i + o.concurrency);
      const res = await Promise.all(batch.map(f => sceneNdvi(f, { zone, latlngs, opts: { minPixels: o.minPixels } }, GeoTIFF).catch(() => null)));
      res.forEach(r => { if (r && r.ndvi !== null && r.n >= o.minPixels) out.push(r); });
    }
    return out.sort((p, q) => (p.date < q.date ? 1 : -1)).slice(0, o.maxScenes);
  }

  // ───────────────────────── soil estimate (SoilGrids + Saxton & Rawls 2006) ─────────────────────────
  const SG_LAYERS = [['0-5cm', 5], ['5-15cm', 15], ['15-30cm', 30], ['30-60cm', 60]];
  function saxtonRawls(S, C, OM) {
    const t1500t = -0.024 * S + 0.487 * C + 0.006 * OM + 0.005 * S * OM - 0.013 * C * OM + 0.068 * S * C + 0.031;
    const wp = t1500t + (0.14 * t1500t - 0.02);
    const t33t = -0.251 * S + 0.195 * C + 0.011 * OM + 0.006 * S * OM - 0.027 * C * OM + 0.452 * S * C + 0.299;
    const fc = t33t + (1.283 * t33t * t33t - 0.374 * t33t - 0.015);
    return { fc, wp };
  }
  function depthWeighted(byLabel, rootM) {
    const rootCm = rootM * 100; let top = 0, tot = 0, w = 0;
    for (const [label, bottom] of SG_LAYERS) { const th = Math.max(0, Math.min(bottom, rootCm) - top); if (th > 0) { tot += byLabel[label] * th; w += th; } top = bottom; }
    return tot / w;
  }
  function soilFromSoilGrids(doc, rootM) {
    const g = {};
    doc.properties.layers.forEach(l => { g[l.name] = {}; l.depths.forEach(d => { g[l.name][d.label] = d.values.mean; }); });
    const sand = depthWeighted(g.sand, rootM) / 1000, clay = depthWeighted(g.clay, rootM) / 1000;
    const om = Math.min(8, depthWeighted(g.soc, rootM) / 10 * 1.724 / 10);
    const { fc, wp } = saxtonRawls(sand, clay, om);
    return { theta_fc: +fc.toFixed(3), theta_wp: +wp.toFixed(3), sand: +sand.toFixed(3), clay: +clay.toFixed(3), om_pct: +om.toFixed(2),
             source: 'ESTIMATE: SoilGrids 250 m + Saxton-Rawls 2006' };
  }
  async function fetchSoil(lat, lng, rootM, fetchFn) {
    const q = 'https://rest.isric.org/soilgrids/v2.0/properties/query?lon=' + lng + '&lat=' + lat +
      '&property=sand&property=clay&property=soc' + SG_LAYERS.map(l => '&depth=' + l[0]).join('') + '&value=mean';
    const r = await fetchFn(q); if (!r.ok) throw new Error('SoilGrids ' + r.status);
    return soilFromSoilGrids(await r.json(), rootM);
  }

  // ───────────────────────── FAO-56 model (same equations as scripts/agrisat_model.py) ─────────────────────────
  const tawMm = (fc, wp, zr) => 1000 * (fc - wp) * zr;
  function ks(dr, taw, p) { const raw = p * taw; return dr <= raw ? 1 : Math.max(0, (taw - dr) / ((1 - p) * taw)); }
  const effRain = (r, min = 5, f = 0.8) => (r < min ? 0 : f * r);
  function step(drPrev, et0, kc, rain, irrig, taw, p) {
    const k = ks(drPrev, taw, p), etc = et0 * kc * k;
    return { dr: Math.min(Math.max(drPrev + etc - effRain(rain) - irrig, 0), taw), etc, ks: k };
  }
  const canopy = (n, s, f) => Math.min(1, Math.max(0, (n - s) / (f - s)));
  const kcFromNdvi = (n, c) => c.kcb_min + (c.kcb_max - c.kcb_min) * canopy(n, c.ndvi_soil, c.ndvi_full);
  function interpNdvi(series, day) {        // series: [{date:'YYYY-MM-DD', ndvi}] ; returns {ndvi, age}
    const d = new Date(day + 'T00:00:00Z').getTime(), pts = series.filter(s => s.ndvi != null).map(s => ({ t: new Date(s.date + 'T00:00:00Z').getTime(), v: s.ndvi })).sort((a, b) => a.t - b.t);
    const past = pts.filter(p => p.t <= d); if (!past.length) return { ndvi: null, age: null };
    const last = past[past.length - 1], next = pts.find(p => p.t > d), age = Math.round((d - last.t) / 864e5);
    if (next && next.t > last.t) return { ndvi: last.v + (next.v - last.v) * (d - last.t) / (next.t - last.t), age };
    return { ndvi: last.v, age };
  }
  const DEFAULT_CROP = { kcb_min: 0.30, kcb_max: 1.15, p: 0.5, ndvi_soil: 0.15, ndvi_full: 0.85, root_depth_m: 0.4, kc_fallback: 0.60, stale_days: 20 };
  const addDays = (iso, n) => new Date(new Date(iso + 'T00:00:00Z').getTime() + n * 864e5).toISOString().slice(0, 10);

  /** Pure advice for one farm. weather: {time:[], rain:[], et0:[]} (past + forecast); events: [{date,type,value}]; today: 'YYYY-MM-DD' */
  function advise(a) {
    const crop = Object.assign({}, DEFAULT_CROP, a.crop || {});
    const soil = a.soil || { theta_fc: 0.28, theta_wp: 0.13, source: 'textbook estimate' };
    const taw = tawMm(soil.theta_fc, soil.theta_wp, crop.root_depth_m), p = crop.p, raw = p * taw;
    const idx = {}; a.weather.time.forEach((t, i) => { idx[t] = i; });
    const start = a.start && a.start.date >= a.weather.time[0] ? a.start : { date: a.weather.time[0], depletion_mm: 0, assumed: true };
    const startMm = start.frac != null ? start.frac * taw : (start.depletion_mm || 0);   // frac = share of TAW already used (farmer's soil check)
    let dr = Math.min(taw, startMm), lastKc = null, stale = false, lastNdvi = null, age = null, rows = [];
    const evSum = (day, type) => (a.events || []).filter(e => e.date === day && e.type === type).reduce((s, e) => s + e.value, 0);
    const yesterday = addDays(a.today, -1);
    for (let day = start.date; day <= yesterday; day = addDays(day, 1)) {
      const i = idx[day]; if (i === undefined) continue;
      const nd = interpNdvi(a.ndvi || [], day);
      stale = nd.ndvi === null || nd.age > crop.stale_days;
      const kc = stale ? crop.kc_fallback : kcFromNdvi(nd.ndvi, crop);
      let rain = a.weather.rain[i] || 0; const farmerRain = evSum(day, 'rain_mm'); if (farmerRain > 0) rain = farmerRain;   // local report beats the grid
      const irrig = evSum(day, 'irrigation_mm');
      const r = step(dr, a.weather.et0[i] || 0, kc, rain, irrig, taw, p); dr = r.dr;
      lastKc = kc; lastNdvi = nd.ndvi; age = nd.age; rows.push({ date: day, dr, rain, et0: a.weather.et0[i] });
    }
    const fwd = [0, 1].reduce((s, k) => { const i = idx[addDays(a.today, k)]; return s + (i === undefined ? 0 : (a.weather.rain[i] || 0)); }, 0);
    let decision, reason;
    if (fwd > 10) { decision = 'WAIT_RAIN'; reason = 'About ' + fwd.toFixed(0) + ' mm of rain is forecast in the next 2 days.'; }
    else if (dr >= raw) { decision = 'IRRIGATE'; reason = 'The root zone has lost ' + dr.toFixed(0) + ' mm; plants start to struggle at ' + raw.toFixed(0) + ' mm.'; }
    else { decision = 'OK'; reason = 'Soil water is fine (' + dr.toFixed(0) + ' mm used of ' + raw.toFixed(0) + ' mm comfortable limit).'; }
    const eff = a.efficiency || 0.88, grossMm = decision === 'IRRIGATE' ? dr / eff : 0, areaM2 = a.areaM2 || 0;
    const litres = grossMm * areaM2;
    const hours = a.flowLph && litres ? litres / a.flowLph : null;
    // days until the comfortable limit is reached at today's use
    const useMm = Math.max(0.1, (lastKc == null ? crop.kc_fallback : lastKc) * (a.weather.et0[idx[a.today]] || a.weather.et0[idx[yesterday]] || 4));
    const daysLeft = decision === 'OK' ? Math.max(0, (raw - dr) / useMm) : 0;
    return { decision, reason, dr, taw, raw, pct: 100 * dr / taw, grossMm, litres, hours, daysLeft, kc: lastKc, ndvi: lastNdvi, ndviAge: age, ndviStale: stale,
             forecastRain2d: fwd, soil, startAssumed: !!start.assumed, needsSetup: !!start.assumed, rows };
  }

  return { centroid, bbox, polygonAreaM2, squareFromArea, pointInPolygon, distToEdges, utmZone, toUTM, farmPixels,
           searchScenes, sceneNdvi, ndviSeries, saxtonRawls, depthWeighted, soilFromSoilGrids, fetchSoil,
           tawMm, ks, effRain, step, kcFromNdvi, interpNdvi, advise, addDays, DEFAULT_CROP };
});
