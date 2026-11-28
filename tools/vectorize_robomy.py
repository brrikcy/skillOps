"""Turns ROBOMY's wordmark (src/static/robomy-logo.png, 544 x 106, hard-edged pixels) into smooth vector shapes.

Run with any Python that has Pillow and numpy (a build-time tool only; the web app does not need them):
    python tools/vectorize_robomy.py
Writes tools/robomy_paths.svgfrag, which tools/make_logo.py puts into the logo in place of the picture, so the logo
stays sharp at every size and on every screen density.

How: the picture is a teal shape (see-through where there is no letter) with white inner lines and paler rings in the
O. The shape's edge is traced at sub-pixel precision (marching squares on a lightly smoothed copy), and each tint of
white-over-teal is traced the same way and painted on top, lightest last. Straight edges are simplified back to a few
points; the O's rings keep their curves.
"""
import os

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "src", "static", "robomy-logo.png")
OUT = os.path.join(HERE, "robomy_paths.svgfrag")
TEAL = (0, 196, 183)
BANDS = [0.20, 0.45]                           # soft rings only; the crisp white lines are traced on their own          # tint levels, painted from palest to strongest
UP = 6                                          # how much finer than the picture the contours are traced
SIGMA = 0.85                                    # smoothing of the letter's edge, in picture pixels (rounds stair-steps, not corners)
SIGMA_TINT = 1.4                                # smoothing of the white lines and rings (they are noisy in the picture)
MIN_AREA = 6                                  # smallest shape kept, in picture pixels squared
TOL = 0.16                                      # simplification tolerance in picture pixels


def blur(a, sigma):
    r = int(sigma * 3) + 1
    k = np.exp(-(np.arange(-r, r + 1) ** 2) / (2 * sigma * sigma))
    k /= k.sum()
    p = np.pad(a, r, mode="edge")
    p = np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 0, p)
    return np.apply_along_axis(lambda m: np.convolve(m, k, mode="valid"), 1, p)


def upscale(a, f):
    im = Image.fromarray(a.astype(np.float32), mode="F")
    return np.asarray(im.resize((a.shape[1] * f, a.shape[0] * f), Image.BICUBIC), dtype=np.float64)


def contours(f, level):
    """Closed loops (lists of (x, y) in field units) where the field crosses `level`; marching squares."""
    b = f >= level
    h, w = f.shape
    tl, tr, bl, br = b[:-1, :-1], b[:-1, 1:], b[1:, :-1], b[1:, 1:]
    case = tl * 8 + tr * 4 + br * 2 + bl * 1
    ys, xs = np.nonzero((case != 0) & (case != 15))

    def lerp(v0, v1):
        d = v1 - v0
        return np.where(np.abs(d) < 1e-12, 0.5, (level - v0) / np.where(d == 0, 1, d))

    segs = {}
    # edge ids: ('t', y, x) top edge of cell (y,x); ('b', ...) = top edge of the cell below; ('l', ..), ('r', ..)
    table = {1: [("l", "b")], 2: [("b", "r")], 3: [("l", "r")], 4: [("t", "r")], 5: [("t", "l"), ("b", "r")], 6: [("t", "b")],
             7: [("t", "l")], 8: [("t", "l")], 9: [("t", "b")], 10: [("t", "r"), ("l", "b")], 11: [("t", "r")], 12: [("l", "r")],
             13: [("b", "r")], 14: [("l", "b")]}
    for y, x in zip(ys, xs):
        c = case[y, x]
        v_tl, v_tr, v_bl, v_br = f[y, x], f[y, x + 1], f[y + 1, x], f[y + 1, x + 1]
        pts = {"t": (("h", y, x), (x + lerp(v_tl, v_tr), y)),
               "b": (("h", y + 1, x), (x + lerp(v_bl, v_br), y + 1)),
               "l": (("v", y, x), (x, y + lerp(v_tl, v_bl))),
               "r": (("v", y, x + 1), (x + 1, y + lerp(v_tr, v_br)))}
        if c in (5, 10):                         # saddle: decide by the centre value
            centre = (v_tl + v_tr + v_bl + v_br) / 4 >= level
            if c == 5:
                pairs = [("t", "l"), ("b", "r")] if centre else [("t", "r"), ("l", "b")]
            else:
                pairs = [("t", "r"), ("l", "b")] if centre else [("t", "l"), ("b", "r")]
        else:
            pairs = table[c]
        for e1, e2 in pairs:
            (id1, p1), (id2, p2) = pts[e1], pts[e2]
            segs.setdefault(id1, {})[id2] = (p1, p2)
            segs.setdefault(id2, {})[id1] = (p2, p1)
    # chain
    loops, seen = [], set()
    for start in list(segs):
        if start in seen:
            continue
        loop, cur, prev = [], start, None
        while True:
            seen.add(cur)
            nbrs = segs[cur]
            nxt = next((n for n in nbrs if n != prev and n not in seen), None)
            point = next(iter(nbrs.values()))[0]
            loop.append(point)
            if nxt is None:
                break
            prev, cur = cur, nxt
        if len(loop) > 8:
            loops.append(loop)
    return loops


