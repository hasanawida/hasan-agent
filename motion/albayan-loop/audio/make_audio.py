#!/usr/bin/env python3
"""Al-Bayan cue sheet: every UI sound, placed by its measured peak on the scene's timeline.

Timeline cues are in beats (0.5 s at 120 BPM). Scrub/volume/stretch/hover cues come from
cues.json, which the page exports from the same pure functions that draw the frames.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "tools"))
from sfx import Mixer, chime, click, draw_sweep, key, stretch_tone, thwip, tick, toggle, whoosh  # noqa: E402

B = 0.5
W = 0.045                     # whooshes peak with the morph's fastest moment


def main():
    cues = json.loads((HERE / "cues.json").read_text())
    mx = Mixer(14.0, B)
    mx.music(HERE / "music_120.wav")
    p = mx.place

    # bar 1 — upload
    p(click(1900, -13), 0.0, "press")
    p(click(2700, -24, 220, 0.05, 0.6), 0.16 * B, "release")
    p(whoosh(-29), 0.18 * B + W, "whoosh → loader")
    p(tick(3600, -23), 1 * B, "progress tick")
    p(tick(4400, -21), 2 * B, "progress tick (closed)")
    p(chime([1174.7, 1760.0], -20, 0.045, 0.22), 3 * B, "check chime D6→A6", first_ms=40)
    # bar 2 — ready → player
    p(whoosh(-27, 0.3, 0.1, 600, 1800), 4 * B + W, "whoosh → island")
    p(whoosh(-26, 0.36, 0.12, 380, 1200), 5 * B + W, "whoosh → player")
    p(click(2100, -14), 6 * B, "play")
    p(click(1500, -19, 150, 0.06, 0.7), 7 * B, "grab playhead")
    # scrub: a tick every two lesson-minutes the playhead passes
    last = None
    for tt, v in cues["value"]:
        if cues["grab"][0] <= tt <= cues["release"][0]:
            m = int(v * 3492 / 120)
            if last is not None and m != last:
                p(tick(5200 + 40 * (m % 5), -30, 0.02), tt, "scrub tick")
            last = m
    p(click(2400, -21, 200, 0.05, 0.6), 9 * B, "release playhead")
    # bars 3-4 — volume, overdrag, toggle
    p(whoosh(-27, 0.32, 0.1, 500, 1400), 10 * B + W, "whoosh → volume")
    p(click(1500, -19, 150, 0.06, 0.7), 11 * B, "grab volume")
    last = None
    for tt, v in cues["value"]:
        if cues["grab"][1] <= tt <= cues["release"][1]:
            m = int(v * 10 + 1e-6)
            if last is not None and m != last:
                p(tick(3000 + 180 * m, -27, 0.025), tt, f"volume {m * 10}%")
            last = m
    t0, st = stretch_tone(cues["stretch"])
    mx.add_at(t0, st, "stretch tension (follows the stretch curve)")
    p(thwip(-21), 13 * B, "release → spring back")
    p(whoosh(-28, 0.3, 0.1, 500, 1500), 14 * B + W, "whoosh → toggle")
    p(toggle(-14), 15 * B, "toggle on", first_ms=25)
    # bars 5-6 — tabs, chart
    p(whoosh(-27, 0.3, 0.1, 700, 2000), 16 * B + W, "whoosh → tabs")
    p(click(1700, -18, 170, 0.06, 0.8), 17 * B, "tab العربية")
    p(click(1800, -18, 170, 0.06, 0.8), 18 * B, "tab עברית")
    p(whoosh(-26, 0.36, 0.12, 350, 1100), 19 * B + W, "whoosh → chart")
    p(draw_sweep(-30), 20 * B + 0.3, "chart draws")
    for tt, i in cues["hover"]:
        p(tick(3800 + 120 * i, -25, 0.025), tt, f"tooltip day {i}")
    # bars 6.4-7 — ⌘K, typing, enter, toast, back
    p(whoosh(-27, 0.3, 0.1, 600, 1700), 23 * B + W, "whoosh → ⌘K")
    p(key(-17, 1.0), 23 * B, "⌘K key")
    p(key(-17, 1.08), 24 * B, "key ש")
    p(key(-17, 0.95), 24.5 * B, "key ל")
    p(key(-14, 0.8, 0.09), 25 * B, "enter")
    p(whoosh(-27, 0.3, 0.1, 450, 1300), 25.12 * B + W, "whoosh → toast")
    p(chime([1480.0, 1975.5], -22, 0.05, 0.2), 26 * B, "toast chime F#6→B6", first_ms=45)
    p(whoosh(-28, 0.3, 0.1, 500, 1400), 27 * B + W, "whoosh → button")

    info = mx.save(HERE / "mix.wav")
    (HERE / "sfx_report.json").write_text(json.dumps(mx.report, indent=1, ensure_ascii=False))
    print("mix.wav", info)


if __name__ == "__main__":
    main()
