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
  console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
})();
