# State catalogue

World px, origin = frame centre; 1440² frame. Zoom = the camera's target (it is also clamped so the live shape fits). Pick 8–12 and map them to what the product really does.

| State | Size (w×h, r) | Zoom | Colour | Content | Typical event / cursor | Sound |
|---|---|---|---|---|---|---|
| Brand card | 560×220, 40 | 2.2 | white | logo, «Shop with X» title, tagline, URL pill | URL types itself (per-char clip), hover, click opens the next state | key taps per char, click |
| Product grid | 760×640, 36 | 1.62 | white | title + 6 tiles (photo, name, price, struck old price) | cursor browses neighbour → neighbour (never across a tile), tile lifts; click picks one | tick per hover, click |
| Button | 320–330×88, 44 | 3.0 | ink | icon + CTA («Upload», «Add to cart») | click → morph | press + release |
| Loader | 96×96, 48 | 6.4 | ink | ring, progress steps on beats (42 % → 100 %) | cursor drifts nearby | tick per step |
| Check | 108×108, 54 | 6.0 | accent | stroke check drawing (dashoffset) | — | two-note chime in key |
| Dynamic island | 380–400×76, 38 | 2.9 | ink | status text + icon/waveform/badge | — | whoosh |
| Player / product card | 540–560×320–480, 40–44 | 1.68–1.9 | ink or white | title, meta, progress/controls or photo carousel, price, stepper | play→pause morph or + quantity; drag scrub / swipe carousel (two pushes); release | click, scrub ticks, detents, snap |
| Slider | 460–480×92–96, 46 | 2.3–2.45 | white or ink | label, track, knob (value inside the knob if a bubble would be clipped) | grab, push, pull past max → rubber stretch, release springs back | ticks per step, tension tone, thwip |
| Toggle | 176×100, 50 | 5.6 | white → accent | knob only | click on the beat; knob edges lead/trail | double click |
| Tabs | 660×96, 48 | 1.8 | white | 3 labels; pill = indicator; white copy clipped to the pill | click, click (liquid indicator) | clicks |
| Chart | 720×560, 44 | 1.62 | white | tabs pinned at top (+20 offset), title, number counting up, line (fill follows the tip, dots land as the tip passes) or bars growing staggered | hover → tooltip (width on its own spring), glide → next point | draw sweep / per-bar ticks, hover ticks |
| Command palette | 660×360 → 229 → 165, 30 | 1.8–1.94 | white | input (search icon on the row-icon column, ⌘K keycap), 4 rows with icons; highlight = pill | ⌘K press, type 1–2 keys filtering 4 → 2 → 1, Enter | keys, enter |
| Toast | text width + ~110 × 88, 44 | 2.4–3.4 | ink | accent disc with check + message (hug the text) | — | chime in key |
| Delivery map | 560×900, 40 | 1.44 | white | «Delivery everywhere» title, country outline drawing itself, dotted route, van badge driving stop to stop, stops lighting with region labels, «fast» subtitle | the van lands on a stop every 2 beats | outline sweep, a pentatonic note per stop |

## Chains that worked

- **SaaS / app (7 bars, one event per beat):** button → loader → check → island → player (play/pause, scrub) → volume slider (overdrag) → toggle → tabs → chart (+ tooltip) → ⌘K (type, filter, enter) → toast → button.
- **Shop (24 bars):** brand card + URL → product grid (browse, pick) → add-to-cart button → loader → check → «added» island → cart card (+ quantity, swipe photos) → age/size slider (overdrag) → gift-wrap toggle → categories tabs → «skills/benefits» chart → search → toast → delivery map → brand card.

## Causes for morphs (every change should have one)

A click on something inside the outgoing state (speaker icon → volume, age chip → age slider, ✓ confirm → toggle, site link → products, product tile → add-to-cart), a drag release, a key (⌘K, Enter), or the natural consequence of the previous click (toggle on → tabs appear, tab chosen → its chart opens, toast → dismiss).
