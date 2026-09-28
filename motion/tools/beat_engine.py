#!/usr/bin/env python3
"""Beat engine for CSS-only motion loops.

CLI (same interface as the first draft, with the beat labels and the loop check fixed):
  python3 tools/beat_engine.py 128 32       → beat grid JSON: time, frame, bar.beat, kind of every beat
  python3 tools/beat_engine.py --presets    → tempos per industry whose loop is a whole number of frames

Library: the same closed-form spring model as the JS scenes (Track = one spring per change, replayed
from the previous loop so the function is periodic), compiled to pure CSS. Every track becomes a
registered custom property (@property) animated by its own @keyframes on #stage; each motion segment
is eased by a CSS linear() curve sampled from the real spring. The page needs no JavaScript to play,
and the renderer can still freeze any instant exactly (document.getAnimations → currentTime).
"""
import json
import math
import sys

import numpy as np

FPS = 60
PRESETS = {"clinic": (75, 95), "food": (95, 115), "shop": (118, 132)}

# spring specs {w: angular frequency, z: damping ratio}; z ≥ 0.8 keeps overshoot ≤ 1.5 %
MORPH = {"w": 17, "z": 0.82}
TINT = {"w": 24, "z": 1}
ENTER = {"w": 21, "z": 1}
EXIT = {"w": 62, "z": 1}
FAST = {"w": 26, "z": 0.86}
POP = {"w": 30, "z": 0.74}           # the shop preset's "snap": ~3 % overshoot, not a bounce
PRESS = {"w": 48, "z": 0.85}
DRAW = {"w": 13, "z": 1}
DRIVE = {"w": 13, "z": 1}
SNAP = {"w": 17, "z": 0.9}
INST = {"inst": True}


def S(t, sp):
    """step response of a damped spring (0 → 1), vectorised"""
    t = np.asarray(t, dtype=float)
    if sp.get("inst"):
        return (t >= 0).astype(float)
    w, z = sp["w"], sp["z"]
    tp = np.maximum(t, 0)
    if z >= 1:
        y = 1 - np.exp(-w * tp) * (1 + w * tp)
    else:
        wd = w * math.sqrt(1 - z * z)
        y = 1 - np.exp(-z * w * tp) * (np.cos(wd * tp) + (z * w / wd) * np.sin(wd * tp))
    return np.where(t > 0, y, 0.0)


def F(t, x0, v0, sp):
    """free response from displacement x0 and velocity v0 (e.g. after a drag is released)"""
    t = np.maximum(np.asarray(t, dtype=float), 0)
    w, z = sp["w"], sp["z"]
    if z >= 1:
        return np.exp(-w * t) * (x0 + (v0 + w * x0) * t)
    wd = w * math.sqrt(1 - z * z)
    return np.exp(-z * w * t) * (x0 * np.cos(wd * t) + ((v0 + z * w * x0) / wd) * np.sin(wd * t))


def grid(bpm, beats, fps=FPS):
    spb = 60.0 / bpm
    loop_frames = beats * spb * fps
    cues = []
    for k in range(beats):
        bar, pos = divmod(k, 4)
        kind = ("phrase" if k % 16 == 0 else "downbeat") if pos == 0 else ("backbeat" if pos in (1, 3) else "beat3")
        t = k * spb
        cues.append({"beat": k, "bar_beat": f"{bar + 1}.{pos + 1}", "kind": kind, "t": round(t, 6),
                     "frame": round(t * fps, 3), "nearest_frame_error_ms": round((round(t * fps) / fps - t) * 1000, 2)})
    return {"bpm": bpm, "beats": beats, "bars": beats / 4, "seconds_per_beat": round(spb, 6),
            "frames_per_beat": round(spb * fps, 4), "loop_s": round(beats * spb, 6), "loop_frames": round(loop_frames, 4),
            "loop_is_whole_frames": abs(loop_frames - round(loop_frames)) < 1e-6, "cues": cues}


