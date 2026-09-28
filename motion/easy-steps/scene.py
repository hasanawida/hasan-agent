#!/usr/bin/env python3
"""Easy Steps story — 9:16 (1080 × 1920), 32 beats at 90 BPM = 21.333 s = 1280 frames, an event on every beat.

Every motion is computed here with the spring model (tools/beat_engine.py) and compiled to CSS keyframes, so
the page plays with CSS only. Writes:
  assets/scene.css    @property + @keyframes for every track (driven on #stage)
  assets/timing.json  BPM / beats / loop length for the renderer
  assets/feet.html    the two footprints and their pressure dots
  audio/cues.json     every UI sound, on the same timeline
"""
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools"))
sys.path.insert(0, str(HERE))
from beat_engine import DRAW, ENTER, EXIT, FAST, INST, MORPH, PRESS, TINT, Loop, S  # noqa: E402
import foot  # noqa: E402

L = Loop(90, 32)
B = L.B
D = 0.18                      # entries wait for the outgoing content (fraction of a beat)
CALM = {"w": 22, "z": 0.94}   # clinic preset: settles fast, no visible overshoot
CUE = L.cue

# ---------------------------------------------------------------- the shape
ST = {
    "hook":   dict(w=900, h=620, r=64, dk=0, tl=0),
    "scan":   dict(w=900, h=1060, r=64, dk=0, tl=0),
    "grid":   dict(w=900, h=1060, r=64, dk=0, tl=0),
    "island": dict(w=760, h=150, r=75, dk=1, tl=0),
    "book":   dict(w=900, h=1060, r=64, dk=0, tl=0),
    "loader": dict(w=220, h=220, r=110, dk=1, tl=0),
    "check":  dict(w=240, h=240, r=120, dk=0, tl=1),
    "done":   dict(w=900, h=1000, r=64, dk=0, tl=0),
    "funds":  dict(w=900, h=620, r=64, dk=0, tl=0),
}
SW, SH, SR = L.track("sw", MORPH), L.track("sh", MORPH), L.track("sr", MORPH)
FLIP = {"w": 44, "z": 1}      # colour flips are quick and wait for the content to leave (no long muddy grey)
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


for b, n in [(4, "scan"), (8, "grid"), (12, "island"), (13, "book"), (22, "loader"), (23, "check"),
             (24, "done"), (28, "funds"), (31, "hook")]:
    shape(b, n)


def P(name, *pairs):
    return L.presence(name, pairs, enter=ENTER, exit=EXIT)


def press(name, beat):
    L.track(name, PRESS, tol=0.002).to(beat, 1).to(beat + 0.16, 0)


# ---------------------------------------------------------------- 1 · hook
P("h1", (0, 1), (4, 0))
P("h2", (1, 1), (4, 0))
P("h3", (2, 1, CALM), (4, 0))
press("h3p", 3)
CUE(0, "soft_tick"); CUE(1, "soft_tick"); CUE(2, "pop"); CUE(3, "tap")

# ---------------------------------------------------------------- 2 · pressure scan
P("sc", (4 + D, 1), (8, 0))
L.track("fo", {"w": 10, "z": 1}, tol=0.002).to(4 + D, 1).to(10, 0, INST)        # outline draws inside beat 4
SCAN0, SCAN1 = -20.0, 640.0                                                        # sweep through the 600-high feet box


def scan_fn(t):
    b = t / B
    u = np.clip((b - 5.0) / 0.8, 0, 1)
    y = SCAN0 + (SCAN1 - SCAN0) * u * u * (3 - 2 * u)
    return np.where(b < 9.5, y, SCAN0)                                             # reset while hidden


L.fn("scan", scan_fn, breaks=[9.5], tol=0.2)
P("sl", (4.92, 1, FAST), (5.85, 0))
P("lg", (5, 1), (8, 0))
P("mk", (6, 1, CALM), (8, 0))
P("tg", (7, 1), (8, 0))
CUE(4, "whoosh"); CUE(4 + D, "draw", dur=0.5); CUE(5, "scan", dur=0.8); CUE(6, "pop"); CUE(7, "chime2")

