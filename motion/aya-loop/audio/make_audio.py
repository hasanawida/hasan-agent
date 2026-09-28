#!/usr/bin/env python3
"""Aya Shop cue sheet (24 bars, 48 s): every UI sound, placed by its measured peak on the scene's timeline.

The shopping flow is written in flow beats and plays at one event per 2 beats from beat 20 (same
mapping as the scene: at()). Intro, products and the delivery map are written in beats. Carousel,
age, stretch and hover cues come from cues.json, exported from the same pure functions that draw
the frames.
"""
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from sfx import Mixer, chime, click, draw_sweep, key, stretch_tone, thwip, tick, toggle, whoosh  # noqa: E402

B = 0.5
W = 0.045                                   # whooshes peak with the morph's fastest moment
C0 = 20


def at(b):
    """flow beat → seconds (one event per 2 beats from beat C0; offsets inside a beat keep their length)"""
    f = math.floor(b)
    return (C0 + 2 * f + (b - f)) * B


def beat(b):
    return b * B


def main():
    cues = json.loads((HERE / "cues.json").read_text())
    mx = Mixer(48.0, B)
    mx.music(HERE / "music_120.wav", rms_db=-17.5, loop_s=16.0)     # 8-bar loop, tiled ×3
    p = mx.place

    # --- intro: shop with Aya, the website ------------------------------------------------------
    p(whoosh(-27, 0.34, 0.11, 450, 1400), beat(94) + W, "whoosh → brand")
    p(tick(3200, -26, 0.03), beat(0), "tagline")
    for i in range(1, 13):                                           # aya-shop.com types itself
        p(key(-24 - (i % 3), 1.1 + 0.03 * (i % 4), 0.05), beat(2 + i * 0.1), f"url char {i}")
    p(tick(3600, -25, 0.03), beat(3.9), "hover link")
    p(click(1900, -15), beat(6), "open aya-shop.com")
    p(whoosh(-26, 0.36, 0.12, 380, 1200), beat(8) + W, "whoosh → products")
    for i in range(6):
        p(tick(2400 + 180 * i, -29, 0.025), beat(8 + 0.18 + 0.1 + i * 0.07), f"tile {i}")
    for tt, i in cues["gridHover"]:
        p(tick(3400 + 150 * i, -25, 0.025), tt, f"hover product {i}")
    p(click(2000, -14), beat(18), "choose عالم التركيز والتفكير")
    p(whoosh(-28, 0.3, 0.1, 500, 1500), beat(19) + W, "whoosh → button")

    # --- the shopping flow (flow beats) ----------------------------------------------------------
    p(click(1900, -13), at(0), "press أضف إلى السلة")
    p(click(2700, -24, 220, 0.05, 0.6), at(0.16), "release")
    p(whoosh(-29), at(0.18) + W, "whoosh → loader")
    p(tick(3600, -23), at(1), "progress tick")
    p(tick(4400, -21), at(2), "progress tick (closed)")
    p(chime([1174.7, 1760.0], -20, 0.045, 0.22), at(3), "check chime D6→A6", first_ms=40)
    p(whoosh(-27, 0.3, 0.1, 600, 1800), at(4) + W, "whoosh → island")
    p(whoosh(-26, 0.36, 0.12, 380, 1200), at(5) + W, "whoosh → card")
    p(click(2100, -14), at(6), "+ quantity")
    p(tick(3000, -24, 0.03), at(6.1), "digit roll")
    p(click(1500, -19, 150, 0.06, 0.7), at(7), "grab carousel")
    pitch = cues["pitch"]
    last = None
    for tt, o in cues["carousel"]:
        if tt < cues["grab"][0]:
            continue
        m = round(o / pitch)
        if last is not None and m != last:
            p(tick(4600, -26, 0.025), tt, "carousel detent (next product)")
        last = m
    p(click(2400, -21, 200, 0.05, 0.6), at(9), "release carousel")
    snap = next((tt for tt, o in cues["carousel"] if tt > cues["release"][0] and abs(o - round(o / pitch) * pitch) < 1.0), None)
    if snap:
        p(click(900, -24, 120, 0.07, 0.4), snap, "photo snaps into place")
    p(click(1700, -18, 170, 0.06, 0.8), at(10), "age chip")
    p(whoosh(-27, 0.32, 0.1, 500, 1400), at(10) + W, "whoosh → age slider")
    p(click(1500, -19, 150, 0.06, 0.7), at(11), "grab age")
    last = None
    for tt, v in cues["value"]:
        if cues["grab"][1] <= tt <= cues["release"][1]:
            age = round(3 + 5 * v)
            if last is not None and age != last:
                p(tick(2600 + 220 * age, -25, 0.025), tt, f"age {age}")
            last = age
    t0, st = stretch_tone(cues["stretch"])
    mx.add_at(t0, st, "stretch tension (follows the stretch curve)")
    p(thwip(-21), at(13), "release → spring back")
    p(click(2200, -17, 180, 0.06, 0.8), at(14), "✓ confirm")
    p(whoosh(-28, 0.3, 0.1, 500, 1500), at(14) + W, "whoosh → toggle")
    p(toggle(-14), at(15), "gift wrap on", first_ms=25)
    p(whoosh(-27, 0.3, 0.1, 700, 2000), at(16) + W, "whoosh → tabs")
    p(click(1700, -18, 170, 0.06, 0.8), at(17), "tab كراسات")
    p(click(1800, -18, 170, 0.06, 0.8), at(18), "tab ألعاب")
    p(whoosh(-26, 0.36, 0.12, 350, 1100), at(19) + W, "whoosh → chart")
    for i in range(5):
        p(tick(2400 + 260 * i, -27, 0.03), at(20 + i * 0.08), f"bar {i}")
    for tt, i in cues["hover"]:
        p(tick(3800 + 120 * i, -25, 0.025), tt, f"tooltip bar {i}")
    p(whoosh(-27, 0.3, 0.1, 600, 1700), at(23) + W, "whoosh → search")
    p(key(-17, 1.0), at(23), "⌘K key")
    p(key(-17, 1.08), at(24), "key ب")
    p(key(-17, 0.95), at(24.5), "key و")
    p(key(-14, 0.8, 0.09), at(25), "enter")
    p(whoosh(-27, 0.3, 0.1, 450, 1300), at(25.12) + W, "whoosh → toast")
    p(chime([1396.9, 1760.0, 2349.3], -22, 0.045, 0.2), at(26), "toast chime F6→A6→D7", first_ms=40)

    # --- delivery: the map draws, the van reaches every stop on its beat ------------------------
    p(whoosh(-25, 0.4, 0.13, 350, 1100), beat(74) + W, "whoosh → map")
    p(draw_sweep(-30, 0.9), beat(74.2) + 0.45, "outline draws")
    notes = [1174.7, 1396.9, 1568.0, 1760.0, 2093.0, 2349.3, 2793.8]      # D minor pentatonic, north → south
    for j, f in enumerate(notes):
        b = 76 if j == 0 else 78 + 2 * (j - 1)
        p(chime([f], -23, 0.04, 0.16), beat(b), f"stop {j} reached")
    p(chime([1174.7, 1760.0, 2349.3], -21, 0.05, 0.24), beat(90), "خلال وقت قصير", first_ms=45)

    info = mx.save(HERE / "mix.wav")
    (HERE / "sfx_report.json").write_text(json.dumps(mx.report, indent=1, ensure_ascii=False))
    print("mix.wav", info)


if __name__ == "__main__":
    main()
