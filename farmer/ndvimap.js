/* NDVI map of a farmer's own farm (one coloured square per 10 m pixel inside the outline).
 * Shared by farmer.html and the dashboard's Analytics page. Needs Leaflet (global L) and AgriCore (farmer/core.js). */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriNdviMap = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const RD = ['#a50026', '#d73027', '#f46d43', '#fdae61', '#fee08b', '#ffffbf', '#d9ef8b', '#a6d96a', '#66bd63', '#1a9850', '#006837'];
  function colour(v) {                          // v = NDVI*100 ; scale -0.2..0.8 like the pipeline's RdYlGn
    const t = Math.max(0, Math.min(1, (v / 100 + 0.2) / 1.0)) * (RD.length - 1), i = Math.min(RD.length - 2, Math.floor(t)), f = t - i;
    const h = x => [1, 3, 5].map(k => parseInt(x.substr(k, 2), 16)), A = h(RD[i]), B = h(RD[i + 1]);
    return 'rgb(' + A.map((c, k) => Math.round(c + (B[k] - c) * f)).join(',') + ')';
  }
  function baseMap(box, L) {
    const map = L.map(box, { zoomControl: false, dragging: false, scrollWheelZoom: false, touchZoom: false, doubleClickZoom: false, boxZoom: false, keyboard: false });
    L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', { maxNativeZoom: 19, maxZoom: 21, attribution: 'Imagery © Esri · Sentinel-2' }).addTo(map);
    return map;
  }
  function drawCells(map, g, L, C) {
    for (let r = 0; r < g.rows; r++) for (let c = 0; c < g.cols; c++) {
      const v = g.v[r * g.cols + c], fl = g.f[r * g.cols + c];
      if (v === null && fl !== 2) continue;                                   // outside the farm outline
      const x = g.x0 + c * 10, y = g.y0 - r * 10;
      L.rectangle([C.fromUTM(x - 5, y - 5, g.zone), C.fromUTM(x + 5, y + 5, g.zone)],
        { stroke: false, fillColor: fl === 2 ? '#9aa0a6' : colour(v), fillOpacity: fl === 2 ? 0.55 : (fl === 1 ? 0.35 : 0.82), interactive: false }).addTo(map);
    }
  }
  function clean(box) { if (box._agriMap) { try { box._agriMap.remove(); } catch (e) {} box._agriMap = null; } }
  /** Draw the scene `idx` of farm.cache.ndvi into element `box`. Returns {scenes, scene, map} (map null when no grid). */
  function render(box, farm, idx, L, C) {
    clean(box);
    const scenes = ((farm.cache && farm.cache.ndvi) || []).filter(s => s.grid && s.ndvi != null);
    if (!scenes.length) return { scenes: [], scene: null, map: null };
    const s = scenes[Math.min(idx || 0, scenes.length - 1)];
    const map = baseMap(box, L);
    drawCells(map, s.grid, L, C);
    const poly = L.polygon(farm.latlngs, { color: '#ffd54a', weight: 2.5, fill: false, interactive: false }).addTo(map);
    const fit = () => { map.invalidateSize(); map.fitBounds(poly.getBounds(), { padding: [12, 12] }); };
    fit(); setTimeout(() => { if (box._agriMap === map) fit(); }, 150);
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
    box._agriMap = map;
    return { map, drawn, colours: FIELD_COLOURS };
  }
  return { colour, render, renderMulti, RD, FIELD_COLOURS };
});
