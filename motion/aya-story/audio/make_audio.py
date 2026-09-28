#!/usr/bin/env python3
"""Aya Shop story cue sheet (64 beats at 80 BPM = 48 s): every UI sound placed by its measured peak on
the timeline that scene.py exported (cues.json). Calm clinic palette: soft taps, short ticks, quiet whooshes, and
chimes tuned to the song's key (estimated here from the conformed loop).
"""
import json
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from sfx import SR, Mixer, chime, click, draw_sweep, key, tick, whoosh  # noqa: E402

W = 0.045                                    # whooshes peak with the morph's fastest moment
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]     # Krumhansl–Kessler
MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def estimate_key(wav):
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(wav), "-f", "f32le", "-ac", "1",
                          "-ar", "22050", "-"], check=True, capture_output=True).stdout
    x = np.frombuffer(raw, np.float32).astype(np.float64)
    n = 8192
    chroma = np.zeros(12)
    f = np.fft.rfftfreq(n, 1 / 22050)
    ok = (f > 60) & (f < 2000)
    pc = (np.round(12 * np.log2(f[ok] / 261.626)) % 12).astype(int)
    for i in range(0, len(x) - n, n // 2):
        m = np.abs(np.fft.rfft(x[i:i + n] * np.hanning(n)))[ok]
        np.add.at(chroma, pc, m)
    best = max(((np.corrcoef(chroma, np.roll(prof, k))[0, 1], k, mode)
                for mode, prof in (("major", MAJOR), ("minor", MINOR)) for k in range(12)))
    return best[1], best[2], round(float(best[0]), 3)


def note(pc, octave):
    return 440.0 * 2 ** ((pc + 12 * (octave + 1) - 69) / 12)


def main():
    cues = json.loads((HERE / "cues.json").read_text())
    grid = json.loads((HERE / "grid.json").read_text())
    B, T = cues["B"], cues["T"]
    wav = HERE / f"music_{grid['bpm']:g}.wav"
    tonic, mode, conf = estimate_key(wav)
    third = 4 if mode == "major" else 3
    root6, fifth6, third6, root7 = note(tonic, 6), note(tonic + 7, 6), note(tonic + third, 6), note(tonic, 7)
    scale = [0, 2, 4, 7, 9] if mode == "major" else [0, 3, 5, 7, 10]                  # pentatonic
    penta = [note(tonic + s, 6) for s in scale]

    mx = Mixer(T, B)
    mx.music(wav, rms_db=-18.5, loop_s=T)
    for c in cues["cues"]:
        t, kind = c["t"], c["name"]
        if kind == "tap":
            mx.place(click(1700, -16, 150, 0.07, 0.7), t, "tap")
        elif kind == "soft_tick":
            mx.place(tick(3000, -27, 0.03), t, kind)
        elif kind == "pop":
            mx.place(click(2600, -22, 190, 0.05, 0.5), t, kind)
        elif kind == "select":
            mx.place(tick(4200, -25, 0.03), t, kind)
        elif kind == "whoosh":
            mx.place(whoosh(-29, 0.36, 0.12, 380, 1200), t + W, kind)
        elif kind == "whoosh_soft":
            mx.place(whoosh(-31, 0.3, 0.1, 500, 1500), t + W, kind)
        elif kind == "whoosh_small":
            mx.place(whoosh(-31, 0.26, 0.08, 600, 1800), t + W, kind)
        elif kind == "draw":
            mx.place(draw_sweep(-32, c.get("dur", 0.5) * B * 1.2), t + c.get("dur", 0.5) * B * 0.6, kind)
        elif kind == "scan":
            d = c.get("dur", 0.8) * B
            mx.place(draw_sweep(-29, d), t + d / 2, kind)
        elif kind == "chime2":
            mx.place(chime([root6, fifth6], -23, 0.06, 0.28), t, kind, first_ms=40)
        elif kind == "tile":
            mx.place(chime([penta[c["i"] % 5]], -26, 0.04, 0.14), t, f"tile {c['i']}")
        elif kind == "new_chime":
            mx.place(chime([root6, third6, fifth6], -21, 0.05, 0.3), t, kind, first_ms=40)
        elif kind == "key":
            mx.place(key(-23, 1.0 + 0.04 * (c.get("k", 0) % 4)), t, kind)
        elif kind == "tick":
            mx.place(tick(3600, -25), t, kind)
        elif kind == "tick_hi":
            mx.place(tick(4400, -23), t, kind)
        elif kind == "chime_ok":
            mx.place(chime([fifth6, root7], -20, 0.05, 0.3), t, kind, first_ms=40)
        elif kind == "swipe":
            mx.place(whoosh(-31, 0.26, 0.07, 700, 2000), t, "swipe release")
            mx.place(click(1300, -24, 120, 0.06, 0.4), t + 0.28, "photo settles")
        elif kind == "price":
            mx.place(tick(3300, -25, 0.03), t, kind)
        elif kind == "heart":
            mx.place(chime([third6 * 2], -25, 0.03, 0.12), t, kind)
        elif kind == "city":
            mx.place(chime([penta[c["j"] % 5] * (2 if c["j"] >= 5 else 1)], -24, 0.04, 0.16), t, f"city {c['j']}")
        elif kind == "swell":                                    # the bulb comes out of the dark
            mx.place(whoosh(-25, 2.2, 1.6, 110, 520), t + 1.6, kind)
        elif kind == "sweep_air":                                # light passing over the bulb
            mx.place(whoosh(-31, 0.9, 0.55, 2400, 7000), t + 0.55, kind)
        elif kind == "switch_on":
            mx.place(click(1500, -15, 110, 0.08, 0.6), t, "switch click")
            mx.place(chime([root6 / 2, fifth6 / 2, root6], -20, 0.07, 0.45), t + 0.05, "light chord", first_ms=40)
        elif kind == "name":
            mx.place(draw_sweep(-31, 0.9), t + 0.45, kind)
        elif kind == "switch_off":
            mx.place(click(1100, -16, 90, 0.08, 0.5), t, "switch click (off)")
        elif kind == "fall":                                     # the bulb goes back into the dark
            mx.place(whoosh(-27, 1.1, 0.25, 900, 180), t + 0.25, kind)
        elif kind == "play":
            mx.place(click(2200, -20, 170, 0.05, 0.5), t, kind)
        elif kind == "chapter":
            mx.place(chime([penta[c["i"] % 5]], -24, 0.04, 0.18), t, f"chapter {c['i']}")
        elif kind in ("row", "chip"):
            mx.place(tick(2800 + 300 * c.get("i", 0), -27, 0.03), t, f"{kind} {c.get('i', 0)}")
        else:
            raise SystemExit(f"no sound for cue {kind}")

    info = mx.save(HERE / "mix.wav")
    info["key"] = f"{NAMES[tonic]} {mode} (r={conf})"
    (HERE / "sfx_report.json").write_text(json.dumps(mx.report, indent=1, ensure_ascii=False))
    print("mix.wav", info)


if __name__ == "__main__":
    main()
