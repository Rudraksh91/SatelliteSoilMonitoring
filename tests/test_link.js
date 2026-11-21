// Tests for farmer/link.js (node tests/test_link.js): the signed envelope must match what the Pi (brain.py verify_command) checks
const nodeCrypto = require('crypto'), L = require('../farmer/link.js');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
(async () => {
  const key = 'a'.repeat(32), sched = { days: { '2026-11-21': [{ zone: 1, start: '06:00', minutes: 30 }], '2026-11-22': [] } };
  const msg = await L.sign(L.scheduleCommand(sched), key, { ts: 1700000000000, nonce: 'abc123' });
  const env = JSON.parse(msg), inner = JSON.parse(env.m);
  check('envelope is {m, sig} with m a JSON string', typeof env.m === 'string' && typeof env.sig === 'string');
  check('signature = HMAC-SHA256(key, m) in hex (what the Pi recomputes)', env.sig === nodeCrypto.createHmac('sha256', key).update(env.m).digest('hex'));
  check('inner carries ts + nonce (replay protection) and the schedule', inner.ts === 1700000000000 && inner.nonce === 'abc123' && inner.schedule.days['2026-11-21'][0].zone === 1);
  check('empty days are kept: they mean "do not irrigate"', Array.isArray(inner.schedule.days['2026-11-22']) && inner.schedule.days['2026-11-22'].length === 0);
  check('a different key gives a different signature', (await L.sign({ x: 1 }, 'b'.repeat(32), { ts: 1, nonce: 'n' })) !== (await L.sign({ x: 1 }, key, { ts: 1, nonce: 'n' })));
  check('nonce is random per command', JSON.parse(JSON.parse(await L.sign({}, key)).m).nonce !== JSON.parse(JSON.parse(await L.sign({}, key)).m).nonce);
  check('controller id is cleaned like the dashboard does', L.cleanId(' My Farm 01! ') === 'my-farm-01');
  check('weak or malformed keys are refused', L.cleanKey('short') === '' && L.cleanKey('x'.repeat(32) + ' ') === 'x'.repeat(32) && L.cleanKey('bad key with spaces!!!!!!') === '');
  require('fs').writeFileSync(process.env.LINK_SAMPLE || '/dev/null', msg);
  console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
})();
