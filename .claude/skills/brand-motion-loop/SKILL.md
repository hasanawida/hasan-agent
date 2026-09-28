---
name: brand-motion-loop
description: Make a brand identity motion video («فيديو هوية») for a website, app or shop — one UI shape that morphs through the product's real screens (button → loader → check → island → cards, sliders, toggle, tabs, chart, search, toast, product grid, delivery map…), driven by a cursor with real clicks and drags, cut to a 120 BPM beat grid with a royalty-free song and synthesized UI sounds, rendered as a seamless MP4 loop (square 1440/2160, or a vertical 9:16 story ad for Reels/TikTok/WhatsApp status; 60 fps, motion blur). Use this whenever someone gives a URL and wants a video about it, or asks for a promo / identity / motion / reel video of their site or store, a Dribbble-style UI animation, or says things like «اعمل فيديو هوية», «فيديو موشن لموقعي», «سوّي فيديو للمتجر», «فيديو إعلان للتطبيق», «ستوري/ريلز لمصلحتي», «إعلان للعيادة» — even if they never say "motion" or "loop".
---

# Brand motion loop («فيديو هوية»)

One element — `#shape` — morphs between the product's UI states. Nothing is ever cut: size, radius and colour spring from state to state while the content inside swaps with a short blur, and a cursor causes every change. The camera zooms so each state fills the square frame. The last frame is the first frame, so the video loops forever. Everything is computed from time inside `seek(t)` (no CSS transitions, no timers, no state between frames), so any frame can be rendered at any resolution and the loop is exact.

Two finished examples live in this repo's `motion/` folder (Al-Bayan: 7 bars/14 s, Hebrew; Aya Shop: 24 bars/48 s, Arabic with a brand intro, product grid and delivery map). Their scenes are bundled here as templates.

## What's in this skill

| Path | Use |
|---|---|
| `scripts/build.mjs` | Inline fonts, images (`@IMG:file@`), text/JSON (`@TEXT:file@`) and the BPM into one self-contained HTML |
| `scripts/render.mjs` | Playwright renderer: `beats`, `stills`, `loopcheck`, `cues`, `video`, `mux`; `--scale=1.5` for 2160, `--part=k/n` for parallel segments |
| `scripts/analyze_song.py` | numpy beat grid: tempo, kick phase, downbeat, window, conform to exactly 120 BPM, per-beat verification |
| `scripts/sfx.py` | Synthesized UI sound palette + `Mixer` (peak placement, seamless music loop, tiling) |
| `scripts/site_scan.mjs` | Scan a site: copy, lang/dir, fonts, colours, CSS variables, images, logo, screenshots |
| `scripts/make_map.mjs` | Country outline (Natural Earth 1:10m) + route stops for a delivery-map scene |
| `scripts/beat_engine.py` | Beat grid + whole-frame tempo presets per industry; compiles spring tracks to CSS `@keyframes` (vertical story mode) |
| `assets/templates/compact-28-beats.html` | Scene template: 28 beats, one event per beat (button → … → toast → button) |
| `assets/templates/extended-96-beats.html` | Scene template: 96 beats — brand intro with website, product grid, the flow at half pace, delivery map |
| `assets/templates/cues-*.py`, `project.json` | Matching cue sheets and project config |
| `references/engine.md` | How the time model works (springs, tracks, flow mapping, drags, pill, camera) — read before editing a scene |
| `references/states.md` | Catalogue of states with geometry, zoom, cursor and sound patterns |
| `references/audio.md` | Song choice, beat analysis pitfalls, sound design |
| `references/qa.md` | The checklist of problems every scene has had so far — run it before the full render |
| `references/vertical-css.md` | 9:16 story ads for businesses (clinic / food / shop presets, safe zones, CTA), CSS-only playback |

## Workflow

### 0 · Set up (once per repo)

```bash
mkdir -p motion/tools && cp <skill>/scripts/* motion/tools/
cd motion && npm init -y >/dev/null && npm pkg set type=module
npm i playwright-core@1.56.1 geist @fontsource/ibm-plex-sans-arabic @fontsource/heebo world-atlas topojson-client
pip install numpy scipy imageio-ffmpeg        # ffmpeg comes from imageio-ffmpeg
```
Each video is a project folder `motion/<name>/` with `src/scene.html`, `project.json`, `assets/`, `audio/` (`track.json`, `make_audio.py`). Chromium: use the preinstalled one (`/opt/pw-browsers/...`); don't download browsers.

In a cloud session the site and Mixkit may be blocked by the network policy — say which host is blocked and that the user can allow it in the environment's network settings. Never disable TLS checks; `site_scan.mjs` trusts only the session proxy's CA key.

### 1 · Intake — ask before building

