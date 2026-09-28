// Scan a website for a brand motion loop: copy, language/direction, fonts, colours, product images, logo.
//   node site_scan.mjs <url> <out-dir>
// Writes <out-dir>/site.json, home.png (viewport), home_full.png, and downloads images to <out-dir>/img/.
//
// Behind an HTTPS-intercepting proxy (e.g. a Claude Code cloud session), Chromium does not read the system
// CA bundle. If /root/.ccr/agent-proxy-ca.crt exists, only that CA's key is trusted via
// --ignore-certificate-errors-spki-list (scoped trust, TLS verification stays on for everything else).
import { chromium } from 'playwright-core';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const [url, outDir = 'site-scan'] = process.argv.slice(2);
if (!url) { console.error('usage: node site_scan.mjs <url> <out-dir>'); process.exit(1); }
mkdirSync(join(outDir, 'img'), { recursive: true });

const args = [];
const ca = '/root/.ccr/agent-proxy-ca.crt';
if (existsSync(ca)) {
  const spki = execFileSync('sh', ['-c',
    `openssl x509 -in ${ca} -pubkey -noout | openssl pkey -pubin -outform der | openssl dgst -sha256 -binary | base64`]).toString().trim();
  args.push(`--ignore-certificate-errors-spki-list=${spki}`);
}
const exe = ['/opt/pw-browsers/chromium-1194/chrome-linux/chrome'].find(existsSync);
const browser = await chromium.launch({ executablePath: exe, args });
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 }).catch((e) => console.error('nav:', e.message));
await page.waitForTimeout(2500);
await page.screenshot({ path: join(outDir, 'home.png') });
await page.screenshot({ path: join(outDir, 'home_full.png'), fullPage: true });

const info = await page.evaluate(() => {
  const cs = (el) => getComputedStyle(el);
  const colors = {};
  for (const el of [...document.querySelectorAll('*')].slice(0, 5000)) {
    const s = cs(el);
    for (const k of ['color', 'backgroundColor', 'borderTopColor']) {
      const v = s[k];
      if (v && v !== 'rgba(0, 0, 0, 0)') colors[`${k}:${v}`] = (colors[`${k}:${v}`] || 0) + 1;
    }
  }
  const fonts = {};
  for (const el of document.querySelectorAll('h1,h2,h3,p,a,button,span,li')) fonts[cs(el).fontFamily] = (fonts[cs(el).fontFamily] || 0) + 1;
  const imgs = [...document.images].map((i) => ({
    src: i.currentSrc || i.src, alt: i.alt, w: i.naturalWidth, h: i.naturalHeight,
    link: i.closest('a') ? i.closest('a').getAttribute('href') : null,
    inHeader: !!i.closest('header, nav'),
  })).filter((i) => i.w > 60);
  const vars = {};
  for (const sheet of [...document.styleSheets]) {
    try { for (const r of sheet.cssRules) if (r.selectorText === ':root') for (const p of r.style) if (p.startsWith('--')) vars[p] = r.style.getPropertyValue(p).trim(); } catch (e) { /* cross-origin sheet */ }
  }
  return {
    url: location.href, title: document.title, lang: document.documentElement.lang,
    dir: document.documentElement.dir || cs(document.body).direction,
    description: (document.querySelector('meta[name="description"]') || {}).content || '',
    headings: [...document.querySelectorAll('h1,h2,h3')].map((h) => h.innerText.trim()).filter(Boolean).slice(0, 40),
    buttons: [...document.querySelectorAll('button, a.btn, a[class*="btn"], [role="button"]')].map((b) => b.innerText.trim()).filter(Boolean).slice(0, 40),
    links: [...document.querySelectorAll('a')].map((a) => `${a.innerText.trim().replace(/\s+/g, ' ')} -> ${a.getAttribute('href')}`).filter((s) => s.length > 6).slice(0, 120),
    text: document.body.innerText.slice(0, 12000),
    cssVars: vars,
    fonts: Object.entries(fonts).sort((a, b) => b[1] - a[1]).slice(0, 8),
    colors: Object.entries(colors).sort((a, b) => b[1] - a[1]).slice(0, 24),
    images: imgs,
    logo: (imgs.find((i) => i.inHeader) || {}).src || null,
  };
});
await browser.close();

// download images with curl (it reads the system CA bundle, so it works behind the proxy too)
info.images.forEach((im, i) => {
  const ext = (im.src.split('?')[0].match(/\.(png|jpe?g|webp|svg|gif)$/i) || ['', 'jpg'])[1].toLowerCase();
  const file = join(outDir, 'img', `${String(i).padStart(2, '0')}.${ext}`);
  try { execFileSync('curl', ['-sSL', '-m', '30', '-A', 'Mozilla/5.0', '-o', file, im.src]); im.file = file; } catch (e) { im.file = null; }
});
writeFileSync(join(outDir, 'site.json'), JSON.stringify(info, null, 1));
console.log(`${info.title || url}  lang=${info.lang} dir=${info.dir}  images=${info.images.length}  → ${join(outDir, 'site.json')}`);
