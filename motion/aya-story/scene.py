#!/usr/bin/env python3
"""Aya Shop story — 9:16 (1080 × 1920), 64 beats at 80 BPM = 48 s = 2880 frames. Paced for reading.

1  cinematic open: the bulb from the logo (traced to vectors, extruded in CSS 3D) comes out of the dark, turns to
   face the camera, a light sweeps it, it switches on and its light fills the screen; the name writes itself in.
2  products: three real products with prices, then the developmental games.
3  recorded workshops (the focus): the instructor, the library, one workshop playing with its chapters, the wall.
4  delivery: the country without the West Bank, from Tayibe to the north, the interior and Jerusalem — paid.
5  the logo returns, the light switches off, the bulb goes back into the dark → loop.
Written in story beats; the file starts 1 beat in so frame 0 already shows the bulb coming out of the dark.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
from beat_engine import ENTER, EXIT, FAST, INST, MORPH, PRESS, Loop, F  # noqa: E402

SHIFT = 1
L = Loop(80, 64, shift=SHIFT)
B = L.B
D = 0.18
CALM = {"w": 22, "z": 0.94}
FLIP = {"w": 44, "z": 1}
SNAP = {"w": 16, "z": 0.9}
CUE = L.cue


def spring(w):
    return {"w": w, "z": 1}


def smooth(u):
    u = np.clip(u, 0, 1)
    return u * u * (3 - 2 * u)


def P(name, *pairs):
    return L.presence(name, pairs)


def press(name, beat):
    L.track(name, PRESS, tol=0.002).to(beat, 1).to(beat + 0.16, 0)


# ---------------------------------------------------------------- 1 · the logo (0–7) and its return (59–63)
BULB_DX = -267.5                                    # logo units: from its place in the logo to the frame centre
L.track("op", spring(5), tol=0.002).to(0, 1, spring(4)).to(62.5, 0, spring(6))
L.track("tz", spring(3), tol=0.5).to(0, -140, spring(3.2)).to(2, 0, spring(4)).to(62.5, -900, spring(6))
L.track("ry", spring(3), tol=0.05).to(0, 24, spring(3.4)).to(2, 0, spring(4)).to(62.5, -70, spring(6))
L.track("rx", spring(3), tol=0.05).to(0, 6, spring(3.4)).to(2, 0, spring(4)).to(62.5, 18, spring(6))
L.track("bl", spring(3), tol=0.05).to(0, 0, spring(3)).to(62.5, 16, spring(6))
L.track("on", spring(14), tol=0.002).to(3, 1).to(61.5, 0, spring(30))
L.fn("sweep", lambda t: np.where(L.story(t) < 10, smooth((L.story(t) - 2.1) / 1.1), 0.0), breaks=[10], tol=0.002)
L.fn("lit", lambda t: np.where(L.story(t) < 30, smooth((L.story(t) - 3.0) / 1.2), 1 - smooth((L.story(t) - 61.5) / 0.9)),
     breaks=[], tol=0.001)
L.track("bx", spring(7), tol=0.1).to(4, 0).to(60.5, BULB_DX)
L.track("bs", spring(7), tol=0.002).to(4, 1).to(60.5, 1.8)
L.track("nm", spring(6), tol=0.002).to(4.3, 1).to(60.3, 0, spring(12))
P("sub", (5, 1), (6.5, 0), (59.6, 1), (60.3, 0))
L.track("ly", spring(5), tol=0.2).to(6.5, 232).to(59, 820)
L.track("ls", spring(5), tol=0.002).to(6.5, 0.62).to(59, 1.35)
P("ui", (7.2, 1), (59, 0))
CUE(0, "swell"); CUE(2.1, "sweep_air"); CUE(3, "switch_on"); CUE(4.3, "name"); CUE(5, "soft_tick")
CUE(6.5, "whoosh"); CUE(59, "whoosh"); CUE(61.5, "switch_off"); CUE(62.5, "fall")

# ---------------------------------------------------------------- the shape
ST = {
    "pill":    dict(w=320, h=110, r=55, dk=0),
    "prod":    dict(w=900, h=1060, r=64, dk=0),
    "games":   dict(w=900, h=1060, r=64, dk=0),
    "courses": dict(w=900, h=1060, r=64, dk=1),
    "map":     dict(w=900, h=1060, r=64, dk=0),
}
SW, SH, SR = L.track("sw", MORPH), L.track("sh", MORPH), L.track("sr", MORPH)
DK = L.track("dk", FLIP, tol=0.002)
_prev = {"dk": 0}


def shape(beat, name, spec=MORPH):
    s = ST[name]
    SW.to(beat, s["w"], spec); SH.to(beat, s["h"], spec); SR.to(beat, s["r"], spec)
    if s["dk"] != _prev["dk"]:
        DK.to(beat + (0.12 if s["dk"] < _prev["dk"] else 0.1), s["dk"])
        _prev["dk"] = s["dk"]


for b, n in [(8, "prod"), (20, "games"), (24, "courses"), (48, "map"), (59, "pill")]:
    shape(b, n)
P("so", (8, 1), (59, 0))

# ---------------------------------------------------------------- 2 · products (8–19) and games (20–23)
PROD = [dict(b=8, end=12), dict(b=12, end=16), dict(b=16, end=20)]
P("pv", (8 + D, 1), (20, 0))
P("ab", (8 + D + 0.2, 1), (20, 0))
PITCH, DRAG, DRAG_T = 840.0, 240.0, 0.32
SWIPES = [12, 16]
V0 = DRAG * 2 / (DRAG_T * B)
RESET = 21.5


def strip(t):
    b = L.story(t)
    x = np.zeros_like(b)
    for k, sb in enumerate(SWIPES):
        s0, nxt = sb - DRAG_T, (SWIPES[k + 1] - DRAG_T if k + 1 < len(SWIPES) else RESET)
        u = (b - s0) / DRAG_T
        x = np.where((b >= s0) & (b < sb), k * PITCH + DRAG * u * u, x)
        x = np.where((b >= sb) & (b < nxt), (k + 1) * PITCH + F((b - sb) * B, DRAG - PITCH, V0, SNAP), x)
    return x


def finger(t):
    b = L.story(t)
    x = np.zeros_like(b)
    for sb in SWIPES:
        u = (b - (sb - DRAG_T)) / DRAG_T
        x = np.where((b >= sb - DRAG_T) & (b < sb), DRAG * u * u, x)
        x = np.where((b >= sb) & (b < sb + 0.5), DRAG, x)
    return x


L.fn("sx", strip, breaks=[RESET], tol=0.2)
L.fn("fgx", finger, breaks=[sb + 0.5 for sb in SWIPES], tol=0.2)
fa, fp = L.track("fga", ENTER, tol=0.002), L.track("fgp", PRESS, tol=0.002)
for sb in SWIPES:
    fa.to(sb - DRAG_T - 0.14, 1, FAST).to(sb + 0.02, 0, EXIT)
    fp.to(sb - DRAG_T - 0.06, 1).to(sb, 0)
    CUE(sb, "swipe")
for i, p in enumerate(PROD):
    P(f"pn{i}", (p["b"] + D, 1), (p["end"], 0))
    P(f"pp{i}", (p["b"] + 1, 1, CALM), (p["end"], 0))
    P(f"pc{i}", (p["b"] + 2, 1, CALM), (p["end"], 0))
    CUE(p["b"] + 1, "price"); CUE(p["b"] + 2, "soft_tick")
CUE(8, "whoosh")

P("gt", (20 + D, 1), (24, 0))
for i in range(4):
    P(f"g{i}", (20 + D + 0.1 * i, 1, CALM), (24, 0))
    CUE(20 + D + 0.1 * i, "tile", i=i)
CUE(20, "whoosh_soft")

# ---------------------------------------------------------------- 3 · recorded workshops (24–47)
P("ct", (24 + D, 1), (29, 0))
for i in range(3):
    P(f"cr{i}", (25 + i, 1, CALM), (29, 0))
    CUE(25 + i, "soft_tick")
CUE(24, "whoosh"); CUE(24 + D, "chime2")

P("lb", (29 + D, 1), (33, 0))
for i in range(4):
    P(f"lr{i}", (29 + D + 0.12 * i, 1), (33, 0))
    CUE(29 + D + 0.12 * i, "row", i=i)
L.track("lh", FAST, tol=0.002).to(31, 1).to(33, 0, EXIT)
press("lrp", 32)
CUE(29, "whoosh_soft"); CUE(31, "select"); CUE(32, "tap")

P("pl", (33 + D, 1), (44, 0))
P("pz", (34.05, 1, INST), (45, 0, INST))                        # play → pause
L.fn("pg", lambda t: np.where(L.story(t) < 46, np.clip((L.story(t) - 34.1) / 8.9, 0, 1), 0.0), breaks=[46], tol=0.001)
for i in range(4):
    P(f"ch{i}", (33 + D + 0.4 + 0.1 * i, 1), (44, 0))
    L.track(f"cd{i}", CALM, tol=0.002).to(36 + 2 * i, 1).to(45, 0, INST)
    CUE(36 + 2 * i, "chapter", i=i)
P("au", (35, 1), (44, 0))
CUE(33, "whoosh"); CUE(34, "tap"); CUE(34.05, "play")

P("wl", (44 + D, 1), (48, 0))
L.fn("wy", lambda t: np.where(L.story(t) < 50, -300 * np.clip((L.story(t) - 44) / 4.4, 0, 1), 0.0), breaks=[50], tol=0.2)
P("wt", (45, 1), (48, 0)); P("wt2", (46, 1), (48, 0))
CUE(44, "whoosh_soft"); CUE(45, "chime2"); CUE(46, "soft_tick")

# ---------------------------------------------------------------- 4 · delivery (48–58)
pins = json.loads((HERE / "assets" / "pins.json").read_text())
HUB, CITIES = pins[0], pins[1:]
P("mp", (48 + D, 1), (59, 0))
L.track("md", spring(10), tol=0.002).to(48 + D, 1).to(60, 0, INST)
P("hb", (49, 1, CALM), (59, 0))
ARC0 = 50


def arc_fn(beat):
    return lambda t: np.where((L.story(t) > 40) & (L.story(t) < 60), smooth((L.story(t) - (beat - 0.62)) / 0.62), 0.0)


# the cities come on the half beats, so the regions and the paid-delivery note get ~3.5 s of stillness to be read
for j, c in enumerate(CITIES):
    L.fn(f"a{j}", arc_fn(ARC0 + 0.5 * j), breaks=[60], tol=0.002)
    P(f"q{j}", (ARC0 + 0.5 * j, 1, CALM), (59, 0))
    CUE(ARC0 + 0.5 * j, "city", j=j)
for i in range(3):
    P(f"rc{i}", (53.5 + 0.12 * i, 1, CALM), (59, 0))
    CUE(53.5 + 0.12 * i, "chip", i=i)
P("dn", (54.5, 1), (59, 0))
P("fee", (55.25, 1, CALM), (59, 0))
press("cpr", 58)
CUE(48, "whoosh"); CUE(48 + D, "draw", dur=0.8); CUE(49, "pop"); CUE(54.5, "soft_tick"); CUE(55.25, "pop"); CUE(58, "tap")

cpu = L.track("cpu", {"w": 70, "z": 1}, tol=0.001)
for r in range(8, 56, 4):                                   # the CTA breathes on the song's downbeats
    cpu.to(r + SHIFT, 1, {"w": 70, "z": 1}).to(r + SHIFT + 0.1, 0, {"w": 13, "z": 1})

TAPS = [(32, 0, 0), (34, 0, 0), (58, 540, 1500)]
TAPS[0] = (32, 90 + 450, 340 + 150 + 105)                 # library row 1 (layer origin 90, 340)
TAPS[1] = (34, 540, 340 + 40 + 280)                         # play button
for i, (b, x, y) in enumerate(TAPS):
    L.presence(f"ta{i}", [(b - 0.26, 1, FAST), (b + 0.3, 0, EXIT)])
    press(f"tp{i}", b)

# ---------------------------------------------------------------- markup generated from data
SIDE = {"Galilee": 1, "Haifa": -1, "Nazareth": 1, "Jaffa": -1, "Jerusalem": 1, "Negev": -1}


def arc_d(a, b):
    (x0, y0), (x1, y1) = a, b
    dx, dy = x1 - x0, y1 - y0
    n = math.hypot(dx, dy)
    px, py = -dy / n, dx / n
    if px < 0:
        px, py = -px, -py
    k = min(0.2 * n, 60)
    cx, cy = (x0 + x1) / 2 + px * k, (y0 + y1) / 2 + py * k
    return f"M{x0:.1f},{y0:.1f} Q{cx:.1f},{cy:.1f} {x1:.1f},{y1:.1f}"


def map_html():
    hx, hy = HUB["xy"]
    arcs = "".join(f'<path class="arc" pathLength="1" d="{arc_d(HUB["xy"], c["xy"])}" style="stroke-dashoffset:calc(1 - var(--a{j}));'
                   f'stroke-opacity:clamp(0, calc(var(--a{j}) * 60), 1)"/>' for j, c in enumerate(CITIES))
    html = [f'<svg class="abs" width="600" height="860" viewBox="0 0 600 860"><path class="land" pathLength="1" '
            f'd="{(HERE / "assets" / "map_path.txt").read_text()}"/>{arcs}</svg>']
    for j, c in enumerate(CITIES):
        d = arc_d(HUB["xy"], c["xy"])
        html.append(f'<div class="head" style="offset-path:path(\'{d}\');offset-distance:calc(var(--a{j}) * 100%);'
                    f'opacity:calc(clamp(0, var(--a{j}) * 30, 1) * clamp(0, (1 - var(--a{j})) * 30, 1))"></div>')
        x, y = c["xy"]
        html.append(f'<div class="pin" style="--p:var(--q{j});left:{x:.1f}px;top:{y:.1f}px"></div>')
        pos = f"left:{x + 22:.1f}px" if SIDE[c["name"]] > 0 else f"right:{600 - x + 22:.1f}px"
        html.append(f'<div class="plbl t" style="--p:var(--q{j});{pos};top:{y:.1f}px">{c["label"]}</div>')
    html.append(f'<div class="hub" style="left:{hx:.1f}px;top:{hy:.1f}px"><svg class="ico" width="34" height="34" viewBox="0 0 24 24" style="transform:scaleX(-1)">'
                '<path d="M14 18V6a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2v11a1 1 0 0 0 1 1h2"/><path d="M15 18H9"/>'
                '<path d="M19 18h2a1 1 0 0 0 1-1v-3.65a1 1 0 0 0-.22-.624l-3.48-4.35A1 1 0 0 0 17.52 8H14"/>'
                '<circle cx="17" cy="18" r="2"/><circle cx="7" cy="18" r="2"/></svg></div>')
    html.append(f'<div class="hlbl t" style="right:{600 - hx + 36:.1f}px;top:{hy:.1f}px">الطيبة</div>')
    return "\n".join(html)


def bulb_html(layers=22, depth=2.0):
    """the bulb, extruded: one filled copy of the traced outline every `depth` logo units behind the front face"""
    d = (HERE / "assets" / "bulb_path.txt").read_text()
    out = []
    for k in range(layers, 0, -1):
        shade = 0.42 + 0.3 * (1 - k / layers)                   # deeper layers darker
        out.append(f'<svg class="layer" viewBox="0 0 184 176" style="transform:translateZ({-k * depth:.1f}px);'
                   f'--sh:{shade:.3f}"><path d="{d}" fill-rule="evenodd"/></svg>')
    out.append(f'<svg class="layer front" viewBox="0 0 184 176"><path d="{d}" fill-rule="evenodd"/></svg>')
    return "\n".join(out)


if __name__ == "__main__":
    (HERE / "assets" / "scene.css").write_text(L.css())
    (HERE / "assets" / "timing.json").write_text(json.dumps(L.timing()))
    (HERE / "assets" / "map.html").write_text(map_html())
    (HERE / "assets" / "bulb.html").write_text(bulb_html())
    (HERE / "audio" / "cues.json").write_text(json.dumps({"B": B, "T": L.T, "cues": L.cues}, ensure_ascii=False, indent=1))
    print(f"{len(L.tracks)} tracks, {len(L.cues)} cues, loop {L.T:.4f} s")