def rdp(pts, tol):
    """Ramer-Douglas-Peucker on a closed loop (split at the two farthest-apart points)."""
    p = np.asarray(pts, dtype=float)
    if len(p) < 6:
        return p
    i0 = 0
    i1 = int(np.argmax(np.hypot(*(p - p[0]).T)))

    def run(seg):
        keep = np.zeros(len(seg), bool)
        keep[0] = keep[-1] = True
        stack = [(0, len(seg) - 1)]
        while stack:
            a, b = stack.pop()
            if b <= a + 1:
                continue
            d = seg[b] - seg[a]
            n = np.hypot(*d)
            q = seg[a + 1:b] - seg[a]
            dist = np.abs(d[0] * q[:, 1] - d[1] * q[:, 0]) / n if n else np.hypot(*q.T)
            k = int(np.argmax(dist))
            if dist[k] > tol:
                m = a + 1 + k
                keep[m] = True
                stack += [(a, m), (m, b)]
        return seg[keep]
    a = run(p[i0:i1 + 1])
    b = run(np.vstack([p[i1:], p[:1]]))
    return np.vstack([a[:-1], b[:-1]])


def to_path(loops, scale, ox=0.0, oy=0.0):
    d = []
    for loop in loops:
        s = rdp(loop, TOL * UP)
        if len(s) < 3:
            continue
        x, y = s[:, 0], s[:, 1]
        if abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))) / 2 < MIN_AREA * UP * UP:
            continue                                                        # a speck of noise in the picture
        pts = [f"{(x + 0.5) / scale + ox:.2f} {(y + 0.5) / scale + oy:.2f}" for x, y in s]
        d.append("M" + " L".join(pts) + "Z")
    return "".join(d)


def main():
    a = np.asarray(Image.open(SRC).convert("RGBA"), dtype=np.float64)
    alpha = a[..., 3] / 255.0
    tint = np.clip(a[..., 0] / 255.0, 0, 1) * (alpha > 0.5)               # red channel: 0 on teal, 1 on white
    PAD = 3                                                               # empty border so every outline closes
    alpha, tint = np.pad(alpha, PAD), np.pad(tint, PAD)
    shape = upscale(blur(alpha, SIGMA), UP)
    out = []
    base = to_path(contours(shape, 0.5), UP, -PAD, -PAD)
    out.append(f'<path fill="rgb{TEAL}" fill-rule="evenodd" d="{base}"/>'.replace("rgb(", "rgb(").replace(", ", ","))
    t = upscale(blur(tint, SIGMA_TINT), UP)
    for lvl in BANDS:
        d = to_path(contours(t, lvl), UP, -PAD, -PAD)
        if not d:
            continue
        mix = [round(c + (255 - c) * min(1, lvl + 0.12)) for c in TEAL]
        out.append(f'<path fill="rgb({mix[0]},{mix[1]},{mix[2]})" fill-rule="evenodd" d="{d}"/>')
    white = upscale(blur(np.clip((tint - 0.55) / 0.35, 0, 1), 0.55), UP)           # the white lines: nearly pure white in the picture
    dw = to_path(contours(white, 0.5), UP, -PAD, -PAD)
    if dw:
        out.append(f'<path fill="#fff" fill-rule="evenodd" d="{dw}"/>')
    # keep the painted tint inside the letter: clip to the base shape
    frag = ('<clipPath id="rbm-clip"><path fill-rule="evenodd" d="%s"/></clipPath>' % base +
            out[0] + '<g clip-path="url(#rbm-clip)">' + "".join(out[1:]) + "</g>")
    open(OUT, "w", encoding="utf-8").write(frag)
    print("wrote", OUT, len(frag), "bytes;", sum(x.count("M") for x in out), "shapes")


if __name__ == "__main__":
    main()
