// Builds the app icon from the same artwork as the welcome screen: dark space, dot-matrix globe turned toward
// central India, tilted orbit ring, satellite with a cyan trail. Writes icons/icon.svg plus PNG sizes.
// Usage (needs the "sharp" package):  node scripts/make_icons.js
const fs = require("fs"), path = require("path");
const OUT = path.join(__dirname, "..", "icons");
const f = n => +n.toFixed(2);

const CX = 256, CY = 262, R = 112, ANG = -0.42, OA = 1.6, OB = 0.42;
function orbit(th) {
  const lx = OA * R * Math.cos(th), ly = OB * R * Math.sin(th);
  return [CX + lx * Math.cos(ANG) - ly * Math.sin(ANG), CY + lx * Math.sin(ANG) + ly * Math.cos(ANG)];
}
function arc(front) {
  let d = "", on = false;
  for (let a = 0; a <= 96; a++) {
    const th = a / 96 * Math.PI * 2, isFront = Math.sin(th) > 0;
    if (isFront === front) { const p = orbit(th); d += (on ? "L" : "M") + f(p[0]) + "," + f(p[1]); on = true; } else on = false;
  }
  return d;
}

// Dot-matrix globe, same point set and land rule as the welcome screen, turned to face the farm region.
const FARM = { lat: 22 * Math.PI / 180, lon: 79 * Math.PI / 180 }, N = 1300, GA = Math.PI * (3 - Math.sqrt(5));
let dots = "";
const land = [], sea = [], near = [];
for (let i = 0; i < N; i++) {
  const yy = 1 - 2 * (i + .5) / N, lat = Math.asin(yy);
  let lon = (GA * i) % (2 * Math.PI); lon = ((lon + Math.PI) % (2 * Math.PI)) - Math.PI;
  const dist = Math.acos(Math.max(-1, Math.min(1, Math.sin(lat) * Math.sin(FARM.lat) + Math.cos(lat) * Math.cos(FARM.lat) * Math.cos(lon - FARM.lon))));
  const n = Math.sin(lon * 2.1 + 1.3) * Math.cos(lat * 2.7) + .6 * Math.sin(lon * 4.3 - lat * 3.1 + 2) + .4 * Math.cos(lon * 7.1 + lat * 5.3);
  const dl = lon - FARM.lon, cosc = Math.sin(FARM.lat) * Math.sin(lat) + Math.cos(FARM.lat) * Math.cos(lat) * Math.cos(dl);
  if (cosc <= 0.04) continue;
  const x = Math.cos(lat) * Math.sin(dl), y = Math.cos(FARM.lat) * Math.sin(lat) - Math.sin(FARM.lat) * Math.cos(lat) * Math.cos(dl);
  const p = { x: CX + R * x, y: CY - R * y, c: cosc };
  (dist < .3 ? near : (n > .15 ? land : sea)).push(p);
}
const circ = (p, r, fill, op) => `<circle cx="${f(p.x)}" cy="${f(p.y)}" r="${f(r)}" fill="${fill}" opacity="${f(op)}"/>`;
sea.forEach(p => dots += circ(p, 1.7, "#2bff7a", 0.2 + 0.14 * p.c));
land.forEach(p => dots += circ(p, 3.0 * (0.55 + 0.45 * p.c), "#00ff41", 0.5 + 0.5 * p.c));
near.forEach(p => dots += circ(p, 3.6 * (0.6 + 0.4 * p.c), "#7df4ff", 0.75 + 0.25 * p.c));

// Stars
let stars = "", seed = 7;
const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
for (let i = 0; i < 46; i++) {
  const x = rnd() * 512, y = rnd() * 512;
  if (Math.hypot(x - CX, y - CY) < 150) continue;
  const q = rnd(), col = q < .15 ? "#00ff41" : q < .3 ? "#00eefc" : "#ffffff";
  stars += `<circle cx="${f(x)}" cy="${f(y)}" r="${f(.7 + rnd() * 1.3)}" fill="${col}" opacity="${f(.35 + rnd() * .55)}"/>`;
}

