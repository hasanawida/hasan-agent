# The time model (read before editing a scene)

Every frame is `seek(t)`: a pure function of time. The renderer calls it at arbitrary times (4 subframes per frame for motion blur, any order, any resolution), so nothing may depend on the previous frame.

## Springs and tracks

- `S(t, spec)` is the closed-form step response of a damped spring (`{w, z}`; `z ≥ 1` critically damped). `F(t, x0, v0, spec)` is the free response from a displacement and velocity (used after a drag is released). `INST` is a step that applies **at its own instant** (`t >= 0`) — an earlier version applied it one sample late and drew a black slab for one frame at a hand-off.
- `Track`: a value that changes target many times is the **sum of one spring per change**. `to(beat, value, spec)` adds a change; `build()` computes the deltas; `at(t)` sums `Δ · (S(t − tᵢ) + S(t − tᵢ + T))`. The `+T` term replays every change from the previous loop, and the base value is the **last** target of the loop — so the function is periodic and `seek(0) === seek(T)` pixel for pixel. Consequence: the timeline must be cyclic (the last state equals the first).
- `ColorTrack` = three Tracks (RGB). `presence(pairs)` = a 0/1 Track for content (ENTER spring on 1, EXIT spring on 0).
- Specs used: MORPH `{17, .82}` for shape geometry, CAM `{10, 1}`, TINT `{24, 1}` for colour, ENTER `{21, 1}`, EXIT `{62, 1}`, LEAD `{30, .84}` / TRAIL `{15, .9}` for liquid edges, PRESS `{48, .85}`, cursor `{14, .9}`/`{13, .95}`.

## Two clocks (extended template)

The extended template writes the shopping flow in **flow beats** (one event per flow beat) and plays it at one event per 2 beats from beat `C0 = 20`: `cb(b) = C0 + 2·floor(b) + frac(b)` — offsets inside a beat (click release 0.16, enter delay 0.18) keep their real length. `Track.to()` takes flow beats; `Track.toAbs()` takes real beats (intro, grid, map). `at(b)` converts a flow beat to seconds. Anything computed by scanning time (hover events, carousel item changes) must use `toAbs` with `t / B`. The cue sheet uses the same mapping.

## The shape and the camera

- `shapeTo(beat, state)` pushes w/h/r/cx/cy (MORPH), background (TINT, optionally delayed with `bgAt` so a black↔white flip happens after the content has left) and camera zoom/centre (CAM, zoom in log space).
- The camera zoom is `softmin(springZoom, 0.9·1440 / max(w, h))` — it springs toward each state's zoom but never lets the live shape (including a stretch) leave the frame. The camera also follows half the stretch so a stretched slider stays in frame.
- `ZNOW = z`: `fx()` divides blur by it and the shadow is set per frame in screen pixels; otherwise a 10 px blur becomes a 60 px glow on a 6× zoomed loader.

## Layers and content

- Each state has a layer box of its own size, centred on the live shape (or pinned to its top edge with a numeric offset, e.g. tabs riding up when the chart opens). `place(el, w, h, presence, pinTop, opts)` positions it and applies `fx()` (opacity + blur + slight rise/scale).
- Exits are fast (EXIT), entries wait `D = 0.18` beats — the outgoing text is gone before the incoming one shows.
- The shape has `overflow: hidden`: anything that must sit above the shape (a bubble over a knob) gets clipped — put values inside the knob instead.

## The pill (one element across states)

`#pill` is the playhead/active dot → slider knob → toggle knob → tab indicator → search-row highlight. While attached (`ATTACH0 … ATTACH1`) its rect is computed from the slider value (`knobRect`) or the carousel (`dotRect`), blended on a spring at the hand-off. After `ATTACH1` it's free: `pillTo(beat, x0, x1, y0, y1)` moves the edges with LEAD/TRAIL depending on direction (MORPH when the centre doesn't move). The free pill is smooth-clamped 4 px inside the live shape so a fast leading edge can't poke out. It retires on Enter (the toast is the shape itself).

The white labels over the tab indicator are a second copy of the tab row clipped to the pill rect (`clip-path: inset(... round r)`).

## Drags

- Grab at `G`, release at `R`. While held: `value = v_grab + Δcursor / length` (relative, so no jump at the grab). Past the end: `stretch = rubber(overshoot)` extends the shape (anchored at the far edge) and squashes its height slightly.
- On release: `F(t − R, stretch(R), stretch'(R), BACK)` — springs back from wherever it was, with its velocity. Carousels snap to `round(offset/pitch + bias)` with `F` toward the snap.
- Split a long drag into two cursor legs so the second push lands on a beat — one long leg finishes early and leaves a dead beat.

## Cursor

`cur(beat, x, y)` adds spring moves in world coordinates (x and y on slightly different springs → curved paths); `click(beat)` presses. The cursor is drawn in screen space from its world point. Park it where it won't cover content, keep it inside the frame at every zoom, and start each approach early enough (or with a stiffer spring) that it has landed before it clicks. Hover-driven UI (tooltips, lifting tiles) is derived once by scanning the cursor path at init, debounced (drop dwells < 0.3 beats), then turned into Track events.

## Text

- RTL: set `direction: rtl` per text element, isolate numbers (`.num` with `unicode-bidi: isolate`), mirror directional icons (send, speaker, truck), don't mirror media play.
- Arabic letters join, so typed Arabic is one string per keystroke (the previous letter changes form) revealed from the right with a clip; Hebrew/Latin can be per-letter spans.
- A placeholder disappears the instant the first key is typed (no fade).
- Fonts are inlined; Geist has no Arabic/Hebrew/⌘ glyphs — use IBM Plex Sans Arabic / Heebo for scripts and an SVG ⌘.