def good_tempos(beats, lo, hi, fps=FPS):
    """integer BPMs whose loop of `beats` is a whole number of frames (no seam drift in the MP4)"""
    return [b for b in range(lo, hi + 1) if abs(beats * 60 * fps / b - round(beats * 60 * fps / b)) < 1e-9]


class Track:
    def __init__(self, loop, name, spec, tol):
        self.L, self.name, self.spec, self.tol = loop, name, spec, tol
        self.ev = []

    def to(self, beat, v, spec=None):
        self.ev.append((beat * self.L.B, float(v), spec or self.spec))
        return self

    def value(self, t):
        ev = sorted(self.ev, key=lambda e: e[0])
        base = ev[-1][1] if ev else 0.0
        v = np.full(np.shape(t), base)
        prev = base
        for t0, val, sp in ev:
            d, prev = val - prev, val
            if d:
                v = v + d * (S(t - t0, sp) + S(t - t0 + self.L.T, sp))
        return v

    def breaks(self):
        return [e[0] for e in self.ev if e[2].get("inst")]


class FnTrack:
    def __init__(self, loop, name, fn, breaks, tol):
        self.L, self.name, self.fn, self._breaks, self.tol = loop, name, fn, [b * loop.B for b in breaks], tol

    def value(self, t):
        return self.fn(np.asarray(t, dtype=float))

    def breaks(self):
        return self._breaks


