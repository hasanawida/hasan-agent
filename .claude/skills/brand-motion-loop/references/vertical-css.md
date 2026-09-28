# Vertical story mode (9:16, CSS keyframes)

For Reels / TikTok / Shorts / WhatsApp status ads for a business (clinic, shop, restaurant). Same springs and the same one-shape idea, but the page plays with **CSS only**: `scripts/beat_engine.py` computes every motion in Python and compiles it to `@keyframes`. Worked example: `motion/easy-steps/` (Easy Steps, orthopedic institute: foot pressure scan → services → new online booking in 4 steps → confirmation → health funds; 32 beats at 90 BPM).

## Frame and layout

- `project.json`: `"size": [1080, 1920]`. `render.mjs` reads it (`--scale=2` → 2160 × 3840).
- Safe zones: keep nothing important in the top 120 px or bottom 240 px (platform UI covers them).
- Three zones: logo at the top (y ≈ 230), the one shape in the middle (centre y ≈ 870, up to 900 × 1060), a persistent CTA pill (y ≈ 1500) with URL · phone under it (y ≈ 1624). The CTA pulses on every downbeat (3 %, fast attack, soft decay) and gets tapped near the end.
- Touches instead of a cursor: a 96 px translucent ring that appears ~0.26 beat before the tap, presses on the beat, and fades.

## Tempo presets (the loop must be a whole number of frames)

`python3 scripts/beat_engine.py --presets 32` lists them. At 60 fps:

| Industry | Range | Whole-frame tempos | Feel |
|---|---|---|---|
| Clinic / health | 75–95 | 75, 80, 90 | critically damped springs, calm palette (navy/teal/mint), trust badges, health funds, booking |
| Food & beverage | 95–115 | 96, 100 | warm palette, dishes, delivery, opening hours |
| Shop / e-commerce | 118–132 | 120, 128 | high contrast, struck prices, discount badges, product carousel; "snap" = z ≈ 0.74 (~3 % overshoot), never elastic |

`Loop(bpm, beats)` refuses a tempo whose loop isn't whole frames (e.g. 95 BPM × 32 beats = 1212.6 frames → the MP4 would jump at the seam). `analyze_song.py` conforms any song to the chosen tempo (`"target_bpm"` and `"tempo_range"` in track.json).

## The compiler

- `L = Loop(90, 32)`; `L.track(name, spec).to(beat, value[, spec])` (sum of springs, periodic), `L.presence(name, [(beat, 1|0[, spec])])`, `L.fn(name, f, breaks)` for anything custom (a scan sweep, a drag), `L.cue(beat, kind, …)` for the sound sheet.
- `L.css()` → one `@property --name { syntax: '<number>' }` + `@keyframes k-name` per track, all animated on `#stage` and inherited. Motion segments are split at extrema and eased with `linear()` sampled from the true curve; holds are plain keyframes; INST steps get a 0.1 ms ramp.
- In the HTML every style is `calc()` of those variables: `width: calc(var(--sw) * 1px)`, `opacity: var(--p)` with `style="--p: var(--h1)"`, colours with `color-mix(in srgb, var(--ink) calc(var(--dk) * 100%), #fff)`.
- Typed text is one string per keystroke (Arabic letters change shape as you type; a clip also cuts digits in half). Right-anchor typed LTR numbers inside an RTL field.
- The page fits any window with CSS alone: `scale: min(tan(atan2(100vh, 1920px)), tan(atan2(100vw, 1080px)))`, reset to 1 by a media query at exactly 1080 × 1920 so the render is pixel-exact.
- Rendering: a tiny script exposes `seek(t)` = pause every `document.getAnimations()` and set `currentTime` — exact frames for motion-blur subframes. (Screenshotting a running CSS animation doesn't work: captures take ~100 ms, so the video comes out at the wrong speed and doesn't loop.)

## Pitfalls met

- White ↔ dark shape flips: use a fast tint spring ({44, 1}) that starts after the content has left, otherwise the shape sits grey for a beat.
- A logo on a white JPEG/PNG ground shows as a box on a tinted canvas — key the white out (`colorkey=0xFFFFFF:0.10:0.08`) instead of `multiply`.
- A marker label placed over a footprint's toes hid them — put callouts where nothing else is drawn.
