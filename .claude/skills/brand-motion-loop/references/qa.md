# QA checklist (run on the beat sheets before the full render)

Render `beats 0.12` (the hit) and `beats 0.9` (settled), plus `stills` at any suspicious moment, and look at full-size frames — contact sheets hide 1-frame problems. Every item below happened in a real scene.

**Loop**
- `loopcheck`: `frame0_equals_frameT: true`, cursor position and velocity equal at the seam.
- The cursor approaches the first click early and is nearly still when it presses.

**Beats**
- Something visibly changes on every beat (compare each hit frame with the previous settled frame). A single long drag leg finishes early → split it so the second push lands on the beat.
- Hover changes land on beats (debounce pass-through hovers; route the cursor neighbour → neighbour).
- Long draws (a chart line) finish inside their own beat.

**Framing**
- The live shape never leaves the frame during a morph (small → wide states are the danger; the zoom clamp handles it).
- The cursor stays in frame during overdrags (the camera follows the stretch).
- Small states (loader, check, toggle) fill enough of the frame.

**Colour**
- No muddy grey: black ↔ white flips wait for the content to leave; knob/highlight colour flips are near-instant; two colour changes don't cross at the same moment.
- Round-capped strokes at length 0 draw a dot — hide them until they start drawing.
- CSS classes beat SVG presentation attributes (`.ico { stroke: currentColor }` turned a white check black) — set `color` on the container.

**Content**
- Exit before entry: two texts never overlap in the same place (tooltip label swaps too — give the tooltip width its own spring).
- The pill never pokes out of the shape; a highlight that turns into a toast doesn't leave a second slab — retire it.
- Content that must sit above a knob inside a short shape gets clipped — put it inside the knob.
- Toasts hug their text; tab rows don't jam against a card's top edge.
- Icons share one stroke weight (a logo glyph at 2.6× stroke looked wrong); ⌘ drawn as an icon.
- Language: RTL order of mixed text/numbers/currency, geresh in Hebrew day letters, a native speaker's wording. Arabic badges like «خصم 25%» need a gap in flex.

**Motion blur**
- Blur and shadow are screen-constant (divide by zoom), otherwise small zoomed states glow.

A parallel review with independent lenses (legibility, language/RTL, code rules, beat timing, design), each finding verified before fixing, found real issues every time — worth it before the long render.
