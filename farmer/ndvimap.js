/* NDVI map of a farmer's own farm (one coloured square per 10 m pixel inside the outline).
 * Shared by farmer.html and the dashboard's Analytics page. Needs Leaflet (global L) and AgriCore (farmer/core.js). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriNdviMap = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const RD = ['#a50026', '#d73027', '#f46d43', '#fdae61', '#fee08b', '#ffffbf', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850', '#006837'];
  const WET = ['#8c510a', '#bf812d', '#dfc27d', '#f6e8c3', '#c7eae5', '#80cdc1', '#35978f', '#01665e'];   // NDMI: dry brown -> water-rich teal
  // per index: colour ramp, value range, and where its 10 m grid lives in a scene's grid object
  const INDEX = { ndvi: { ramp: RD, lo: -0.2, hi: 0.8, key: 'v' }, ndmi: { ramp: WET, lo: -0.4, hi: 0.6, key: 'vm' }, ndre: { ramp: RD, lo: 0.0, hi: 0.7, key: 'vr' } };
  function colour(v, index) {                   // v = index*100
    const I = INDEX[index || 'ndvi'], ramp = I.ramp;
    const t = Math.max(0, Math.min(1, (v / 100 - I.lo) / (I.hi - I.lo))) * (ramp.length - 1), i = Math.min(ramp.length - 2, Math.floor(t)), f = t - i;
    const h = x => [1, 3, 5].map(k => parseInt(x.substr(k, 2), 16)), A = h(ramp[i]), B = h(ramp[i + 1]);
    return 'rgb(' + A.map((c, k) => Math.round(c + (B[k] - c) * f)).join(',') + ')';
  }
  const gradient = index => 'linear-gradient(90deg,' + INDEX[index || 'ndvi'].ramp.join(',') + ')';
  // Esri World_Imagery is only photographed to z17-18 over most of rural India; asking for a missing tile returns a grey
  // "Map data not yet available" picture. Ask Esri's tilemap service which zoom really exists at the place being looked at
  // and let Leaflet enlarge that level instead of requesting tiles that do not exist.
  const TILEMAP = 'https://services.arcgisonline.com/arcgis/rest/services/World_Imagery/MapServer/tilemap';
  const SAFE_NATIVE_ZOOM = 17;
  const tileXY = (lat, lng, z) => { const n = Math.pow(2, z); return { x: Math.floor((lng + 180) / 360 * n), y: Math.floor((1 - Math.asinh(Math.tan(lat * Math.PI / 180)) / Math.PI) / 2 * n) }; };
  const zCache = {};
  /** Highest zoom (15..19) at which Esri has a photo tile at (lat, lng). Falls back to 17 (exists nearly everywhere) if the service cannot be reached. */
  function probeMaxZoom(lat, lng, fetchFn) {
    const key = lat.toFixed(2) + ',' + lng.toFixed(2); if (zCache[key]) return zCache[key];
    const f = fetchFn || (typeof fetch !== 'undefined' ? fetch : null);
    const p = (async () => {
      if (!f) return SAFE_NATIVE_ZOOM;
      for (let z = 19; z >= 15; z--) {
        const t = tileXY(lat, lng, z);
        try { const r = await f(TILEMAP + '/' + z + '/' + t.y + '/' + t.x + '/1/1?f=json'); const j = await r.json(); if (j && j.data && j.data[0] === 1) return z; }
        catch (e) { delete zCache[key]; return SAFE_NATIVE_ZOOM; }
      }
      return 15;
    })();
    zCache[key] = p; return p;
  }
  // ── sharp maps on high-density screens (4K, retina) ──
  // Leaflet's detectRetina asks for tiles one zoom level deeper and draws them at half size, so every screen pixel gets a real image pixel.
  // That makes URL zoom = map zoom + 1, so the layer's maxNativeZoom has to be one lower than the deepest zoom that really exists.
  const isRetina = () => typeof window !== 'undefined' && !!(window.L && window.L.Browser && window.L.Browser.retina);
  /** Options for a tile layer whose photos really exist up to zoom `nat`. `retina` is only passed by tests; pages leave it out. */
  function tileOpts(nat, extra, retina) {
    const r = retina === undefined ? isRetina() : retina;
    return Object.assign({ maxNativeZoom: nat - (r ? 1 : 0), maxZoom: 22, detectRetina: true }, extra || {});   // maxZoom 22: Leaflet lowers it by one on retina, and the map itself stops at 21
  }
  /** maxNativeZoom for an existing layer: uses the zoomOffset Leaflet really applied when the layer was built, so a later display change cannot make it request tiles that do not exist. */
  const nativeFor = (layer, nat) => nat - ((layer && layer.options && layer.options.zoomOffset) || 0);
  /** Keep the given Esri tile layers' native zoom at what exists around the map centre (re-checked after every move). */
  function adaptNativeZoom(map, layers, fetchFn) {
    let timer = null;
    const run = () => { const c = map.getCenter(); if (map.getZoom() < 14) return;
      probeMaxZoom(c.lat, c.lng, fetchFn).then(z => layers.forEach(l => { if (l.options.maxNativeZoom !== nativeFor(l, z)) {
        l.options.maxNativeZoom = nativeFor(l, z);
        // remove + add (public API) rebuilds the tile grid for the new native zoom; redraw() alone leaves Leaflet's x-wrap range at the old zoom and requests tiles with wrong x
        const m = l._map; if (m) { m.removeLayer(l); m.addLayer(l); }
      } })); };
    map.on('moveend', () => { clearTimeout(timer); timer = setTimeout(run, 250); });
    run();
  }
  function baseMap(box, L) {
    const map = L.map(box, { zoomControl: false, dragging: false, scrollWheelZoom: false, touchZoom: false, doubleClickZoom: false, boxZoom: false, keyboard: false });
    const sat = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', tileOpts(SAFE_NATIVE_ZOOM, { attribution: 'Imagery © Esri · Sentinel-2' })).addTo(map);
    map._agriSat = sat;
    return map;
  }
  function drawCells(map, g, L, C, index) {
    const arr = g[INDEX[index || 'ndvi'].key]; if (!arr) return;
    for (let r = 0; r < g.rows; r++) for (let c = 0; c < g.cols; c++) {
      const v = arr[r * g.cols + c], fl = g.f[r * g.cols + c];
      if (v === null && fl !== 2) continue;                                   // outside the farm outline
      const x = g.x0 + c * 10, y = g.y0 - r * 10;
      L.rectangle([C.fromUTM(x - 5, y - 5, g.zone), C.fromUTM(x + 5, y + 5, g.zone)],
        { stroke: false, fillColor: fl === 2 ? '#9aa0a6' : colour(v, index), fillOpacity: fl === 2 ? 0.55 : (fl === 1 ? 0.35 : 0.82), interactive: false }).addTo(map);
    }
  }
  function clean(box) { if (box._agriMap) { try { box._agriMap.remove(); } catch (e) {} box._agriMap = null; } }
  /** Draw the scene `idx` of farm.cache.ndvi into element `box`. Returns {scenes, scene, map} (map null when no grid). */
  function render(box, farm, idx, L, C, index) {
    clean(box); index = index || 'ndvi';
    const scenes = ((farm.cache && farm.cache.ndvi) || []).filter(s => s.grid && s.ndvi != null && (index === 'ndvi' || (s.grid[INDEX[index].key] && s[index] != null)));
    if (!scenes.length) return { scenes: [], scene: null, map: null };
    const s = scenes[Math.min(idx || 0, scenes.length - 1)];
    const map = baseMap(box, L);
    drawCells(map, s.grid, L, C, index);
    const poly = L.polygon(farm.latlngs, { color: '#ffd54a', weight: 2.5, fill: false, interactive: false }).addTo(map);
    const fit = () => { map.invalidateSize(); map.fitBounds(poly.getBounds(), { padding: [12, 12] }); };
    fit(); setTimeout(() => { if (box._agriMap === map) fit(); }, 150);
    adaptNativeZoom(map, [map._agriSat]);
    box._agriMap = map;
    return { scenes, scene: s, map, refit: fit };
  }
  const FIELD_COLOURS = ['#ffd54a', '#4fc3f7', '#ff8a65', '#ba68c8', '#81c784', '#f06292', '#a1887f', '#90a4ae'];
  /** Several fields on ONE map: every field's newest NDVI squares + a coloured, labelled outline. Returns {map, drawn}. */
  function renderMulti(box, farms, L, C, labelOf) {
    clean(box);
    const map = baseMap(box, L); let drawn = 0, bounds = null;
    farms.forEach((f, i) => {
      const sc = ((f.cache && f.cache.ndvi) || []).filter(s => s.grid && s.ndvi != null)[0];
      if (sc) { drawCells(map, sc.grid, L, C); drawn++; }
      const col = FIELD_COLOURS[i % FIELD_COLOURS.length];
      const poly = L.polygon(f.latlngs, { color: col, weight: 3, fill: false, dashArray: sc ? null : '6 5', interactive: false }).addTo(map);
      poly.bindTooltip(String(labelOf ? labelOf(f) : f.name), { permanent: true, direction: 'center', className: 'fld-tip' });
      bounds = bounds ? bounds.extend(poly.getBounds()) : poly.getBounds();
    });
    const fit = () => { map.invalidateSize(); if (bounds) map.fitBounds(bounds, { padding: [14, 14], maxZoom: 18 }); };
    fit(); setTimeout(() => { if (box._agriMap === map) fit(); }, 150);
    adaptNativeZoom(map, [map._agriSat]);
    box._agriMap = map;
    return { map, drawn, colours: FIELD_COLOURS };
  }
  return { colour, gradient, render, renderMulti, RD, INDEX, FIELD_COLOURS, probeMaxZoom, adaptNativeZoom, tileXY, SAFE_NATIVE_ZOOM, tileOpts, nativeFor };
});
