#!/usr/bin/env python3
"""Easy Steps · products + delivery — 9:16 (1080 × 1920), 64 beats at 90 BPM = 42.667 s = 2560 frames.

hook → categories → five real products (swipe, price, sizes / colour / wishlist) → add to cart → ✓ →
«أُضيف إلى السلة» → delivery map from Tayibe to the whole country incl. the West Bank and Jerusalem → order → hook.
Paced for reading: every screen holds 1–3 beats after its last change (one event per beat was too fast to read).
Written in story beats; the file starts 3 beats in (SHIFT) so frame 0 already shows the full title card, and the
big changes (first product, add to cart, map) land on the song's downbeats.
Every motion is a spring track compiled to CSS keyframes (tools/beat_engine.py); the page plays with CSS only.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from beat_engine import ENTER, EXIT, FAST, INST, MORPH, PRESS, Loop, F  # noqa: E402

SHIFT = 3
L = Loop(90, 64, shift=SHIFT)
B = L.B
D = 0.18
CALM = {"w": 22, "z": 0.94}
FLIP = {"w": 44, "z": 1}
SNAP = {"w": 16, "z": 0.9}
CUE = L.cue

# ---------------------------------------------------------------- the shape
ST = {
    "hook":   dict(w=900, h=620, r=64, dk=0, tl=0),
    "cats":   dict(w=900, h=1060, r=64, dk=0, tl=0),
    "prod":   dict(w=900, h=1060, r=64, dk=0, tl=0),
    "loader": dict(w=220, h=220, r=110, dk=1, tl=0),
    "check":  dict(w=240, h=240, r=120, dk=0, tl=1),
    "island": dict(w=760, h=150, r=75, dk=1, tl=0),
    "map":    dict(w=900, h=1060, r=64, dk=0, tl=0),
    "toast":  dict(w=760, h=150, r=75, dk=1, tl=0),
}
SW, SH, SR = L.track("sw", MORPH), L.track("sh", MORPH), L.track("sr", MORPH)
DK, TL = L.track("dk", FLIP, tol=0.002), L.track("tl", FLIP, tol=0.002)
_prev = {"dk": 0, "tl": 0}


def shape(beat, name, bg_delay=0.12):
    s = ST[name]
    SW.to(beat, s["w"]); SH.to(beat, s["h"]); SR.to(beat, s["r"])
    lighter = s["dk"] + s["tl"] < _prev["dk"] + _prev["tl"]
    for k, tr in (("dk", DK), ("tl", TL)):
        if s[k] != _prev[k]:
            tr.to(beat + (bg_delay if lighter else 0.1), s[k])
            _prev[k] = s[k]


# story beats: hook 0 · categories 5 · products 11 · loader 39 · ✓ 40 · island 41 · map 43 · toast 60 · hook 64 (= 0)
for b, n in [(5, "cats"), (11, "prod"), (39, "loader"), (40, "check"), (41, "island"), (43, "map"), (60, "toast"), (64, "hook")]:
    shape(b, n)


def P(name, *pairs):
    return L.presence(name, pairs)


def press(name, beat):
    L.track(name, PRESS, tol=0.002).to(beat, 1).to(beat + 0.16, 0)


def smooth(u):
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------- 1 · hook (0–4)
P("h1", (0 + D, 1), (5, 0)); P("h2", (1, 1), (5, 0)); P("h3", (2, 1, CALM), (5, 0)); press("h3p", 4)
CUE(0 + D, "soft_tick"); CUE(1, "soft_tick"); CUE(2, "pop"); CUE(4, "tap")

# ---------------------------------------------------------------- 2 · categories (5–10)
P("ct", (5 + D, 1), (11, 0))
for i in range(6):
    P(f"k{i}", (5 + D + 0.06 * i, 1, CALM), (11, 0))
    CUE(5 + D + 0.06 * i, "tile", i=i)
TILE = [(665, 260), (235, 260), (665, 560), (235, 560), (665, 860), (235, 860)]     # photo centres, right → left
P("cs", (7, 1, FAST), (11, 0))
L.track("csx", MORPH, tol=0.1).to(7, TILE[0][0], INST).to(9, TILE[1][0], {"w": 24, "z": 0.86}).to(11.5, TILE[0][0], INST)
press("k1p", 10)
CUE(5, "whoosh"); CUE(7, "select"); CUE(9, "select"); CUE(10, "tap")

# ---------------------------------------------------------------- 3 · five products (11–38)
PROD = [  # arrival beat, the beat the next one swipes in
    dict(b=11, end=18), dict(b=18, end=26), dict(b=26, end=31), dict(b=31, end=36), dict(b=36, end=39)]
P("pv", (11 + D, 1), (39, 0))
P("ab", (11 + D + 0.2, 1), (39, 0))
press("abp", 39)
PITCH, DRAG, DRAG_T = 840.0, 240.0, 0.32
SWIPES = [18, 26, 31, 36]                               # the strip moves to product k+1 on these beats
V0 = DRAG * 2 / (DRAG_T * B)                            # finger speed at release (ease-in drag)
RESET = 40.5                                            # the photo is gone by then


def strip(t):
    b = L.story(t)
    x = np.zeros_like(b)
    for k, sb in enumerate(SWIPES):
        s0, nxt = sb - DRAG_T, (SWIPES[k + 1] - DRAG_T if k + 1 < len(SWIPES) else RESET)
        u = (b - s0) / DRAG_T
        drag = k * PITCH + DRAG * u * u
        rel = (k + 1) * PITCH + F((b - sb) * B, DRAG - PITCH, V0, SNAP)
        x = np.where((b >= s0) & (b < sb), drag, x)
        x = np.where((b >= sb) & (b < nxt), rel, x)
    return x                                            # before the first drag and after RESET: 0


L.fn("sx", strip, breaks=[RESET], tol=0.2)


def finger(t):
    b = L.story(t)
    x = np.zeros_like(b)
    for sb in SWIPES:
        u = (b - (sb - DRAG_T)) / DRAG_T
        x = np.where((b >= sb - DRAG_T) & (b < sb), DRAG * u * u, x)
        x = np.where((b >= sb) & (b < sb + 0.5), DRAG, x)
    return x


L.fn("fgx", finger, breaks=[sb + 0.5 for sb in SWIPES], tol=0.2)
fa = L.track("fga", ENTER, tol=0.002)
fp = L.track("fgp", PRESS, tol=0.002)
for sb in SWIPES:
    fa.to(sb - DRAG_T - 0.14, 1, FAST).to(sb + 0.02, 0, EXIT)
    fp.to(sb - DRAG_T - 0.06, 1).to(sb, 0)
    CUE(sb, "swipe")

for i, p in enumerate(PROD):
    P(f"pn{i}", (p["b"] + D, 1), (p["end"], 0))          # category + name
    P(f"pp{i}", (p["b"] + 1, 1, CALM), (p["end"], 0))    # price, struck price, badge
    CUE(p["b"] + 1, "price")
P("op0", (14, 1), (18, 0)); L.track("sz0", FAST, tol=0.002).to(15, 1).to(18, 0, EXIT)          # sizes, tap 44.5
P("op1", (19, 1), (26, 0)); L.track("cox", {"w": 24, "z": 0.86}, tol=0.1).to(21, 250).to(27, 660, INST)   # black → glossy
L.track("acg", {"w": 30, "z": 1}, tol=0.002).to(21.04, 1).to(28, 0, INST)                        # photo → glossy
L.track("hf", CALM, tol=0.002).to(23, 1).to(26, 0, EXIT)                                          # wishlist heart
P("op2", (27, 1), (31, 0))
P("op3", (32, 1), (36, 0))
CUE(11, "whoosh"); CUE(14, "soft_tick"); CUE(15, "tap"); CUE(15.02, "select")
CUE(19.3, "soft_tick"); CUE(21, "tap"); CUE(21.02, "select"); CUE(23, "tap"); CUE(23.03, "heart")
CUE(27.3, "soft_tick"); CUE(32.3, "soft_tick"); CUE(39, "tap")

# ---------------------------------------------------------------- 4 · cart (39–42)
P("ld", (39 + D, 1), (40, 0))
L.track("rg", FAST, tol=0.002).to(39 + D, 0.55).to(39.62, 1).to(41, 0, INST)
P("ck", (40, 1, INST), (41, 0))
L.track("ckd", {"w": 20, "z": 1}, tol=0.002).to(40.04, 1).to(42, 0, INST)
P("il", (41 + D, 1), (43, 0)); P("ilb", (41 + D + 0.12, 1, CALM), (43, 0))
CUE(39.05, "whoosh_small"); CUE(39 + D, "tick"); CUE(39.62, "tick_hi"); CUE(40, "chime_ok"); CUE(41, "whoosh")

# ---------------------------------------------------------------- 5 · delivery map (43–59)
pins = json.loads((HERE / "assets" / "pins.json").read_text())
HUB, CITIES = pins[0], pins[1:]
P("mp", (43 + D, 1), (60, 0))
L.track("md", {"w": 10, "z": 1}, tol=0.002).to(43 + D, 1).to(61, 0, INST)
P("hb", (44, 1, CALM), (60, 0))
ARC0 = 45


def arc_fn(beat):
    return lambda t: np.where(L.story(t) < 61, smooth((L.story(t) - (beat - 0.78)) / 0.78), 0.0)


for j, c in enumerate(CITIES):
    L.fn(f"a{j}", arc_fn(ARC0 + j), breaks=[61], tol=0.002)
    P(f"q{j}", (ARC0 + j, 1, CALM), (60, 0))
    CUE(ARC0 + j, "city", j=j)
for i in range(3):
    P(f"rc{i}", (54 + 0.12 * i, 1, CALM), (60, 0))
    CUE(54 + 0.12 * i, "chip", i=i)
P("fr", (56, 1, CALM), (60, 0))
CUE(43, "whoosh"); CUE(43 + D, "draw", dur=0.8); CUE(44, "pop"); CUE(56, "chime2")

# ---------------------------------------------------------------- 6 · order (59–63)
press("cpr", 59)
P("tt", (60 + D, 1), (64, 0)); P("tti", (60 + D + 0.1, 1, CALM), (64, 0))
CUE(59, "tap"); CUE(60, "whoosh_small"); CUE(60 + D + 0.1, "new_chime"); CUE(64, "whoosh")

cpu = L.track("cpu", {"w": 70, "z": 1}, tol=0.001)
for r in range(0, 64, 4):                                   # on the song's downbeats (real beats)
    cpu.to(r + SHIFT, 1, {"w": 70, "z": 1}).to(r + SHIFT + 0.1, 0, {"w": 13, "z": 1})

TAPS = [(4, 540, 1000), (10, 325, 600), (15, 501, 1155), (21, 340, 1155), (23, 190, 440), (39, 540, 1313), (59, 540, 1500)]
for i, (b, x, y) in enumerate(TAPS):
    L.presence(f"ta{i}", [(b - 0.26, 1, FAST), (b + 0.3, 0, EXIT)])
    press(f"tp{i}", b)


# ---------------------------------------------------------------- map markup (arcs from the hub, pins, labels)
SIDE = {"Haifa": -1, "Nazareth": 1, "Jenin": 1, "Nablus": 1, "Jerusalem": 1, "Hebron": 1, "Beersheba": -1, "Eilat": 1}


def arc_d(a, b):
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    px, py = -dy / n, dx / n
    if px < 0:
        px, py = -px, -py                                   # every arc bulges east
    k = min(0.2 * n, 70)
    cx, cy = (x0 + x1) / 2 + px * k, (y0 + y1) / 2 + py * k
    return f"M{x0:.1f},{y0:.1f} Q{cx:.1f},{cy:.1f} {x1:.1f},{y1:.1f}"


def map_html():
    hx, hy = HUB["xy"]
    out = []
    for j, c in enumerate(CITIES):
        d = arc_d(HUB["xy"], c["xy"])
        out.append(f'<path class="arc" pathLength="1" d="{d}" style="stroke-dashoffset:calc(1 - var(--a{j}));'
                   f'stroke-opacity:clamp(0, calc(var(--a{j}) * 60), 1)"/>')
    svg = "\n".join(out)
    html = [f'<svg class="abs" width="600" height="860" viewBox="0 0 600 860">'
            f'<path class="land" pathLength="1" d="{(HERE / "assets" / "map_path.txt").read_text()}"/>{svg}</svg>']
    for j, c in enumerate(CITIES):
        d = arc_d(HUB["xy"], c["xy"])
        html.append(f'<div class="head" style="offset-path:path(\'{d}\');offset-distance:calc(var(--a{j}) * 100%);'
                    f'opacity:calc(clamp(0, var(--a{j}) * 30, 1) * clamp(0, (1 - var(--a{j})) * 30, 1))"></div>')
        x, y = c["xy"]
        side = SIDE[c["name"]]
        html.append(f'<div class="pin" style="--p:var(--q{j});left:{x:.1f}px;top:{y:.1f}px"></div>')
        pos = f"left:{x + 22:.1f}px" if side > 0 else f"right:{600 - x + 22:.1f}px"
        html.append(f'<div class="plbl t" style="--p:var(--q{j});{pos};top:{y:.1f}px">{c["label"]}</div>')
    html.append(f'<div class="hub" style="left:{hx:.1f}px;top:{hy:.1f}px"><svg class="ico" width="34" height="34" viewBox="0 0 24 24" style="transform:scaleX(-1)">'
                '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/>'
                '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/>'
                '<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/></svg></div>')
    html.append(f'<div class="hlbl t" style="right:{600 - hx + 36:.1f}px;top:{hy:.1f}px">الطيبة</div>')
    html.append(f'<div class="free t fxs" style="--p:var(--fr);right:{600 - hx + 36:.1f}px;top:{hy + 52:.1f}px">توصيل مجاني</div>')
    return "\n".join(html)


if __name__ == "__main__":
    (HERE / "assets" / "scene.css").write_text(L.css())
    (HERE / "assets" / "timing.json").write_text(json.dumps(L.timing()))
    (HERE / "assets" / "map.html").write_text(map_html())
    (HERE / "audio" / "cues.json").write_text(json.dumps({"B": B, "T": L.T, "cues": L.cues}, ensure_ascii=False, indent=1))
    print(f"{len(L.tracks)} tracks, {len(L.cues)} cues, loop {L.T:.4f} s")
