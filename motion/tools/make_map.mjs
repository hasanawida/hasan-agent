// Country outline (Natural Earth 1:10m via the world-atlas package) + route stops, projected into a map card.
//   node make_map.mjs "<Country name>[+<Other>]" stops.json <out-dir> [cardW=560] [areaTop=118] [areaH=672] [--drop-bbox=…]
// stops.json: [{ "name": "Haifa", "ll": [lon, lat], "label": "الشمال" | null }, …] in route order.
// Writes <out-dir>/map_path.txt (SVG path d) and <out-dir>/pins.json ([{name, xy:[x,y], label}] in card px).
// Needs: npm i world-atlas topojson-client (in the motion/ package).
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';

const require = createRequire(resolve('package.json'));
const topo = require('topojson-client');
const world = require('world-atlas/countries-10m.json');

const pos = process.argv.slice(2).filter((a) => !a.startsWith('--'));
const [country, stopsFile, outDir, cardW = '560', areaTop = '118', areaH = '672'] = pos;
// several areas as one shape: "Israel+Palestine" is merged (shared borders removed) into a single service area;
// --drop-bbox=minLon,minLat,maxLon,maxLat drops any polygon whose centre lies inside (e.g. an area not served)
const dropArg = process.argv.find((a) => a.startsWith('--drop-bbox='));
const drop = dropArg ? dropArg.split('=')[1].split(',').map(Number) : null;
const centre = (ring) => ring.reduce((c, [x, y]) => [c[0] + x / ring.length, c[1] + y / ring.length], [0, 0]);
const geoms = country.split('+').map((name) => {
  const g = world.objects.countries.geometries.find((q) => q.properties.name === name);
  if (!g) { console.error(`no country named "${name}" in Natural Earth`); process.exit(1); }
  if (!drop || g.type !== 'MultiPolygon') return g;
  const polys = topo.feature(world, g).geometry.coordinates;
  const keep = polys.map((poly) => { const [x, y] = centre(poly[0]); return !(x > drop[0] && y > drop[1] && x < drop[2] && y < drop[3]); });
  return { ...g, arcs: g.arcs.filter((_, i) => keep[i]) };
});
const merged = geoms.length > 1 || drop ? topo.merge(world, geoms) : topo.feature(world, geoms[0]).geometry;
const rings = merged.type === 'Polygon' ? [merged.coordinates] : merged.coordinates;

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
