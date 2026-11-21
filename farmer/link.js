/* AgriSat controller link — sends the autopilot's plan to the farm's Raspberry Pi over the same signed MQTT channel the dashboard uses.
 *   sign(obj, key)      -> '{"m":"<inner JSON>","sig":"<hex HMAC-SHA256 of m>"}'  (the Pi's verify_command() checks exactly this)
 *   scheduleCommand(s)  -> the "schedule" command body for a plan from AgriAutopilot.buildSchedule()
 *   connect()/send()    -> browser only; needs mqtt.js (window.mqtt). Listens on agrisat/<id>/data so the farmer can SEE the Pi's acknowledgement.
 * The command key lives only in this browser's localStorage (same key the dashboard uses); it is never in the repository or the page. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.AgriLink = factory();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';
  const KEY_STORE = 'agrisat_cmd_key', ID_STORE = 'agrisat_farm_id', BROKER = 'wss://broker.hivemq.com:8884/mqtt';
  const hex = buf => Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
  const cleanId = s => String(s || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/^-+|-+$/g, '').slice(0, 64);
  const cleanKey = s => { s = String(s || '').trim(); return /^[0-9A-Za-z_-]{16,128}$/.test(s) ? s : ''; };

  async function sign(obj, key, o) {
    o = o || {};
    const cr = o.crypto || (typeof crypto !== 'undefined' ? crypto : require('crypto').webcrypto);
    const nonce = o.nonce || hex(cr.getRandomValues(new Uint8Array(8)));
    const inner = JSON.stringify(Object.assign({}, obj, { ts: o.ts || Date.now(), nonce }));
    const enc = new TextEncoder();
    const k = await cr.subtle.importKey('raw', enc.encode(key), { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
    return JSON.stringify({ m: inner, sig: hex(await cr.subtle.sign('HMAC', k, enc.encode(inner))) });
  }
  const scheduleCommand = s => ({ schedule: { days: s.days } });

  // ── browser side ──
  const st = { client: null, connected: false, id: '', ack: null, listeners: [] };
  function store(k, v) { try { v ? localStorage.setItem(k, v) : localStorage.removeItem(k); } catch (e) {} }
  function load(k) { try { return localStorage.getItem(k) || ''; } catch (e) { return ''; } }
  const getKey = () => cleanKey(load(KEY_STORE)), getId = () => cleanId(load(ID_STORE));
  const setKey = v => { const k = cleanKey(v); store(KEY_STORE, k); return !!k; };
  const setId = v => { const i = cleanId(v); store(ID_STORE, i); return i; };
  const ready = () => !!(getKey() && getId());
  // one-click setup, same as the dashboard:  <site>/farmer.html#cmdkey=<key>&farm=<id>
  function readHash() {
    try {
      const h = location.hash || '', k = /(?:^#|&)cmdkey=([0-9A-Za-z_-]{16,128})/.exec(h), f = /(?:^#|&)farm=([0-9A-Za-z_-]{1,64})/.exec(h);
      if (k) setKey(k[1]); if (f) setId(f[1]);
      if (k || f) history.replaceState(null, '', location.pathname + location.search);
    } catch (e) {}
  }
  function onAck(cb) { st.listeners.push(cb); if (st.ack) cb(st.ack); }
  function connect() {
    const id = getId();
    if (!id || typeof mqtt === 'undefined') return false;
    if (st.client && st.id === id) return true;
    if (st.client) { try { st.client.end(true); } catch (e) {} st.client = null; }
    st.id = id; st.connected = false;
    const c = st.client = mqtt.connect(BROKER, { clientId: 'agrisat-farmer-' + Math.random().toString(16).slice(2, 9), clean: true, reconnectPeriod: 6000, connectTimeout: 15000, keepalive: 30 });
    c.on('connect', () => { st.connected = true; c.subscribe('agrisat/' + id + '/data', { qos: 0 }); });
    c.on('close', () => { st.connected = false; });
    c.on('message', (t, payload) => {
      try { const d = JSON.parse(payload.toString()); if (d && d.source === 'pi_brain') { st.ack = { at: Date.now(), schedule: d.schedule || null, auto: d.auto_mode }; st.listeners.forEach(f => f(st.ack)); } } catch (e) {}
    });
    return true;
  }
  const waitConnected = (ms) => new Promise(res => { const t0 = Date.now(); (function w() { if (st.connected) return res(true); if (Date.now() - t0 > ms) return res(false); setTimeout(w, 200); })(); });
  /** Sign and publish one command. Resolves true when it was handed to the broker. */
  async function send(obj) {
    const key = getKey(), id = getId();
    if (!key || !id || !connect() || !(await waitConnected(15000))) return false;
    const msg = await sign(obj, key);
    return new Promise(res => st.client.publish('agrisat/' + id + '/cmd', msg, { qos: 0, retain: false }, err => res(!err)));
  }
  /** Wait until the Pi's own status shows it holds a plan issued after `since` (ms). */
  const waitAck = (since, ms) => new Promise(res => { const t0 = Date.now(); (function w() {
    const a = st.ack; if (a && a.schedule && a.schedule.issued && a.schedule.issued >= since - 2000) return res(a);
    if (Date.now() - t0 > ms) return res(null); setTimeout(w, 500); })(); });

  return { sign, scheduleCommand, cleanId, cleanKey, getKey, getId, setKey, setId, ready, readHash, connect, send, onAck, waitAck, state: st, BROKER };
});
