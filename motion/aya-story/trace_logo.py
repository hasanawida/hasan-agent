#!/usr/bin/env python3
"""Trace the Aya logo (a 719 × 176 PNG — the only size the site has) into vector outlines, so the logo can be
extruded in 3D and shown large without going soft. Writes assets/bulb_path.txt and assets/name_path.txt
(SVG path data, fill-rule evenodd) and prints their viewBoxes.

The logo draws a grey drop shadow under every yellow stroke; the 3D extrusion replaces it, so only the yellow is
traced for the bulb, and only the big grey lettering (not the small subtitle line) for the name.
"""
import subprocess
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

HERE = Path(__file__).resolve().parent
SRC = HERE.parent / "aya-loop" / "assets" / "src" / "logo.png"
UP = 8                                   # trace on an 8× upsampled, lightly smoothed coverage map


def load():
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    raw = subprocess.run([ff, "-v", "error", "-i", str(SRC), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(176, 719, 4).astype(np.float32)


def trace(cov, x0, y0, eps=0.6):
    h, w = cov.shape
    big = cv2.resize(cov, (w * UP, h * UP), interpolation=cv2.INTER_CUBIC)
    big = cv2.GaussianBlur(big, (0, 0), UP * 0.45)
    mask = (big > 0.5).astype(np.uint8) * 255
    contours, _ = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    parts = []
    for c in contours:
        if cv2.contourArea(c) < (UP * 1.2) ** 2:
            continue
        a = cv2.approxPolyDP(c, eps * UP / 4, True)[:, 0, :] / UP
        pts = " L".join(f"{x0 + x:.2f},{y0 + y:.2f}" for x, y in a)
        parts.append(f"M{pts}Z")
    return "".join(parts)


def main():
    img = load()
    r, g, b, a = img[..., 0], img[..., 1], img[..., 2], img[..., 3] / 255
    yellow = a * np.clip((r - b - 40) / 60, 0, 1)
    grey = a * np.clip((40 - np.abs(r - b)) / 30, 0, 1) * np.clip((200 - r) / 40, 0, 1)
    # bulb: columns 535.. ; name: columns ..530, rows above the subtitle line
    rows = (grey[:, :530] > 0.5).sum(1)
    gap = [y for y in range(90, 150) if rows[y] == 0]
    cut = gap[0] if gap else 128
    bulb = trace(yellow[:, 535:], 0, 0)
    name = trace(grey[:cut, :530], 0, 0)
    (HERE / "assets" / "bulb_path.txt").write_text(bulb)
    (HERE / "assets" / "name_path.txt").write_text(name)
    print("bulb viewBox 0 0 184 176,", len(bulb), "chars | name viewBox 0 0 530", cut, ",", len(name), "chars | subtitle cut at row", cut)


if __name__ == "__main__":
    main()
