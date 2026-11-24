"""Run: python tools/make_logo.py            (writes the logos into src/static)
     python tools/make_logo.py --variants  (writes a sheet of variants to compare, into tools/logo-variants/)

Builds the SkillOps logos from ROBOMY's own measurements:
  src/static/logo.svg            the one logo: ROBOMY (its PNG, unchanged) | SKILLOPS
  src/static/skillops-logo.svg   the SKILLOPS word on its own
  src/static/favicon.svg         the browser-tab icon

Measured in robomy-logo.png (544 x 106): letters run from y 7 to y 96. Upright strokes are 30 wide, horizontal
strokes about 22-24, corners square, bowls flat on top and bottom. A thin white line (2.5) runs along the middle of
every stroke, turns square corners and stops about 12 short of an open stroke end. The O is a 30-wide ring
(outer radius 47, open centre radius 17.5) with that white line in the middle of the ring and fainter rings.
SKILLOPS is drawn to the same rules. S and P are built like ROBOMY's B and R (flat bars joined by round turns);
the O is built like ROBOMY's O.
"""
import base64
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..", "src", "static")
TOP, BASE, XH, DESC = 7, 96, 30, 116      # cap line, baseline, x-height line, descender (logo units)
STEM, BAR, BAR_LC = 30, 22, 18            # upright strokes, horizontal strokes, the small s's horizontals
INLINE, INSET = 2.6, 12                   # the white line, and how far it stops from an open stroke end

# The chosen look (compare all options with --variants): capitals, like ROBOMY itself, in deep navy - calm,
# trustworthy and institutional next to ROBOMY's teal - with ROBOMY's own O and no extra decoration.
# The O is a progress ring: the navy ring, three-quarters drawn over in ROBOMY teal (the user's "concept A").
FINAL = {"caps": True, "color": "#1d2d50", "arc": "#00c4b7", "gap": 11}
# The same logo for dark mode: light letters, the white line drawn in the page colour, a lighter ring track.
DARK = {"caps": True, "color": "#e9eef8", "arc": "#19c9bb", "track": "#e9eef8", "inline": "#141c2f", "gap": 11,
        "progress": True}


class Glyph:
    def __init__(self, width):
        self.width, self.ink, self.lines, self.extra = width, [], [], ""

    def rect(self, x, y, w, h):
        self.ink.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" height="{h:g}"/>')

    def stroke(self, d, w):
        self.ink.append(f'<path d="{d}" fill="none" stroke-width="{w:g}" stroke-linecap="butt" stroke-linejoin="miter"/>')

    def line(self, d):
        self.lines.append(f'<path d="{d}"/>')


def letter_s(height_top, bar, width):
    """A geometric S: flat bars joined by round turns, like ROBOMY's B."""
    g = Glyph(width)
    top_c, bot_c = height_top + bar / 2, BASE - bar / 2
    mid_c = (top_c + bot_c) / 2
    r = (mid_c - top_c) / 2
    lx, rx = r + bar / 2, width - r - bar / 2
    turns = f"H {lx:g} A {r:g} {r:g} 0 0 0 {lx:g} {mid_c:g} H {rx:g} A {r:g} {r:g} 0 0 1 {rx:g} {bot_c:g}"
    g.stroke(f"M {width} {top_c:g} {turns} H 0", bar)
    g.line(f"M {width - INSET} {top_c:g} {turns} H {INSET}")
    return g


def cap_k():
    g = Glyph(70)
    g.rect(0, TOP, STEM, BASE - TOP)
    g.ink.append(f'<clipPath id="kclip"><rect x="18" y="{TOP}" width="52" height="{BASE - TOP}"/></clipPath>'
                 '<g clip-path="url(#kclip)"><path d="M 14 74 L 78 -12" fill="none" stroke-width="30"/>'
                 '<path d="M 34 46 L 80 112" fill="none" stroke-width="30"/></g>')
    g.line(f"M {STEM / 2} {TOP + INSET} V {BASE - INSET}")
    g.line("M 30 52.5 L 56.7 16.6")
    g.line("M 43.8 60 L 61.9 86.1")
    return g


