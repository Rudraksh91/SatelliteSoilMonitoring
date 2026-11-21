// The dashboard's valve crop list (index.html CROP_DB) must be a clean array: a stray ",," makes a hole that crashes open/close valve and the crop search.
const fs = require('fs'), s = fs.readFileSync(__dirname + '/../index.html', 'utf8');
let n = 0, fail = 0; const check = (name, ok) => { n++; console.log((ok ? 'ok   ' : 'FAIL ') + name); if (!ok) fail++; };
const i = s.indexOf('const CROP_DB=['), j = s.indexOf('\n];', i);
const arr = eval(s.slice(i + 'const CROP_DB='.length, j + 2));
check('no holes or empty entries (every index exists)', Object.keys(arr).length === arr.length && arr.every(x => x && typeof x === 'object'));
check('every crop has key, category, label, mm, duration and flow', arr.every(x => x.key && x.cat && x.label && x.mm >= 0 && x.dur >= 0 && x.flow > 0));
check('keys are unique', new Set(arr.map(x => x.key)).size === arr.length);
check('the Indian crops added for farmers are present', ['black_gram', 'tobacco', 'berseem', 'amla', 'aloe_vera', 'ashwagandha', 'kodo_millet'].every(k => arr.some(x => x.key === k)));
check('find() / filter() over the list never throws (what the valve code does)', (() => { try { arr.find(x => x.key === 'nope'); arr.filter(c => c.label.toLowerCase().includes('wheat') || c.cat.toLowerCase().includes('wheat')); return true; } catch (e) { return false; } })());
console.log('\n' + (n - fail) + '/' + n + ' checks passed'); process.exit(fail ? 1 : 0);
