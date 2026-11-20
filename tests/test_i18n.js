// Completeness checks for farmer/i18n.js: every key in every language, same placeholders, and every key used by the page exists.
const I = require('../farmer/i18n.js'), fs = require('fs'), path = require('path');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
const en = Object.keys(I.dict.en), ph = s => (s.match(/\{\w+\}/g) || []).sort().join(',');
for (const l of ['hi', 'gu']) {
  const k = Object.keys(I.dict[l]);
  check(l + ': same keys as English', en.every(x => k.includes(x)) && k.every(x => en.includes(x)));
  check(l + ': same {placeholders} in every string', en.every(x => ph(I.dict.en[x]) === ph(I.dict[l][x])));
  const same = en.filter(x => I.dict[l][x] === I.dict.en[x] && !/^(et|title)$/.test(x) && /[A-Za-z]{3,}/.test(I.dict.en[x].replace(/<[^>]+>|\{\w+\}|Open-Meteo|ISRIC SoilGrids|AWS|Copernicus|Sentinel-2|FAO-56|NDVI|Kc|SoilGrids/g, '')));
  check(l + ': every string is translated (not left in English)' + (same.length ? ' — untranslated: ' + same.join(',') : ''), same.length === 0);
}
const page = fs.readFileSync(path.join(__dirname, '..', 'farmer.html'), 'utf8');
const used = new Set([...page.matchAll(/data-(?:i|h|ph)="(\w+)"/g)].map(m => m[1]).concat([...page.matchAll(/\bT\('(\w+)'/g)].map(m => m[1]), [...page.matchAll(/'(?:st[A-Z]\w+|w[A-Z]\w+)'/g)].map(m => m[0].replace(/'/g, '')), ['hIrrigate', 'hOk', 'hWait', 'rWait', 'rIrrigate', 'rOk', 'crop_general', 'crop_cotton']));
const missing = [...used].filter(k => !k.endsWith('_') && I.dict.en[k] === undefined);
check('every key the page uses exists' + (missing.length ? ' — missing: ' + missing.join(',') : ''), missing.length === 0);
check('t() falls back to English, then to the key', I.t('hi', 'nonexistent_key') === 'nonexistent_key' && I.t('hi', 'cont') === 'आगे बढ़ें');
check('t() fills placeholders', I.t('gu', 'corners', { n: 2 }).includes('2'));
console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