def small_k():
    g = Glyph(62)
    g.rect(0, TOP, STEM, BASE - TOP)
    g.ink.append(f'<clipPath id="kclip"><rect x="18" y="{XH}" width="44" height="{BASE - XH}"/></clipPath>'
                 f'<g clip-path="url(#kclip)"><path d="M 16 82 L 66 {XH - 14}" fill="none" stroke-width="28"/>'
                 f'<path d="M 34 60 L 70 {BASE + 20}" fill="none" stroke-width="28"/></g>')
    g.line(f"M {STEM / 2} {TOP + INSET} V {BASE - INSET}")
    g.line(f"M {STEM / 2} 76 L 50 {XH + 13}")
    g.line(f"M 40 66 L 55 {BASE - 11}")
    return g


def cap_i():
    g = Glyph(STEM)
    g.rect(0, TOP, STEM, BASE - TOP)
    g.line(f"M {STEM / 2} {TOP + INSET} V {BASE - INSET}")
    return g


def small_i():
    g = Glyph(STEM)
    g.rect(0, XH, STEM, BASE - XH)
    g.rect(0, TOP, STEM, XH - TOP - 8)
    g.line(f"M {STEM / 2} {XH + INSET} V {BASE - INSET}")
    return g


def cap_l():
    g = Glyph(56)
    g.rect(0, TOP, STEM, BASE - TOP)
    g.rect(0, BASE - BAR, 56, BAR)
    g.line(f"M {STEM / 2} {TOP + INSET} V {BASE - BAR / 2} H {56 - INSET}")
    return g


def small_l():
    g = Glyph(STEM)
    g.rect(0, TOP, STEM, BASE - TOP)
    g.line(f"M {STEM / 2} {TOP + INSET} V {BASE - INSET}")
    return g


def letter_o(color, steps, arc=None, inline="#fff", progress=False):
    """ROBOMY's O: a 30-wide ring with an open centre, a white line through the middle and fainter rings.
    With arc: a progress ring - three quarters of the ring drawn over in that colour, from the top round to the left."""
    g = Glyph(94)
    cx, cy, ro, ri = 47, 51.5, 47, 17.5
    mid = (ro + ri) / 2
    g.ink.append(f'<circle cx="{cx}" cy="{cy}" r="{mid}" fill="none" stroke-width="{ro - ri}"'
                 + (f' stroke="{color}"' if color else "") + "/>")
    arc_path = f'M {cx:.2f} {cy - mid:.2f} A {mid} {mid} 0 1 1 {cx - mid:.2f} {cy:.2f}'
    if arc and not progress:
        g.extra += f'<path d="{arc_path}" fill="none" stroke="{arc}" stroke-width="{ro - ri}"/>'
    if arc and progress:
        # progress being tracked: the ring is the track; the teal arc runs inside it with rounded ends,
        # and the track's line (drawn next, on top) runs through the progress too
        g.extra += (f'<path d="{arc_path}" fill="none" stroke="{arc}" stroke-width="{ro - ri - 9}" '
                    f'stroke-linecap="round"/>')
    g.extra += (f'<circle cx="{cx}" cy="{cy}" r="{mid}" fill="none" stroke="{inline}" stroke-width="{INLINE}"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{mid + 7.5}" fill="none" stroke="{inline}" stroke-width="3" opacity=".3"/>'
                f'<circle cx="{cx}" cy="{cy}" r="{mid - 7.5}" fill="none" stroke="{inline}" stroke-width="3" opacity=".3"/>')
    if steps:
        c = color or "currentColor"
        g.extra += (f'<rect x="{cx - 10.5}" y="{cy + 1}" width="5.4" height="7" rx="1" fill="{c}" opacity=".55"/>'
                    f'<rect x="{cx - 2.7}" y="{cy - 4.5}" width="5.4" height="12.5" rx="1" fill="{c}" opacity=".8"/>'
                    f'<rect x="{cx + 5.1}" y="{cy - 10}" width="5.4" height="18" rx="1" fill="{c}"/>')
    return g


