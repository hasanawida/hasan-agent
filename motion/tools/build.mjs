// Build one self-contained HTML file for a project: inline fonts, images and the timing config.
//   node tools/build.mjs <project-dir>
import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dir = resolve(process.argv[2] || '.');
const proj = JSON.parse(readFileSync(join(dir, 'project.json'), 'utf8'));
const nm = join(root, 'node_modules');

const faces = proj.fonts.map(([family, weight, rel]) => {
  const b64 = readFileSync(join(nm, rel)).toString('base64');
  return `@font-face{font-family:'${family}';font-weight:${weight};font-style:normal;font-display:block;src:url(data:font/woff2;base64,${b64}) format('woff2');}`;
}).join('\n');

const gridPath = join(dir, 'audio', 'grid.json');
const grid = existsSync(gridPath) ? JSON.parse(readFileSync(gridPath, 'utf8')) : { bpm: 120 };
const cfg = { bpm: grid.bpm };

let html = readFileSync(join(dir, 'src', 'scene.html'), 'utf8');
html = html.replace('/*@FONTS@*/', faces);
html = html.replace(/\/\*@CFG@\*\/[\s\S]*?\/\*@END@\*\//, `/*@CFG@*/${JSON.stringify(cfg)}/*@END@*/`);
const mime = { '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.svg': 'image/svg+xml' };
html = html.replace(/@IMG:([\w.\-/]+)@/g, (_, rel) => {
  const p = join(dir, 'assets', rel);
  return `data:${mime[extname(p).toLowerCase()]};base64,${readFileSync(p).toString('base64')}`;
});
const out = join(dir, proj.output);
writeFileSync(out, html);
console.log(`${proj.output}  ${(html.length / 1024).toFixed(0)} KB  bpm=${cfg.bpm}`);
