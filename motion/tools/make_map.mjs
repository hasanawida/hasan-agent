// Country outline (Natural Earth 1:10m via the world-atlas package) + route stops, projected into a map card.
//   node make_map.mjs "<Country name>" stops.json <out-dir> [cardW=560] [areaTop=118] [areaH=672]
// stops.json: [{ "name": "Haifa", "ll": [lon, lat], "label": "الشمال" | null }, …] in route order.
// Writes <out-dir>/map_path.txt (SVG path d) and <out-dir>/pins.json ([{name, xy:[x,y], label}] in card px).
// Needs: npm i world-atlas topojson-client (in the motion/ package).
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const require = createRequire(resolve('package.json'));
const topo = require('topojson-client');
const world = require('world-atlas/countries-10m.json');

const [country, stopsFile, outDir, cardW = '560', areaTop = '118', areaH = '672'] = process.argv.slice(2);
const feature = topo.feature(world, world.objects.countries).features.find((f) => f.properties.name === country);
if (!feature) { console.error(`no country named "${country}" in Natural Earth`); process.exit(1); }
const rings = feature.geometry.type === 'Polygon' ? [feature.geometry.coordinates] : feature.geometry.coordinates;

const AREA = { x: 60, y: +areaTop, w: +cardW - 120, h: +areaH };
let lo = [Infinity, Infinity], hi = [-Infinity, -Infinity];
for (const poly of rings) for (const ring of poly) for (const [x, y] of ring) {
  lo = [Math.min(lo[0], x), Math.min(lo[1], y)]; hi = [Math.max(hi[0], x), Math.max(hi[1], y)];
}
const kx = Math.cos(((lo[1] + hi[1]) / 2) * Math.PI / 180);           // equirectangular around the centre
const k = Math.min(AREA.h / (hi[1] - lo[1]), AREA.w / ((hi[0] - lo[0]) * kx));
const ox = AREA.x + (AREA.w - (hi[0] - lo[0]) * kx * k) / 2, oy = AREA.y + (AREA.h - (hi[1] - lo[1]) * k) / 2;
const proj = ([lon, lat]) => [+(ox + (lon - lo[0]) * kx * k).toFixed(2), +(oy + (hi[1] - lat) * k).toFixed(2)];

const d = rings.map((poly) => poly.map((ring) => 'M' + ring.map(proj).map((p) => p.join(',')).join('L') + 'Z').join('')).join('');
writeFileSync(join(outDir, 'map_path.txt'), d);
const stops = JSON.parse(readFileSync(stopsFile, 'utf8')).map((s) => ({ name: s.name, xy: proj(s.ll), label: s.label || null }));
writeFileSync(join(outDir, 'pins.json'), JSON.stringify(stops));
console.log(`${country}: ${d.length} chars of path, ${stops.length} stops → ${outDir}`);