def cap_p():
    g = Glyph(66)
    g.rect(0, TOP, STEM, BASE - TOP)
    top_c, low_c = TOP + BAR / 2, 62
    r = (low_c - top_c) / 2
    cx = 66 - r - BAR / 2
    g.stroke(f"M {STEM / 2} {top_c} H {cx} A {r} {r} 0 0 1 {cx} {low_c} H {STEM / 2}", BAR)
    g.line(f"M {STEM / 2} {BASE - INSET} V {top_c} H {cx} A {r} {r} 0 0 1 {cx} {low_c} H {STEM / 2}")
    return g


def small_p():
    g = Glyph(62)
    g.rect(0, XH, STEM, DESC - XH)
    top_c, bot_c = XH + BAR / 2, BASE - BAR / 2
    r = (bot_c - top_c) / 2
    cx = 62 - r - BAR / 2
    g.stroke(f"M {STEM / 2} {top_c} H {cx} A {r} {r} 0 0 1 {cx} {bot_c} H {STEM / 2}", BAR)
    g.line(f"M {STEM / 2} {DESC - INSET} V {top_c} H {cx} A {r} {r} 0 0 1 {cx} {bot_c} H {STEM / 2}")
    return g


def build_word(prefix, caps=True, color="#1d2d50", o_color=None, steps=False, gap=11, arc=None, track=None,
               inline="#fff", progress=False):
    o = letter_o(track or o_color, steps, arc, inline, progress)
    if caps:
        glyphs = [letter_s(TOP, BAR, 64), cap_k(), cap_i(), cap_l(), cap_l(), o, cap_p(), letter_s(TOP, BAR, 64)]
    else:
        glyphs = [letter_s(TOP, BAR, 64), small_k(), small_i(), small_l(), small_l(), o, small_p(),
                  letter_s(XH, BAR_LC, 50)]
    x, ink, lines, extra = 0, [], [], []
    for g in glyphs:
        body = "".join(g.ink).replace('id="kclip"', f'id="{prefix}kclip"').replace("#kclip", f"#{prefix}kclip")
        ink.append(f'<g transform="translate({x:g} 0)">{body}</g>')
        lines.append(f'<g transform="translate({x:g} 0)">{"".join(g.lines)}</g>')
        if g.extra:
            extra.append(f'<g transform="translate({x:g} 0)" color="{color}">{g.extra}</g>')
        x += g.width + gap
    width = x - gap
    svg = (f'<g fill="{color}" stroke="{color}">{"".join(ink)}</g>'
           f'<g fill="none" stroke="{inline}" stroke-width="{INLINE}" stroke-linecap="butt" stroke-linejoin="miter">'
           f'{"".join(lines)}</g>{"".join(extra)}')
    return svg, width, (BASE if caps else DESC)


ROBOMY_PNG = base64.b64encode(open(os.path.join(ROOT, "robomy-logo.png"), "rb").read()).decode()


def lockup(style, prefix="l", divider="#d5d9e0"):
    word, w, bottom = build_word(prefix, **style)
    space = 30
    div_x = 539 + space
    word_x = div_x + space
    width = word_x + w
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="6 2 {width - 4:g} {bottom - 2 + 6:g}" role="img" '
            f'aria-label="Robomy SkillOps">\n'
            f'  <!-- One logo: ROBOMY exactly as it is, a thin divider, and SKILLOPS drawn to ROBOMY\'s letter rules\n'
            f'       (tools/make_logo.py). -->\n'
            f'  <image href="data:image/png;base64,{ROBOMY_PNG}" x="0" y="0" width="544" height="106"/>\n'
            f'  <rect x="{div_x - 1.5}" y="18" width="3" height="68" rx="1.5" fill="{divider}"/>\n'
            f'  <g transform="translate({word_x} 0)">{word}</g>\n</svg>\n')


