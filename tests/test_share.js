// Offline tests for farmer/share.js (WhatsApp share text and link). Run: node tests/test_share.js
const S = require('../farmer/share.js');
let n = 0, fail = 0;
function check(name, cond) { n++; console.log((cond ? 'ok   ' : 'FAIL ') + name); if (!cond) fail++; }

check('plain strips the app\'s bold tags and entities', S.plain('<b>Apply about 12 mm</b> = <b>3,400 litres</b>&nbsp;over the farm &amp; more') === 'Apply about 12 mm = 3,400 litres over the farm & more');
check('plain survives null / undefined', S.plain(null) === '' && S.plain(undefined) === '');
const gu = S.whatsappText({ title: 'મારું ખેતર · 2026-11-21', head: 'આજે પિયત આપો', lines: ['માટીનું પાણી ઘટ્યું છે.', '<b>લગભગ 12 મિમી</b> પાણી આપો.'], note: 'અંદાજ, ગેરંટી નથી.', url: 'https://example.org/farmer.html' });
check('layout: bold title, bold headline, blank line, details, italic note, link', gu.split('\n').length === 8 && gu.startsWith('*મારું ખેતર · 2026-11-21*\n*આજે પિયત આપો*\n\n') && gu.indexOf('_અંદાજ, ગેરંટી નથી._') > 0 && gu.endsWith('https://example.org/farmer.html'));
check('Gujarati text is kept as is (no mangling)', gu.indexOf('આજે પિયત આપો') > 0 && gu.indexOf('લગભગ 12 મિમી પાણી આપો.') > 0);
check('HTML never reaches the message', gu.indexOf('<') < 0);
check('formatting characters inside a field name cannot break the layout', S.whatsappText({ title: 'My_farm_*1*', head: 'x' }).split('\n')[0] === '*My farm 1*');
check('empty parts are skipped, not printed as blanks', S.whatsappText({ title: 'A', head: '', lines: ['', null], note: '' }) === '*A*');
const long = S.whatsappText({ title: 'T', head: 'H', lines: [('word ').repeat(600)], note: 'n' });
check('very long text is cut to the limit and ends with an ellipsis', long.length <= S.MAX_LEN && long.endsWith('…'));

const u = S.whatsappUrl('નમસ્તે & hello\nline 2');
check('no phone: wa.me opens the contact picker', u.startsWith('https://wa.me/?text='));
check('text is URL-encoded (newline, ampersand, Gujarati)', u.indexOf('%0A') > 0 && u.indexOf('%26') > 0 && decodeURIComponent(u.split('text=')[1]) === 'નમસ્તે & hello\nline 2');
check('phone is reduced to digits and used', S.whatsappUrl('hi', '+91 98765-43210').startsWith('https://wa.me/919876543210?text='));
check('a number that is too short or too long is ignored', S.whatsappUrl('hi', '123').startsWith('https://wa.me/?text=') && S.whatsappUrl('hi', '1'.repeat(16)).startsWith('https://wa.me/?text='));
console.log(`\n${n - fail}/${n} checks passed`);
process.exit(fail ? 1 : 0);
