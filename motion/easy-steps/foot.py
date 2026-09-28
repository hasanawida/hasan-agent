"""Right-foot footprint (plantar view, big toe on the medial = left side) in a 260 × 600 box,
plus a pressure field and the dots that sample it."""
import numpy as np

SOLE = ("M128,594 C176,594 206,562 209,506 C212,452 204,404 211,344 C218,288 242,236 242,184 "
        "C242,140 218,118 188,117 C156,116 118,112 84,110 C44,108 20,136 22,180 "
        "C24,226 66,262 84,318 C98,362 64,420 54,478 C45,540 80,594 128,594 Z")
TOES = [  # cx, cy, rx, ry, rotation (deg)
    (60, 64, 31, 41, -8), (116, 46, 18, 23, 0), (155, 54, 16, 20, 6), (189, 70, 14, 18, 12), (218, 94, 12, 15, 18)]


def _cubic(p0, p1, p2, p3, n=40):
    t = np.linspace(0, 1, n)[:, None]
    return (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3


def sole_polygon():
    import re
    tok = re.sub(r"([MCZ])", r" \1 ", SOLE).replace(",", " ").split()
    pts, i, cur = [], 0, None
    while i < len(tok):
        c = tok[i]
        if c == "M":
            cur = np.array([float(tok[i + 1]), float(tok[i + 2])]); pts.append(cur); i += 3
        elif c == "C":
            p1, p2, p3 = (np.array([float(tok[i + 1 + 2 * k]), float(tok[i + 2 + 2 * k])]) for k in range(3))
            pts += list(_cubic(cur, p1, p2, p3)[1:]); cur = p3; i += 7
        else:
            i += 1
    return np.array(pts)


def inside(poly, x, y):
    n, c = len(poly), False
    for k in range(n):
        (x1, y1), (x2, y2) = poly[k], poly[(k + 1) % n]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c


def pressure(x, y, side):
    g = lambda cx, cy, s, a: a * np.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * s * s))
    heel = 1.0 if side == "R" else 0.86
    p = g(128, 522, 48, heel) + g(62, 176, 34, 0.9) + g(180, 190, 40, 0.5) + g(204, 380, 38, 0.36) + g(124, 172, 58, 0.26)
    return float(min(p, 1.0))


def dots(side, step=22):
    poly = sole_polygon()
    out = []
    for y in np.arange(128, 596, step):
        for x in np.arange(18, 248, step):
            xo = x + (step / 2 if int((y - 128) / step) % 2 else 0)
            if inside(poly, xo, y):
                d = min(np.hypot(*(poly - [xo, y]).T))
                if d > 7:
                    out.append((float(xo), float(y), pressure(xo, y, side)))
    for (cx, cy, rx, ry, _) in TOES:
        out.append((float(cx), float(cy + ry * 0.15), 0.55 if rx > 20 else 0.3))
    return out


LEVELS = ["#CFEDE6", "#69DBC0", "#008F95", "#652D90"]


def level(p):
    return 3 if p > 0.74 else 2 if p > 0.5 else 1 if p > 0.27 else 0