def favicon(color, arc=None):
    """The S of the logo, white on a navy tile, with its line in navy (the user chose this over the ring)."""
    s = letter_s(TOP, BAR, 64)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
            '  <!-- SkillOps: the S of the logo, white on the SkillOps navy -->\n'
            f'  <rect width="64" height="64" rx="14" fill="{color}"/>\n'
            f'  <g transform="translate(14.6 9.6) scale(.545)">'
            f'<g fill="#fff" stroke="#fff">{"".join(s.ink)}</g>'
            f'<g fill="none" stroke="{color}" stroke-width="{INLINE + 1}">{"".join(s.lines)}</g></g>\n</svg>\n')


VARIANTS = {
    "A  capitals · deep crimson": {"caps": True, "color": "#8f1d24"},
    "B  capitals · navy": {"caps": True, "color": "#1d2d50"},
    "C  capitals · charcoal, crimson O": {"caps": True, "color": "#262a31", "o_color": "#8f1d24"},
    "D  capitals · deep ROBOMY teal": {"caps": True, "color": "#0b5f5a"},
    "E  capitals · navy, ROBOMY-teal O": {"caps": True, "color": "#1d2d50", "o_color": "#00b5a7"},
    "F  capitals · bright red (today's colour)": {"caps": True, "color": "#e5383b"},
    "G  mixed case · deep crimson": {"caps": False, "color": "#8f1d24"},
    "H  mixed case · red with steps (current)": {"caps": False, "color": "#e5383b", "steps": True},
}

if "--variants" in sys.argv:
    out = os.path.join(HERE, "logo-variants")
    os.makedirs(out, exist_ok=True)
    cards = []
    for k, (name, style) in enumerate(VARIANTS.items()):
        fn = f"v{k}.svg"
        open(os.path.join(out, fn), "w", encoding="utf-8").write(lockup(style, prefix=f"v{k}"))
        cards.append(f'<div class="card"><div class="name">{name}</div><img src="{fn}" class="big">'
                     f'<div class="bar"><img src="{fn}" class="small"></div></div>')
    open(os.path.join(out, "index.html"), "w", encoding="utf-8").write(
        '<!doctype html><html><head><meta charset="utf-8"><style>'
        'body{margin:0;padding:24px;background:#f2f3f6;font:13px Segoe UI,sans-serif;color:#444}'
        '.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.card{background:#fff;border-radius:14px;padding:18px 22px}'
        '.name{font-weight:700;margin-bottom:12px}.big{height:62px;display:block}'
        '.bar{margin-top:14px;padding:10px 12px;border-top:1px solid #eee}.small{height:28px;display:block}'
        f'</style></head><body><div class="grid">{"".join(cards)}</div></body></html>')
    print("variants written to", out)
else:
    word, w, bottom = build_word("w", **FINAL)
    open(os.path.join(ROOT, "skillops-logo.svg"), "w", encoding="utf-8").write(
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 2 {w + 4:g} {bottom - 2 + 6:g}" role="img" '
        f'aria-label="SkillOps">\n  {word}\n</svg>\n')
    open(os.path.join(ROOT, "logo.svg"), "w", encoding="utf-8").write(lockup(FINAL))
    open(os.path.join(ROOT, "logo-dark.svg"), "w", encoding="utf-8").write(lockup(DARK, prefix="d", divider="#3a4766"))
    open(os.path.join(ROOT, "favicon.svg"), "w", encoding="utf-8").write(favicon(FINAL["color"], FINAL["arc"]))
    print("logos written; word width", w)
