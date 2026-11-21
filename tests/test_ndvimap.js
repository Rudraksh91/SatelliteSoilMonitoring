// Tests for the map helpers in farmer/ndvimap.js (node tests/test_ndvimap.js): satellite zoom probing, index colours
const M = require('../farmer/ndvimap.js');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
(async () => {
  const t = M.tileXY(21.6277, 73.5854, 18);
  check('tile x/y of a known place at z18 (Esri tilemap probe above gave 184655 / 114935)', t.x === 184655 && t.y === 114935);
  const mk = (avail, calls) => async (url) => { const z = +/tilemap\/(\d+)\//.exec(url)[1]; calls.push(z); return { json: async () => ({ data: [avail.includes(z) ? 1 : 0] }) }; };
  let calls = [];
  check('photos stop at z18 here: the probe picks 18 (not the missing z19 that shows "Map data not yet available")', (await M.probeMaxZoom(21.6, 73.5, mk([15, 16, 17, 18], calls))) === 18 && calls[0] === 19);
  calls = [];
  check('photos stop at z16: picks 16', (await M.probeMaxZoom(26.9, 70.9, mk([15, 16], calls))) === 16);
  check('the answer is cached per place (no second request)', (calls.length = 0, await M.probeMaxZoom(26.9, 70.9, mk([15, 16], calls)), calls.length === 0));
  check('service unreachable: safe z17, and the failure is not cached', (await M.probeMaxZoom(10.1, 77.1, async () => { throw new Error('offline'); })) === 17 && (await M.probeMaxZoom(10.1, 77.1, mk([15, 16, 17, 18, 19], []))) === 19);
  check('index colours: NDMI dry is brown, wet is teal; NDVI low is red, high is green', M.colour(-40, 'ndmi') === 'rgb(140,81,10)' && M.colour(60, 'ndmi') === 'rgb(1,102,94)' && M.colour(-20, 'ndvi') === 'rgb(165,0,38)' && M.colour(80, 'ndvi') === 'rgb(0,104,55)');
  // ── sharp tiles on 4K / retina screens ──
  const std = M.tileOpts(17, { attribution: 'x' }, false), ret = M.tileOpts(17, { attribution: 'x' }, true);
  check('normal screen: native zoom is the deepest real zoom (17), retina detection requested', std.maxNativeZoom === 17 && std.detectRetina === true && std.attribution === 'x');
  check('retina screen: native zoom is one lower (16), because Leaflet then asks for tiles one zoom deeper (16 + 1 = 17)', ret.maxNativeZoom === 16);
  check('layer maxZoom is 22 so Leaflet\'s retina "minus one" still lets the map reach zoom 21', std.maxZoom === 22 && ret.maxZoom === 22);
  // what Leaflet really does with these options: URL zoom = min(map zoom, maxNativeZoom) + zoomOffset
  const urlZoom = (o, offset, mapZoom) => Math.min(mapZoom, o.maxNativeZoom) + offset;
  check('retina: even at map zoom 21 the requested tile zoom never exceeds the real 17 (no grey "Map data not yet available")', [10, 16, 17, 18, 21].every(z => urlZoom(ret, 1, z) <= 17));
  check('retina: at map zoom 16 the sharper z17 tiles are used (one level deeper than a normal screen)', urlZoom(ret, 1, 16) === 17 && urlZoom(std, 0, 16) === 16);
  check('nativeFor follows the offset Leaflet really applied (retina layer: 18 -> 17, normal layer: 18 -> 18)', M.nativeFor({ options: { zoomOffset: 1 } }, 18) === 17 && M.nativeFor({ options: {} }, 18) === 18 && M.nativeFor(null, 18) === 18);
  // adaptNativeZoom keeps the offset when the probe raises the native zoom
  const lay = { options: { maxNativeZoom: 16, zoomOffset: 1 }, _map: null };
  const fakeMap = { _h: {}, getCenter: () => ({ lat: 21.6, lng: 73.5 }), getZoom: () => 16, on(ev, fn) { this._h[ev] = fn; } };
  M.adaptNativeZoom(fakeMap, [lay], async (url) => { const z = +/tilemap\/(\d+)\//.exec(url)[1]; return { json: async () => ({ data: [z <= 18 ? 1 : 0] }) }; });
  await new Promise(r => setTimeout(r, 50));
  check('probe finds real photos up to z18: a retina layer is set to 17, so it requests z18 and never z19', lay.options.maxNativeZoom === 17);
  console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
})();