# ---------------------------------------------------------------- 3 · what Easy Steps does
P("gt", (8 + D, 1), (12, 0))
for i in range(4):
    P(f"g{i}", (8 + i + (D if i == 0 else 0), 1, CALM), (12, 0))
    CUE(8 + i + (D if i == 0 else 0), "tile", i=i)
CUE(8, "whoosh")

# ---------------------------------------------------------------- 4 · NEW: book online
P("il", (12 + D, 1), (13, 0))
P("ilb", (12 + D + 0.12, 1, CALM), (13, 0))
CUE(12, "whoosh"); CUE(12 + D + 0.12, "new_chime")

P("bk", (13 + D, 1), (22, 0))
L.track("stp", MORPH, tol=0.002).to(16, 1).to(18, 2).to(20, 3).to(23, 0, INST)
P("s1", (13 + D, 1), (16, 0)); P("s2", (16 + D, 1), (18, 0)); P("s3", (18 + D, 1), (20, 0)); P("s4", (20 + D, 1), (22, 0))
for i in range(4):
    P(f"r{i}", (13 + D + 0.07 * i, 1), (16, 0))
P("cb", (13 + D + 0.3, 1), (16, 0))
L.track("se1", FAST, tol=0.002).to(14, 1).to(16, 0, EXIT)
press("cbp", 15)
CUE(13, "whoosh"); CUE(14, "tap"); CUE(14.02, "select"); CUE(15, "tap")
for i in range(7):
    P(f"d{i}", (16 + D + 0.04 * i, 1), (18, 0))
P("dh", (16 + D + 0.3, 1), (18, 0))
L.track("se2", FAST, tol=0.002).to(17, 1).to(18, 0, EXIT)
CUE(16, "whoosh_soft"); CUE(17, "tap"); CUE(17.02, "select")
for i in range(4):
    P(f"t{i}", (18 + D + 0.06 * i, 1), (20, 0))
L.track("se3", FAST, tol=0.002).to(19, 1).to(20, 0, EXIT)
CUE(18, "whoosh_soft"); CUE(19, "tap"); CUE(19.02, "select")
P("f4", (20 + D, 1), (22, 0))
P("cf", (20 + D + 0.2, 1), (22, 0))
NAME = ["س", "سا", "سار", "سارة"]
KEYS_N = [20.3, 20.44, 20.58, 20.72]
for k, b in enumerate(KEYS_N):
    nxt = KEYS_N[k + 1] if k + 1 < len(KEYS_N) else 22
    L.presence(f"n{k}", [(b, 1, INST), (nxt, 0, INST if k + 1 < len(KEYS_N) else EXIT)])
    CUE(b, "key", k=k)
L.presence("np", [(20.3, 0, INST), (23, 1, INST)])                  # a placeholder vanishes on the first key
PHONE = "050-1234567"
KEYS_P = [21.0 + 0.055 * k for k in range(len(PHONE))]
for k, b in enumerate(KEYS_P):                                         # one string per keystroke, right-anchored
    nxt = KEYS_P[k + 1] if k + 1 < len(KEYS_P) else 22
    L.presence(f"q{k}", [(b, 1, INST), (nxt, 0, INST if k + 1 < len(KEYS_P) else EXIT)])
    if PHONE[k] != "-":
        CUE(b, "key", k=k)
L.presence("pp", [(21.0, 0, INST), (23, 1, INST)])
L.track("fcn", FAST, tol=0.002).to(20.2, 1).to(20.9, 0)            # focus ring follows the field being typed
L.track("fcp", FAST, tol=0.002).to(20.9, 1).to(21.75, 0)
press("cfp", 22)
CUE(20, "whoosh_soft"); CUE(22, "tap")

