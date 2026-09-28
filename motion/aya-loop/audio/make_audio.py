#!/usr/bin/env python3
"""Aya Shop cue sheet: every UI sound, placed by its measured peak on the scene's timeline.

Timeline cues are in beats (0.5 s at 120 BPM). Carousel/age/stretch/hover cues come from
cues.json, which the page exports from the same pure functions that draw the frames.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from sfx import Mixer, chime, click, key, stretch_tone, thwip, tick, toggle, whoosh  # noqa: E402

B = 0.5
W = 0.045


def main():
    cues = json.loads((HERE / "cues.json").read_text())
    mx = Mixer(14.0, B)
    mx.music(HERE / "music_120.wav", rms_db=-17.5)
    p = mx.place

    # bar 1 — add to cart
    p(click(1900, -13), 0.0, "press")
    p(click(2700, -24, 220, 0.05, 0.6), 0.16 * B, "release")
    p(whoosh(-29), 0.18 * B + W, "whoosh → loader")
    p(tick(3600, -23), 1 * B, "progress tick")
    p(tick(4400, -21), 2 * B, "progress tick (closed)")
    p(chime([1174.7, 1760.0], -20, 0.045, 0.22), 3 * B, "check chime D6→A6", first_ms=40)
    # bar 2 — added → cart card, quantity
    p(whoosh(-27, 0.3, 0.1, 600, 1800), 4 * B + W, "whoosh → island")
    p(whoosh(-26, 0.36, 0.12, 380, 1200), 5 * B + W, "whoosh → card")
    p(click(2100, -14), 6 * B, "+ quantity")
    p(tick(3000, -24, 0.03), 6.1 * B, "digit roll")
    p(click(1500, -19, 150, 0.06, 0.7), 7 * B, "grab carousel")
    # carousel: a soft detent when the shown product changes (same rounding the card uses), a thump on the snap
    pitch = cues["pitch"]
    last = None
    for tt, o in cues["carousel"]:
        if tt < cues["grab"][0]:
            continue
        m = round(o / pitch)
        if last is not None and m != last:
            p(tick(4600, -26, 0.025), tt, "carousel detent (next product)")
        last = m
    p(click(2400, -21, 200, 0.05, 0.6), 9 * B, "release carousel")
    snap = next((tt for tt, o in cues["carousel"] if tt > cues["release"][0] and abs(o - round(o / pitch) * pitch) < 1.0), None)
    if snap:
        p(click(900, -24, 120, 0.07, 0.4), snap, "photo snaps into place")
    # bars 3–4 — age slider, overdrag, gift wrap toggle
    p(whoosh(-27, 0.32, 0.1, 500, 1400), 10 * B + W, "whoosh → age slider")
    p(click(1500, -19, 150, 0.06, 0.7), 11 * B, "grab age")
    last = None
    for tt, v in cues["value"]:
        if cues["grab"][1] <= tt <= cues["release"][1]:
            age = round(3 + 5 * v)
            if last is not None and age != last:
                p(tick(2600 + 220 * age, -25, 0.025), tt, f"age {age}")
            last = age
    t0, st = stretch_tone(cues["stretch"])
    mx.add_at(t0, st, "stretch tension (follows the stretch curve)")
    p(thwip(-21), 13 * B, "release → spring back")
    p(whoosh(-28, 0.3, 0.1, 500, 1500), 14 * B + W, "whoosh → toggle")
    p(toggle(-14), 15 * B, "gift wrap on", first_ms=25)
    # bars 5–6 — tabs, chart
    p(whoosh(-27, 0.3, 0.1, 700, 2000), 16 * B + W, "whoosh → tabs")
    p(click(1700, -18, 170, 0.06, 0.8), 17 * B, "tab كراسات")
    p(click(1800, -18, 170, 0.06, 0.8), 18 * B, "tab ألعاب")
    p(whoosh(-26, 0.36, 0.12, 350, 1100), 19 * B + W, "whoosh → chart")
    for i in range(5):                                  # each bar lands with its own soft tick
        p(tick(2400 + 260 * i, -27, 0.03), (20 + i * 0.08) * B, f"bar {i}")
    for tt, i in cues["hover"]:
        p(tick(3800 + 120 * i, -25, 0.025), tt, f"tooltip bar {i}")
    # bars 6.4–7 — search, typing, enter, toast, back
    p(whoosh(-27, 0.3, 0.1, 600, 1700), 23 * B + W, "whoosh → search")
    p(key(-17, 1.0), 23 * B, "⌘K key")
    p(key(-17, 1.08), 24 * B, "key ب")
    p(key(-17, 0.95), 24.5 * B, "key و")
    p(key(-14, 0.8, 0.09), 25 * B, "enter")
    p(whoosh(-27, 0.3, 0.1, 450, 1300), 25.12 * B + W, "whoosh → toast")
    p(chime([1396.9, 1760.0, 2349.3], -22, 0.045, 0.2), 26 * B, "toast chime F6→A6→D7", first_ms=40)
    p(whoosh(-28, 0.3, 0.1, 500, 1400), 27 * B + W, "whoosh → button")

    info = mx.save(HERE / "mix.wav")
    (HERE / "sfx_report.json").write_text(json.dumps(mx.report, indent=1, ensure_ascii=False))
    print("mix.wav", info)


if __name__ == "__main__":
    main()