def _simplify(x, u, tol):
    """keep the fewest points so linear interpolation stays within tol of u (vertical error)"""
    keep = np.zeros(len(x), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(x) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        xs, us = x[a + 1:b], u[a + 1:b]
        interp = u[a] + (u[b] - u[a]) * (xs - x[a]) / (x[b] - x[a])
        i = int(np.argmax(np.abs(us - interp)))
        if abs(us[i] - interp[i]) > tol:
            m = a + 1 + i
            keep[m] = True
            stack += [(a, m), (m, b)]
    return np.nonzero(keep)[0]


def _num(v, nd=3):
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class Loop:
    def __init__(self, bpm, beats, fps=FPS):
        self.bpm, self.beats, self.fps = bpm, beats, fps
        self.B = 60.0 / bpm
        self.T = beats * self.B
        self.tracks = []
        self.cues = []
        g = grid(bpm, beats, fps)
        if not g["loop_is_whole_frames"]:
            raise SystemExit(f"{beats} beats at {bpm} BPM = {g['loop_frames']} frames: the MP4 loop would drift. "
                             f"Whole-frame tempos: {good_tempos(beats, int(bpm) - 8, int(bpm) + 8, fps)}")

    # --- authoring ----------------------------------------------------------------------------
    def track(self, name, spec=MORPH, tol=0.15):
        k = Track(self, name, spec, tol)
        self.tracks.append(k)
        return k

    def fn(self, name, fn, breaks=(), tol=0.15):
        k = FnTrack(self, name, fn, breaks, tol)
        self.tracks.append(k)
        return k

    def presence(self, name, pairs, enter=ENTER, exit=EXIT):
        """0/1 track for content: [(beat, 1|0[, spec])] — fast exits, softer entries"""
        k = self.track(name, enter, tol=0.002)
        for p in pairs:
            b, v = p[0], p[1]
            k.to(b, v, p[2] if len(p) > 2 else (enter if v else exit))
        return k

    def cue(self, beat, name, **kw):
        self.cues.append({"t": round(beat * self.B, 6), "beat": beat, "name": name, **kw})

    # --- compile ------------------------------------------------------------------------------
    def _keyframes(self, k):
        T, eps = self.T, 1e-4
        ts = np.arange(0, T, 1 / 480)
        extra = []
        for b in k.breaks():
            b %= T
            extra += [b - eps, b] if b - eps > 0 else [b]
        ts = np.unique(np.concatenate([ts, [T], np.array(extra, float)]))
        ts = ts[(ts >= 0) & (ts <= T)]
        v = k.value(ts)
        seam = abs(v[0] - v[-1])
        if seam > max(k.tol, 1e-3):
            raise SystemExit(f"track {k.name}: value at 0 ({v[0]:.4f}) ≠ value at T ({v[-1]:.4f}) — the loop would jump")
        v[-1] = v[0]
        step_tol = max(np.ptp(v), 1e-9) * 1e-5
        moving = np.abs(np.diff(v)) > step_tol
        frames = [(0.0, v[0], None)]
        i, n = 0, len(moving)
        while i < n:
            if not moving[i]:
                i += 1
                continue
            j = i
            while j < n and moving[j]:
                j += 1
            # interval of samples i..j is moving; split into monotone pieces
            seg = np.arange(i, j + 1)
            d = np.sign(np.diff(v[seg]))
            cuts = [0] + [m + 1 for m in range(1, len(d)) if d[m] != d[m - 1] and d[m] != 0 and d[m - 1] != 0] + [len(seg) - 1]
            for a, b in zip(cuts[:-1], cuts[1:]):
                ia, ib = seg[a], seg[b]
                ta, tb, va, vb = ts[ia], ts[ib], v[ia], v[ib]
                if tb - ta < 1e-9:
                    continue
                if abs(vb - va) < 1e-12:
                    frames.append((ta, va, None))
                    continue
                x = (ts[ia:ib + 1] - ta) / (tb - ta)
                u = (v[ia:ib + 1] - va) / (vb - va)
                keep = _simplify(x, u, min(0.002, k.tol / abs(vb - va)))
                if len(keep) <= 2:
                    ease = None                                  # a straight ramp (or a step)
                else:
                    pts = ", ".join(f"{_num(u[m], 4)} {_num(x[m] * 100, 2)}%" for m in keep[1:-1])
                    ease = f"linear(0, {pts}, 1)"
                frames.append((ta, va, ease))
                frames.append((tb, vb, None))
            i = j
        frames.append((T, v[0], None))
        # merge frames at the same instant (the later one carries the easing of the next piece)
        out = []
        for f in frames:
            if out and abs(out[-1][0] - f[0]) < 1e-7:
                out[-1] = (out[-1][0], f[1], f[2] or out[-1][2])
            else:
                out.append(f)
        return out

    def css(self):
        props, kfs, names = [], [], []
        for k in self.tracks:
            var = f"--{k.name}"
            frames = self._keyframes(k)
            props.append(f"@property {var} {{ syntax: '<number>'; inherits: true; initial-value: {_num(frames[0][1])}; }}")
            lines = []
            for t, val, ease in frames:
                pct = _num(t / self.T * 100, 5)
                body = f"{var}: {_num(val)};" + (f" animation-timing-function: {ease};" if ease else "")
                lines.append(f"  {pct}% {{ {body} }}")
            kfs.append(f"@keyframes k-{k.name} {{\n" + "\n".join(lines) + "\n}")
            names.append(f"k-{k.name}")
        T = _num(self.T, 6)
        stage = (f"#stage {{ animation-name: {', '.join(names)};\n  animation-duration: {T}s; animation-timing-function: linear;"
                 f" animation-iteration-count: infinite; animation-fill-mode: both; }}")
        return "\n".join(props) + "\n" + "\n".join(kfs) + "\n" + stage + "\n"

    def timing(self):
        return {"BPM": self.bpm, "BEATS": self.beats, "B": self.B, "T": self.T}


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--presets":
        beats = int(sys.argv[2]) if len(sys.argv) > 2 else 32
        for name, (lo, hi) in PRESETS.items():
            print(f"{name:7s} {lo}–{hi} BPM, {beats} beats → whole-frame tempos: {good_tempos(beats, lo, hi)}")
        sys.exit()
    bpm = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    beats = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    print(json.dumps(grid(bpm, beats), indent=2, ensure_ascii=False))
