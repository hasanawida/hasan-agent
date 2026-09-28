#!/usr/bin/env python3
"""Easy Steps · products + delivery — 9:16 (1080 × 1920), 40 beats at 90 BPM = 26.667 s = 1600 frames.

hook → categories → five real products (swipe, price, sizes / colour / wishlist) → add to cart → ✓ →
«أُضيف إلى السلة» → delivery map from Tayibe to the whole country incl. the West Bank and Jerusalem → order → hook.
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

L = Loop(90, 40)
B = L.B
D = 0.18
CALM = {"w": 22, "z": 0.94}
FLIP = {"w": 44, "z": 1}
SNAP = {"w": 16, "z": 0.9}
CUE = L.cue
ORIGIN = (90, 340)                   # top-left of a 900 × 1060 layer centred on the shape (540, 870)

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


for b, n in [(4, "cats"), (8, "prod"), (22, "loader"), (23, "check"), (24, "island"), (25, "map"), (38, "toast"), (39, "hook")]:
    shape(b, n)


def P(name, *pairs):
    return L.presence(name, pairs)


def press(name, beat):
    L.track(name, PRESS, tol=0.002).to(beat, 1).to(beat + 0.16, 0)


def smooth(u):
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


# ---------------------------------------------------------------- 1 · hook (0–3)
P("h1", (0, 1), (4, 0)); P("h2", (1, 1), (4, 0)); P("h3", (2, 1, CALM), (4, 0)); press("h3p", 3)
CUE(0, "soft_tick"); CUE(1, "soft_tick"); CUE(2, "pop"); CUE(3, "tap")

# ---------------------------------------------------------------- 2 · categories (4–7)
P("ct", (4 + D, 1), (8, 0))
for i in range(6):
    P(f"k{i}", (4 + D + 0.06 * i, 1, CALM), (8, 0))
    CUE(4 + D + 0.06 * i, "tile", i=i)
TILE = [(665, 260), (235, 260), (665, 560), (235, 560), (665, 860), (235, 860)]     # photo centres, right → left
P("cs", (5, 1, FAST), (8, 0))
L.track("csx", MORPH, tol=0.1).to(5, TILE[0][0], INST).to(6, TILE[1][0], {"w": 24, "z": 0.86}).to(8.5, TILE[0][0], INST)
press("k1p", 7)
CUE(4, "whoosh"); CUE(5, "select"); CUE(6, "select"); CUE(7, "tap")

# ---------------------------------------------------------------- 3 · five products (8–21)
PROD = [  # beat of arrival, number of beats
    dict(b=8, n=4), dict(b=12, n=4), dict(b=16, n=2), dict(b=18, n=2), dict(b=20, n=2)]
P("pv", (8 + D, 1), (22, 0))
P("ab", (8 + D + 0.2, 1), (22, 0))
press("abp", 22)
PITCH, DRAG, DRAG_T = 840.0, 240.0, 0.32
SWIPES = [12, 16, 18, 20]                               # the strip moves to product k+1 on these beats
V0 = DRAG * 2 / (DRAG_T * B)                            # finger speed at release (ease-in drag)


def strip(t):
    b = t / B
    x = np.zeros_like(b)
    for k, sb in enumerate(SWIPES):
        s0, nxt = sb - DRAG_T, (SWIPES[k + 1] - DRAG_T if k + 1 < len(SWIPES) else 23.5)
        u = (b - s0) / DRAG_T
        drag = k * PITCH + DRAG * u * u
        rel = (k + 1) * PITCH + F((b - sb) * B, DRAG - PITCH, V0, SNAP)
        x = np.where((b >= s0) & (b < sb), drag, x)
        x = np.where((b >= sb) & (b < nxt), rel, x)
    return x                                            # before the first drag and after 23.5 (hidden): 0


L.fn("sx", strip, breaks=[23.5], tol=0.2)


def finger(t):
    b = t / B
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
    b, n = p["b"], p["n"]
    P(f"pn{i}", (b + D, 1), (b + n, 0))                  # category + name
    P(f"pp{i}", (b + 1, 1, CALM), (b + n, 0))            # price, struck price, badge
    CUE(b + 1, "price")
P("op0", (10, 1), (12, 0)); L.track("sz0", FAST, tol=0.002).to(11, 1).to(12, 0, EXIT)          # sizes, tap 44.5
P("op1", (13, 1), (16, 0)); L.track("cox", {"w": 24, "z": 0.86}, tol=0.1).to(14, 250).to(16.5, 660, INST)   # colours: black → glossy
L.track("acg", {"w": 30, "z": 1}, tol=0.002).to(14.04, 1).to(17, 0, INST)                         # photo → glossy
L.track("hf", CALM, tol=0.002).to(15, 1).to(16, 0, EXIT)                                          # wishlist heart
P("op2", (17, 1), (18, 0))
P("op3", (19, 1), (20, 0))
CUE(8, "whoosh"); CUE(10, "soft_tick"); CUE(11, "tap"); CUE(11.02, "select")
CUE(14, "tap"); CUE(14.02, "select"); CUE(15, "tap"); CUE(15.03, "heart"); CUE(22, "tap")

# ---------------------------------------------------------------- 4 · cart (22–24)
P("ld", (22 + D, 1), (23, 0))
L.track("rg", FAST, tol=0.002).to(22 + D, 0.55).to(22.62, 1).to(24, 0, INST)
P("ck", (23, 1, INST), (24, 0))
L.track("ckd", {"w": 20, "z": 1}, tol=0.002).to(23.04, 1).to(25, 0, INST)
P("il", (24 + D, 1), (25, 0)); P("ilb", (24 + D + 0.12, 1, CALM), (25, 0))
CUE(22.05, "whoosh_small"); CUE(22 + D, "tick"); CUE(22.62, "tick_hi"); CUE(23, "chime_ok"); CUE(24, "whoosh")

# ---------------------------------------------------------------- 5 · delivery map (25–37)
pins = json.loads((HERE / "assets" / "pins.json").read_text())
HUB, CITIES = pins[0], pins[1:]
P("mp", (25 + D, 1), (38, 0))
L.track("md", {"w": 10, "z": 1}, tol=0.002).to(25 + D, 1).to(38.7, 0, INST)
P("hb", (26, 1, CALM), (38, 0))
ARC0 = 27


def arc_fn(beat):
    return lambda t: np.where(t / B < 38.7, smooth(((t / B) - (beat - 0.78)) / 0.78), 0.0)


for j, c in enumerate(CITIES):
    L.fn(f"a{j}", arc_fn(ARC0 + j), breaks=[38.7], tol=0.002)
    P(f"q{j}", (ARC0 + j, 1, CALM), (38, 0))
    CUE(ARC0 + j, "city", j=j)
for i in range(3):
    P(f"rc{i}", (35 + 0.08 * i, 1, CALM), (38, 0))
    CUE(35 + 0.08 * i, "chip", i=i)
P("fr", (36, 1, CALM), (38, 0))
CUE(25, "whoosh"); CUE(25 + D, "draw", dur=0.8); CUE(26, "pop"); CUE(36, "chime2")

# ---------------------------------------------------------------- 6 · order (37–39)
press("cpr", 37)
P("tt", (38 + D, 1), (39, 0)); P("tti", (38 + D + 0.1, 1, CALM), (39, 0))
CUE(37, "tap"); CUE(38, "whoosh_small"); CUE(38 + D + 0.1, "new_chime"); CUE(39, "whoosh")

cpu = L.track("cpu", {"w": 70, "z": 1}, tol=0.001)
for b in range(0, 40, 4):
    cpu.to(b, 1, {"w": 70, "z": 1}).to(b + 0.1, 0, {"w": 13, "z": 1})

TAPS = [(3, 540, 1000), (7, 325, 600), (11, 501, 1155), (14, 340, 1155), (15, 190, 440), (22, 540, 1313), (37, 540, 1500)]
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
