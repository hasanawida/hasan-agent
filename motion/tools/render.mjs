// Render a project's scene with Playwright.
//   node tools/render.mjs <project> beats [offset]   → one PNG per beat (+ contact sheet) in out/beats
//   node tools/render.mjs <project> stills 0 3.25 …  → PNGs at given times (seconds) in out/stills
//   node tools/render.mjs <project> loopcheck        → seek(0) vs seek(T): pixels, cursor position and speed
//   node tools/render.mjs <project> cues             → audio/cues.json from the scene's pure functions
//   node tools/render.mjs <project> video            → 60 fps, 4 subframes per frame blended with tmix
//   node tools/render.mjs <project> mux              → out/<name>.mp4 = silent video + audio/mix.wav
import { chromium } from 'playwright-core';
import { spawn, execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

const here = resolve(process.argv[2] || '.');
const proj = JSON.parse(readFileSync(join(here, 'project.json'), 'utf8'));
const out = join(here, 'out');
mkdirSync(out, { recursive: true });
const FFMPEG = execFileSync('python3', ['-c', 'import imageio_ffmpeg;print(imageio_ffmpeg.get_ffmpeg_exe())']).toString().trim();
const FPS = 60, SUB = 4, SHUTTER = 0.5;          // 180° shutter

async function open() {
  const browser = await chromium.launch({
    executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome',
    args: ['--font-render-hinting=none', '--disable-lcd-text', '--force-color-profile=srgb'],
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1440 }, deviceScaleFactor: 1 });
  await page.goto(pathToFileURL(join(here, proj.output)).href);
  await page.evaluate(() => window.__ready);
  const timing = await page.evaluate(() => window.TIMING);
  const cdp = await page.context().newCDPSession(page);
  const shot = async (t) => {
    await page.evaluate((tt) => window.seek(tt), t);
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', optimizeForSpeed: true, captureBeyondViewport: false });
    return Buffer.from(data, 'base64');
  };
  return { browser, page, timing, shot };
}

const mode = process.argv[3] || 'beats';

if (mode === 'beats') {
  const { browser, timing, shot, page } = await open();
  const dir = join(out, 'beats'); mkdirSync(dir, { recursive: true });
  const offset = parseFloat(process.argv[4] || '0.2');   // fraction of a beat after the hit
  for (let k = 0; k < timing.BEATS; k++) {
    const bar = Math.floor(k / 4) + 1, bt = (k % 4) + 1;
    writeFileSync(join(dir, `b${String(k).padStart(2, '0')}_${bar}.${bt}.png`), await shot((k + offset) * timing.B));
  }
  console.log('debug', JSON.stringify(await page.evaluate(() => window.DEBUG)));
  await browser.close();
  execFileSync(FFMPEG, ['-y', '-loglevel', 'error', '-pattern_type', 'glob', '-i', join(dir, 'b*.png'),
    '-vf', `scale=360:360,tile=${timing.BEATS % 7 ? 8 : 7}x${Math.ceil(timing.BEATS / (timing.BEATS % 7 ? 8 : 7))}:padding=6:color=0xC9C5BC`,
    '-frames:v', '1', join(out, 'beats_sheet.png')]);
  console.log('beats →', dir);
} else if (mode === 'stills') {
  const { browser, shot } = await open();
  const dir = join(out, 'stills'); mkdirSync(dir, { recursive: true });
  for (const a of process.argv.slice(4)) writeFileSync(join(dir, `t_${a}.png`), await shot(parseFloat(a)));
  await browser.close();
} else if (mode === 'loopcheck') {
  const { browser, timing, shot, page } = await open();
  const a = await shot(0), b = await shot(timing.T);
  const c = await shot(1 / FPS), d = await shot(timing.T + 1 / FPS);
  // cursor position + speed at the seam
  const seam = await page.evaluate((T) => {
    const pos = (t) => { window.seek(t); const m = new DOMMatrix(getComputedStyle(document.getElementById('cursor')).transform); return [m.m41, m.m42]; };
    const e = 1 / 240;
    return { p0: pos(0), pT: pos(T), v0: [(pos(e)[0] - pos(-e)[0]) / (2 * e), (pos(e)[1] - pos(-e)[1]) / (2 * e)],
      vT: [(pos(T + e)[0] - pos(T - e)[0]) / (2 * e), (pos(T + e)[1] - pos(T - e)[1]) / (2 * e)] };
  }, timing.T);
  await browser.close();
  console.log(JSON.stringify({ frame0_equals_frameT: a.equals(b), next_equal: c.equals(d), seam }));
} else if (mode === 'cues') {
  const { browser, page } = await open();
  const cues = await page.evaluate(() => window.CUES());
  writeFileSync(join(here, 'audio', 'cues.json'), JSON.stringify(cues));
  await browser.close();
  console.log('cues →', join(here, 'audio', 'cues.json'), 'hover', JSON.stringify(cues.hover));
} else if (mode === 'video') {
  const { browser, timing, shot } = await open();
  const frames = Math.round(timing.T * FPS);
  const silent = join(out, 'video_silent.mp4');
  const ff = spawn(FFMPEG, ['-y', '-loglevel', 'error',
    '-f', 'image2pipe', '-framerate', String(FPS * SUB), '-c:v', 'png', '-i', '-',
    '-vf', `tmix=frames=${SUB}:weights='1 1 1 1',select='eq(mod(n\\,${SUB})\\,${SUB - 1})',setpts=N/${FPS}/TB`,
    '-r', String(FPS), '-c:v', 'libx264', '-preset', 'slow', '-crf', '12', '-pix_fmt', 'yuv420p',
    '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709', '-movflags', '+faststart', silent],
    { stdio: ['pipe', 'inherit', 'inherit'] });
  const t0 = Date.now();
  for (let f = 0; f < frames; f++) {
    for (let j = 0; j < SUB; j++) {
      const t = f / FPS + ((j + 0.5) / SUB - 0.5) * (SHUTTER / FPS);
      const buf = await shot(t);
      if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
    }
    if (f % 60 === 0) console.log(`frame ${f}/${frames}  ${((Date.now() - t0) / 1000).toFixed(0)}s`);
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  await browser.close();
  console.log('silent video →', silent, `${frames} frames`);
} else if (mode === 'mux') {
  const name = proj.output.replace(/\.html$/, '.mp4');
  execFileSync(FFMPEG, ['-y', '-loglevel', 'error', '-i', join(out, 'video_silent.mp4'), '-i', join(here, 'audio', 'mix.wav'),
    '-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '320k', '-ar', '48000', '-shortest',
    '-movflags', '+faststart', join(out, name)]);
  console.log('video →', join(out, name));
}