Ask in the user's language, briefly:
1. **The site/product** (URL) and what the video is for (WhatsApp/Instagram/site hero).
2. **8–12 UI states** — or offer a chain built from the site (see `references/states.md`).
3. **Colour:** black & white + one accent (default: the brand's own accent, sampled from its CSS/logo), on a light warm-gray canvas.
4. **Language:** match the site (Hebrew/Arabic are RTL — bars fill from the right, tabs run right → left). Watch for mixed signals (e.g. "Hebrew" requested while writing Arabic) and confirm.
5. **Song:** a royalty-free track near 120 BPM (Mixkit Free License allows commercial use without attribution), or offer to pick one.
6. **Length:** 7 bars (14 s, one event per beat, very dense) or longer (e.g. 24 bars / 48 s with an intro, more products and an outro — clients often find 14 s "too fast").

If the user delegates ("اعمل اشي مرتب", "you decide"), choose sensible defaults, say what you chose, and keep going.

### 2 · Research the site

```bash
node motion/tools/site_scan.mjs https://example.com motion/<name>/assets/scan
```
Read `site.json` and the screenshots. Take real copy (buttons, product names, prices, taglines), the brand colour, fonts, logo and product photos. Prefer the brand's own products; photos on white grounds work best (blend them into the slide with `mix-blend-mode: multiply`, level near-white to pure white with ffmpeg `colorlevels`). Don't invent features the product doesn't have; sample data (chart values) is fine but say so.

### 3 · Propose the beat grid — before writing code

120 BPM → 0.5 s per beat, 4 beats per bar. Show a table: `bar.beat | time | state / event | cursor action | sound`. Every beat should carry something (a morph, a click, a drag push, a key, a tooltip); for longer videos, a calmer "one event per 2 beats" flow is fine. Every morph needs a visible cause (a click, a drag release, a key) unless it's the natural consequence of the previous click. Get a yes, then build.

### 4 · Song → beat grid

Put `track.json` in `motion/<name>/audio/` (`id, title, artist, mp3_url, license, window_start_s|null, bars`) and run:
```bash
python3 motion/tools/analyze_song.py motion/<name>/audio
```
It writes `grid.json` and `music_120.wav` (the window, time-stretched to exactly 120 BPM, starting on a downbeat, with a tail for the loop crossfade). Check `grid.json`: `kick_attack_vs_grid_ms` within ±3 ms, `kick_xcorr_vs_grid_ms` median near 0. Read `references/audio.md` for choosing a track whose harmony loops at your bar count (this matters more than tempo).

### 5 · Build the scene

Copy the closest template to `motion/<name>/src/scene.html`, `project.json` next to it, then edit **states (`ST`)**, **the timeline (shapeTo / cur / click / presence / pillTo)** and **the layer DOM** for the new product. Read `references/engine.md` first — the time model has a few rules that make or break the loop. Build and look:
```bash
node motion/tools/build.mjs motion/<name>
node motion/tools/render.mjs motion/<name> loopcheck        # must print frame0_equals_frameT: true
node motion/tools/render.mjs motion/<name> beats 0.12      # 60 ms after each beat — is the hit visible?
node motion/tools/render.mjs motion/<name> beats 0.9       # settled — is everything readable?
```
View `out/beats_sheet.png` and full-size frames. Open `<name>.html?t=6.2` in a browser for any moment.

### 6 · Sounds

```bash
node motion/tools/render.mjs motion/<name> cues            # exports drag/hover/stretch timing from the scene itself
python3 motion/<name>/audio/make_audio.py                  # cue sheet → mix.wav
```
Start from the matching `cues-*.py`. Every sound's measured peak lands on its cue; whooshes peak ~45 ms after a morph starts; tonal accents are tuned to the song's key (see `references/audio.md`).

### 7 · QA before the full render

Go through `references/qa.md` on the beat sheets. For a thorough pass, spawn independent reviewers with distinct lenses (legibility, language/RTL, code rules, beat timing, design) over the frames + source, and verify each finding before fixing — this caught real bugs every time (black check on a coloured circle, a one-frame slab at a hand-off, the cursor leaving the frame, dead beats, muddy colour crossings).

### 8 · Render and deliver

```bash
node motion/tools/render.mjs motion/<name> video && node motion/tools/render.mjs motion/<name> mux          # 1440²
# 2160² master, three segments in parallel (~0.6 s per subframe per process):
for k in 1 2 3; do node motion/tools/render.mjs motion/<name> video --scale=1.5 --part=$k/3 & done; wait
node motion/tools/render.mjs motion/<name> mux --scale=1.5
```
Verify with ffmpeg (duration = bars × 2 s, 60 fps, audio present, decodes without errors), sample frames into a contact sheet and look, then send the file. Tell the user: **WhatsApp recompresses anything sent as a video — send it as a Document to keep full quality.** 2160² is the useful ceiling for a square video on phones; 8K doesn't survive any platform and phones can't play it.

Commit the project (sources, built HTML, final MP4, beat sheet) — not the raw song MP3 (the licence doesn't allow redistributing the track itself) or intermediate WAVs/PNGs.

## Vertical story ads (9:16)

For Reels / TikTok / WhatsApp status ads for a business, use the vertical mode: 1080 × 1920, safe zones (top 120 px, bottom 240 px), logo → one morphing shape → pulsing CTA with URL and phone, taps instead of a cursor, and a tempo from the industry preset (clinic 75/80/90, food 96/100, shop 120/128 — the loop must be whole frames). The timeline is written in Python with the same springs and compiled to CSS keyframes, so the HTML plays with CSS only. Read `references/vertical-css.md`; worked example `motion/easy-steps/`.

## Rules that keep it Dribbble-level

- Springs only, damping ≥ 0.8 (overshoot ≤ 1.5 %). No bouncy easing, particle bursts, glows, gradients on UI chrome, or heavy shadows.
- One icon system: 24-grid stroke icons at stroke 2, scaled with their box. Logos are either on that weight or filled.
- The toggle knob and tab indicator move with a fast leading edge and a slow trailing edge (liquid stretch).
- Drags are direct manipulation: while held the value comes from the cursor; on release it springs from where it was, with its velocity.
- Text that swaps inside a morphing shape exits fast and enters late, so two texts never overlap.
- Colour flips (black ↔ white) never cross at the same moment as another colour change, and a coloured knob flips almost instantly — otherwise frames go muddy grey.
- Never `will-change` on anything the camera scales; blur and shadow are sized in screen pixels (divide by zoom).
- The cursor lands before it clicks, never leaves the frame, and every morph has a cause.
