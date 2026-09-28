// Country outline (Natural Earth 1:10m via world-atlas) + delivery stops, projected into the map card.
//   node aya-loop/assets/make_map.mjs   → map_path.txt, pins.json
import { createRequire } from 'node:module';
import { writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const topo = require('topojson-client');
const world = require('world-atlas/countries-10m.json');
const here = dirname(fileURLToPath(import.meta.url));

const country = topo.feature(world, world.objects.countries).features.find((f) => f.properties.name === 'Israel');
const rings = country.geometry.type === 'Polygon' ? [country.geometry.coordinates] : country.geometry.coordinates;

// equirectangular around the country's centre, fitted into the card's map area (local px of a 560 × 900 card)
const AREA = { x: 60, y: 118, w: 440, h: 672 };
let lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
for (const poly of rings) for (const ring of poly) for (const [x, y] of ring) {
  lo = [Math.min(lo[0], x), Math.min(lo[1], y)]; hi = [Math.max(hi[0], x), Math.max(hi[1], y)];
}
const lat0 = (lo[1] + hi[1]) / 2, kx = Math.cos(lat0 * Math.PI / 180);
const k = Math.min(AREA.h / (hi[1] - lo[1]), AREA.w / ((hi[0] - lo[0]) * kx));
const ox = AREA.x + (AREA.w - (hi[0] - lo[0]) * kx * k) / 2, oy = AREA.y + (AREA.h - (hi[1] - lo[1]) * k) / 2;
const proj = ([lon, lat]) => [+(ox + (lon - lo[0]) * kx * k).toFixed(2), +(oy + (hi[1] - lat) * k).toFixed(2)];

const d = rings.map((poly) => poly.map((ring) => 'M' + ring.map(proj).map((p) => p.join(',')).join('L') + 'Z').join('')).join('');
writeFileSync(join(here, 'map_path.txt'), d);

// route north → south; a few region labels
const stops = [
  { name: 'Kiryat Shmona', ll: [35.57, 33.21], label: 'الشمال' },
  { name: 'Nazareth', ll: [35.30, 32.70] },
  { name: 'Haifa', ll: [34.99, 32.79] },
  { name: 'Umm al-Fahm', ll: [35.15, 32.52] },
  { name: 'Tel Aviv', ll: [34.78, 32.08], label: 'المركز' },
  { name: 'Beersheba', ll: [34.79, 31.25], label: 'الجنوب' },
  { name: 'Eilat', ll: [34.95, 29.56] },
].map((s) => ({ ...s, xy: proj(s.ll) }));
writeFileSync(join(here, 'pins.json'), JSON.stringify(stops.map(({ name, xy, label }) => ({ name, xy, label: label || null }))));
console.log('map', d.length, 'chars; bbox lon', lo[0].toFixed(2), hi[0].toFixed(2), 'lat', lo[1].toFixed(2), hi[1].toFixed(2), '; stops', stops.map((s) => s.xy.join(',')).join(' | '));