# ---------------------------------------------------------------- 5 · confirmed
P("ld", (22 + D, 1), (23, 0))
L.track("rg", FAST, tol=0.002).to(22 + D, 0.55).to(22.62, 1).to(24, 0, INST)
P("ck", (23, 1, INST), (24, 0))
L.track("ckd", {"w": 20, "z": 1}, tol=0.002).to(23.04, 1).to(25, 0, INST)
CUE(22 + 0.05, "whoosh_small"); CUE(22 + D, "tick"); CUE(22.62, "tick_hi"); CUE(23, "chime_ok")
P("dt", (24 + D, 1), (28, 0))
for i in range(3):
    P(f"dr{i}", (25 + 0.08 * i, 1), (28, 0))
    CUE(25 + 0.08 * i, "row", i=i)
P("da", (26, 1), (28, 0))
P("dh2", (27, 1), (28, 0))
CUE(24, "whoosh"); CUE(26, "soft_tick"); CUE(27, "soft_tick")

# ---------------------------------------------------------------- 6 · all health funds
P("ft", (28 + D, 1), (31, 0))
for i in range(4):
    P(f"c{i}", (29 + 0.08 * i, 1, CALM), (31, 0))
    CUE(29 + 0.08 * i, "chip", i=i)
CUE(28, "whoosh"); CUE(30, "tap"); CUE(31, "whoosh")

# ---------------------------------------------------------------- persistent: CTA pulse on every downbeat, taps
cpu = L.track("cpu", {"w": 70, "z": 1}, tol=0.001)
for b in range(0, 32, 4):
    cpu.to(b, 1, {"w": 70, "z": 1}).to(b + 0.1, 0, {"w": 13, "z": 1})
press("cpr", 30)
TAPS = [(3, 540, 1000), (14, 740, 680), (15, 540, 1270), (17, 645, 730), (19, 540, 1015), (22, 540, 1270), (30, 540, 1500)]
for i, (b, x, y) in enumerate(TAPS):
    L.presence(f"ta{i}", [(b - 0.26, 1, FAST), (b + 0.3, 0, EXIT)])
    press(f"tp{i}", b)

# ---------------------------------------------------------------- feet markup
FEET_W, FEET_H, GAP = 260, 600, 20


def feet_html():
    out = []
    for side, x0 in (("L", 0), ("R", FEET_W + GAP)):
        tr = f"translate({x0 + FEET_W} 0) scale(-1 1)" if side == "L" else f"translate({x0} 0)"
        out.append(f'<g transform="{tr}">')
        out.append(f'<path class="sole" pathLength="1" d="{foot.SOLE}"/>')
        for (cx, cy, rx, ry, r) in foot.TOES:
            out.append(f'<ellipse class="sole" pathLength="1" cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" transform="rotate({r} {cx} {cy})"/>')
        for x, y, p in foot.dots(side):
            rad = 3.6 + 4.6 * p
            out.append(f'<circle class="dot" style="--y:{y:.0f}" cx="{x:.1f}" cy="{y:.1f}" r="{rad:.1f}" fill="{foot.LEVELS[foot.level(p)]}"/>')
        out.append("</g>")
    return "\n".join(out)


if __name__ == "__main__":
    (HERE / "assets").mkdir(exist_ok=True)
    (HERE / "audio").mkdir(exist_ok=True)
    css = L.css()
    (HERE / "assets" / "scene.css").write_text(css)
    (HERE / "assets" / "timing.json").write_text(json.dumps(L.timing()))
    (HERE / "assets" / "feet.html").write_text(feet_html())
    taps = [{"beat": b, "x": x, "y": y} for b, x, y in TAPS]
    (HERE / "audio" / "cues.json").write_text(json.dumps({"B": B, "T": L.T, "cues": L.cues, "taps": taps}, ensure_ascii=False, indent=1))
    print(f"{len(L.tracks)} tracks, scene.css {len(css) / 1024:.0f} KB, {len(L.cues)} cues, loop {L.T:.4f} s")