// Satellite on the front of the ring, with a cyan trail behind it
const TH = 0.62, sp = orbit(TH);
let trail = "";
for (let k = 1; k <= 22; k++) {
  const a = TH - k * .055, p = orbit(a);
  trail += `<circle cx="${f(p[0])}" cy="${f(p[1])}" r="${f(Math.max(.8, 4.2 - k * .17))}" fill="#00eefc" opacity="${f(.65 * (1 - k / 22))}"/>`;
}
const sat = `<g transform="translate(${f(sp[0])},${f(sp[1])}) rotate(${f(ANG * 180 / Math.PI)}) scale(2.5)">
  <rect x="-18" y="-3.5" width="12" height="7" fill="#1a3a8a" stroke="#7db8ff" stroke-width=".6"/>
  <rect x="6" y="-3.5" width="12" height="7" fill="#1a3a8a" stroke="#7db8ff" stroke-width=".6"/>
  <rect x="-5" y="-5.5" width="10" height="11" fill="#dfe8ff"/><rect x="-2" y="-2" width="4" height="4" fill="#00eefc"/></g>`;

function svg(maskable) {
  const rx = maskable ? 0 : 112;
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs>
  <radialGradient id="bg" cx="50%" cy="38%" r="75%"><stop offset="0" stop-color="#0b1a14"/><stop offset=".6" stop-color="#060a09"/><stop offset="1" stop-color="#030504"/></radialGradient>
  <radialGradient id="au1" cx="22%" cy="16%" r="55%"><stop offset="0" stop-color="#00ff41" stop-opacity=".22"/><stop offset="1" stop-color="#00ff41" stop-opacity="0"/></radialGradient>
  <radialGradient id="au2" cx="80%" cy="30%" r="50%"><stop offset="0" stop-color="#00eefc" stop-opacity=".16"/><stop offset="1" stop-color="#00eefc" stop-opacity="0"/></radialGradient>
  <radialGradient id="globe" cx="40%" cy="35%" r="70%"><stop offset="0" stop-color="#0c3324"/><stop offset="1" stop-color="#030f0a"/></radialGradient>
  <radialGradient id="halo" cx="50%" cy="50%" r="50%"><stop offset=".72" stop-color="#00eefc" stop-opacity="0"/><stop offset=".9" stop-color="#00eefc" stop-opacity=".38"/><stop offset="1" stop-color="#00eefc" stop-opacity="0"/></radialGradient>
  <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="3"/></filter>
</defs>
<rect width="512" height="512" rx="${rx}" fill="url(#bg)"/>
<rect width="512" height="512" rx="${rx}" fill="url(#au1)"/><rect width="512" height="512" rx="${rx}" fill="url(#au2)"/>
${stars}
<circle cx="${CX}" cy="${CY}" r="${R * 1.32}" fill="url(#halo)"/>
<path d="${arc(false)}" fill="none" stroke="#7db8ff" stroke-width="2.4" opacity=".28" stroke-linecap="round"/>
<circle cx="${CX}" cy="${CY}" r="${R}" fill="url(#globe)"/>
<circle cx="${CX}" cy="${CY}" r="${R}" fill="none" stroke="#00eefc" stroke-width="3" opacity=".75" filter="url(#soft)"/>
<circle cx="${CX}" cy="${CY}" r="${R}" fill="none" stroke="#00eefc" stroke-width="1.4" opacity=".7"/>
${dots}
<path d="${arc(true)}" fill="none" stroke="#7db8ff" stroke-width="3" opacity=".85" stroke-linecap="round"/>
${trail}
${sat}
</svg>`;
}

fs.writeFileSync(path.join(OUT, "icon.svg"), svg(false));
fs.writeFileSync(path.join(OUT, "icon-maskable.svg"), svg(true));
(async () => {
  const sharp = require("sharp");
  const jobs = [["icon.svg", 192, "icon-192.png"], ["icon.svg", 512, "icon-512.png"], ["icon-maskable.svg", 512, "icon-maskable-512.png"],
    ["icon-maskable.svg", 180, "apple-touch-icon.png"], ["icon.svg", 32, "favicon-32.png"]];
  for (const [src, size, name] of jobs) {
    await sharp(path.join(OUT, src), { density: 384 }).resize(size, size).png().toFile(path.join(OUT, name));
    console.log("wrote", name);
  }
})().catch(e => { console.error(e); process.exit(1); });
