# Music and UI sound

## Picking the song

- Source: Mixkit (`https://mixkit.co/free-stock-music/…`, direct MP3s at `https://assets.mixkit.co/music/<id>/<id>.mp3`). The Mixkit Stock Music Free License allows commercial use without attribution; don't redistribute the raw MP3 (download it at build time, keep it out of git).
- Aim for 116–124 BPM with a constant DAW grid; `analyze_song.py` conforms it to exactly 120 BPM with `atempo` (pitch preserved; ±3 % is inaudible).
- **What loops matters more than tempo.** Check the track's harmonic cycle against your bar count: a track that alternates chords every 2 bars can't loop cleanly at 7 bars (bar 7 → bar 1 repeats a chord); a one-chord groove loops at any length; an 8-bar chord cycle loops perfectly at 8, 16 or 24 bars (tile the 8-bar loop). Compare the bar after the window with the window's first bar (chroma/mel distance) — `analyze_song.py` ranks windows by that.
- Minimal, confident, instrumental fits product UI; melodic/positive fits kids/family shops. You can't listen: say you chose by measurement and ask the user to audition.

## Beat analysis pitfalls (all hit in practice)

- House bass sits on the offbeats and hats on the "and": a low-band onset *or* a click-band onset alone can lock the phase half a beat off. The kick is the one hit with a low thump **and** a broadband click at once — use the geometric mean of the 30–150 Hz and 160–2500 Hz onset envelopes.
- Long analysis windows pull onset peaks early (~23 ms at 2048/22 kHz). Use a 512-sample window for timing.
- Downbeat: novelty-based guesses nearly tie between phases. DAW exports start on bar 1 — if the first sound sits on a grid beat, that's a downbeat; phrase changes (8-bar intro → drop) confirm it.
- Include the beat nearest 0 s when building the grid (an off-by-one here chose the wrong phase).
- Verify on the conformed audio by cross-correlating each beat's low band with the average kick (template): sub-ms precise. A backbeat snare can bias alternate beats by +15–20 ms in the low band — the attack check (averaged waveform) is the ground truth.

## UI sounds (`sfx.py`)

- Everything is synthesized (clicks, ticks, keys, whooshes, toggles, thwip, chimes, draw sweep, a tension tone that follows the stretch curve) — nothing to license.
- `Mixer.place(sound, t)` measures the sound's peak (1 ms envelope) and puts it on `t`; for multi-note chimes pass `first_ms` so the first note's attack is the anchor.
- Put clicks exactly on the click beat, whooshes ~45 ms after a morph starts (its fastest moment), per-step ticks from the scene's exported curves (`render.mjs cues`: drag values, stretch, hover changes), and a key sound on the same instant the letter appears (a 50 ms audio lead is noticeable).
- Tune tonal accents to the song: estimate pitch classes of the loop (FFT → chroma) and use notes from its chord/scale (e.g. D6→A6 in D minor; a pentatonic run for a sequence of arrivals).
- Levels: music ~−17 dB RMS; clicks −13…−19 dBFS peak; ticks −23…−30; whooshes −26…−29; chimes −20…−23; soft limiter at −1 dBFS. Loop seam: equal-power crossfade of what follows the window into its start (120 ms); sound tails wrap around the loop point.
