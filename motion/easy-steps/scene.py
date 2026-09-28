#!/usr/bin/env python3
"""Easy Steps story — 9:16 (1080 × 1920), 56 beats at 90 BPM = 37.333 s = 2240 frames.

Paced for reading: every screen holds 1–4 beats after its last change (the first cut, one event per beat, was
too fast to read). Written in story beats; the file starts 3 beats in (SHIFT) so frame 0 shows the full title card.

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

SHIFT = 3
L = Loop(90, 56, shift=SHIFT)
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


# story beats: hook 0 · scan 6 · services 15 · «new» 22 · booking 25 · loader 39 · ✓ 40 · confirmed 41 · funds 50 ·
# «new» again after the CTA tap 54 · hook 56 (= 0)
for b, n in [(6, "scan"), (15, "grid"), (22, "island"), (25, "book"), (39, "loader"), (40, "check"),
             (41, "done"), (50, "funds"), (54, "island"), (56, "hook")]:
    shape(b, n)


def P(name, *pairs):
    return L.presence(name, pairs, enter=ENTER, exit=EXIT)


def press(name, beat):
    L.track(name, PRESS, tol=0.002).to(beat, 1).to(beat + 0.16, 0)


# ---------------------------------------------------------------- 1 · hook (0–5)
P("h1", (0 + D, 1), (6, 0))
P("h2", (1, 1), (6, 0))
P("h3", (2, 1, CALM), (6, 0))
press("h3p", 5)
CUE(0 + D, "soft_tick"); CUE(1, "soft_tick"); CUE(2, "pop"); CUE(5, "tap")

# ---------------------------------------------------------------- 2 · pressure scan (6–14)
P("sc", (6 + D, 1), (15, 0))
L.track("fo", {"w": 10, "z": 1}, tol=0.002).to(6 + D, 1).to(17, 0, INST)         # outline draws inside its beat
SCAN0, SCAN1 = -20.0, 640.0                                                        # sweep through the 600-high feet box


def scan_fn(t):
    b = L.story(t)
    u = np.clip((b - 7.0) / 0.8, 0, 1)
    y = SCAN0 + (SCAN1 - SCAN0) * u * u * (3 - 2 * u)
    return np.where(b < 17, y, SCAN0)                                              # reset while hidden


L.fn("scan", scan_fn, breaks=[17], tol=0.2)
P("sl", (6.92, 1, FAST), (7.85, 0))
P("lg", (7, 1), (15, 0))
P("mk", (9, 1, CALM), (15, 0))
P("tg", (11, 1), (15, 0))
CUE(6, "whoosh"); CUE(6 + D, "draw", dur=0.5); CUE(7, "scan", dur=0.8); CUE(9, "pop"); CUE(11, "chime2")

# ---------------------------------------------------------------- 3 · what Easy Steps does (15–21)
P("gt", (15 + D, 1), (22, 0))
for i in range(4):
    P(f"g{i}", (15 + i + (D if i == 0 else 0), 1, CALM), (22, 0))
    CUE(15 + i + (D if i == 0 else 0), "tile", i=i)
CUE(15, "whoosh")

# ---------------------------------------------------------------- 4 · NEW: book online (22–38)
P("il", (22 + D, 1), (25, 0), (54 + D, 1), (56, 0))
P("ilb", (22 + D + 0.12, 1, CALM), (25, 0), (54 + D + 0.12, 1, CALM), (56, 0))
CUE(22, "whoosh"); CUE(22 + D + 0.12, "new_chime"); CUE(54, "whoosh_small"); CUE(54 + D + 0.12, "chime2")

P("bk", (25 + D, 1), (39, 0))
L.track("stp", MORPH, tol=0.002).to(30, 1).to(33, 2).to(36, 3).to(41, 0, INST)
P("s1", (25 + D, 1), (30, 0)); P("s2", (30 + D, 1), (33, 0)); P("s3", (33 + D, 1), (36, 0)); P("s4", (36 + D, 1), (39, 0))
for i in range(4):
    P(f"r{i}", (25 + D + 0.07 * i, 1), (30, 0))
P("cb", (25 + D + 0.3, 1), (30, 0))
L.track("se1", FAST, tol=0.002).to(27, 1).to(30, 0, EXIT)
press("cbp", 29)
CUE(25, "whoosh"); CUE(27, "tap"); CUE(27.02, "select"); CUE(29, "tap")
for i in range(7):
    P(f"d{i}", (30 + D + 0.04 * i, 1), (33, 0))
P("dh", (30 + D + 0.3, 1), (33, 0))
L.track("se2", FAST, tol=0.002).to(32, 1).to(33, 0, EXIT)
CUE(30, "whoosh_soft"); CUE(32, "tap"); CUE(32.02, "select")
for i in range(4):
    P(f"t{i}", (33 + D + 0.06 * i, 1), (36, 0))
L.track("se3", FAST, tol=0.002).to(35, 1).to(36, 0, EXIT)
CUE(33, "whoosh_soft"); CUE(35, "tap"); CUE(35.02, "select")
P("f4", (36 + D, 1), (39, 0))
P("cf", (36 + D + 0.2, 1), (39, 0))
NAME = ["س", "سا", "سار", "سارة"]
KEYS_N = [36.3, 36.44, 36.58, 36.72]
for k, b in enumerate(KEYS_N):
    nxt = KEYS_N[k + 1] if k + 1 < len(KEYS_N) else 39
    L.presence(f"n{k}", [(b, 1, INST), (nxt, 0, INST if k + 1 < len(KEYS_N) else EXIT)])
    CUE(b, "key", k=k)
L.presence("np", [(36.3, 0, INST), (41, 1, INST)])                  # a placeholder vanishes on the first key
PHONE = "050-1234567"
KEYS_P = [37.0 + 0.055 * k for k in range(len(PHONE))]
for k, b in enumerate(KEYS_P):                                         # one string per keystroke, right-anchored
    nxt = KEYS_P[k + 1] if k + 1 < len(KEYS_P) else 39
    L.presence(f"q{k}", [(b, 1, INST), (nxt, 0, INST if k + 1 < len(KEYS_P) else EXIT)])
    if PHONE[k] != "-":
        CUE(b, "key", k=k)
L.presence("pp", [(37.0, 0, INST), (41, 1, INST)])
L.track("fcn", FAST, tol=0.002).to(36.2, 1).to(36.9, 0)            # focus ring follows the field being typed
L.track("fcp", FAST, tol=0.002).to(36.9, 1).to(37.75, 0)
press("cfp", 39)
CUE(36, "whoosh_soft"); CUE(39, "tap")

# ---------------------------------------------------------------- 5 · confirmed (39–49)
P("ld", (39 + D, 1), (40, 0))
L.track("rg", FAST, tol=0.002).to(39 + D, 0.55).to(39.62, 1).to(41, 0, INST)
P("ck", (40, 1, INST), (41, 0))
L.track("ckd", {"w": 20, "z": 1}, tol=0.002).to(40.04, 1).to(42, 0, INST)
CUE(39 + 0.05, "whoosh_small"); CUE(39 + D, "tick"); CUE(39.62, "tick_hi"); CUE(40, "chime_ok")
P("dt", (41 + D, 1), (50, 0))
for i in range(3):
    P(f"dr{i}", (42 + 0.1 * i, 1), (50, 0))
    CUE(42 + 0.1 * i, "row", i=i)
P("da", (44, 1), (50, 0))
P("dh2", (46, 1), (50, 0))
CUE(41, "whoosh"); CUE(44, "soft_tick"); CUE(46, "soft_tick")

# ---------------------------------------------------------------- 6 · all health funds (50–53)
P("ft", (50 + D, 1), (54, 0))
for i in range(4):
    P(f"c{i}", (51 + 0.1 * i, 1, CALM), (54, 0))
    CUE(51 + 0.1 * i, "chip", i=i)
CUE(50, "whoosh"); CUE(53, "tap"); CUE(56, "whoosh")

# ---------------------------------------------------------------- persistent: CTA pulse on the song's downbeats, taps
cpu = L.track("cpu", {"w": 70, "z": 1}, tol=0.001)
for r in range(0, 56, 4):
    cpu.to(r + SHIFT, 1, {"w": 70, "z": 1}).to(r + SHIFT + 0.1, 0, {"w": 13, "z": 1})
press("cpr", 53)
TAPS = [(5, 540, 1000), (27, 740, 680), (29, 540, 1270), (32, 645, 730), (35, 540, 1015), (39, 540, 1270), (53, 540, 1500)]
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
