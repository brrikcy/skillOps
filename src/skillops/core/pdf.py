"""A very small PDF writer: A4 pages of wrapped text (bold, italic, code, colours), tables, shaded blocks, tick boxes.

No packages: it uses the PDF's built-in Helvetica and Courier fonts, so it works the same on a laptop and in the Lambda.
Text is written in Windows-1252 (English, Indian English and most punctuation); other letters print as "?".

    doc = Document("Title", footer="Batch A · 18 Nov 2026")
    doc.text("Hello", size=16, bold=True)
    doc.rich([("Some ", False, False, False, None), ("bold", True, False, False, None)])
    data = doc.render()      # bytes of the .pdf

A run is (text, bold, italic, mono, (r, g, b) or None). A "\\n" inside a run's text is a line break.
"""
import re

PAGE_W, PAGE_H = 595.28, 841.89
MARGIN = 48.0
INK = (0.1, 0.12, 0.16)

# Helvetica widths (per 1000 em) for characters 32..126; bold is set a little wider so wrapping stays safe
_W = [278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
      556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278, 584, 584, 584, 556,
      1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778,
      667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556,
      333, 556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556,
      556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584]

FONT_NAMES = {"F1": "Helvetica", "F2": "Helvetica-Bold", "F3": "Helvetica-Oblique", "F4": "Helvetica-BoldOblique",
              "F5": "Courier", "F6": "Courier-Bold"}


def font_for(bold=False, italic=False, mono=False):
    if mono:
        return "F6" if bold else "F5"
    return {(False, False): "F1", (True, False): "F2", (False, True): "F3", (True, True): "F4"}[(bool(bold), bool(italic))]


def _pdf_text(s):
    """Text as cp1252-safe characters (one line), kept to what the built-in fonts can show."""
    s = (s or "").replace("→", "->").replace("←", "<-").replace("✓", "v").replace(" ", " ")
    for a, b in (("₹", "Rs "), ("≥", ">="), ("≤", "<="), ("≠", "!="), ("−", "-"), ("‑", "-"),
                 (" ", " "), (" ", " "), ("≈", "~"), ("×", "×")):
        s = s.replace(a, b)
    s = re.sub(r"[​-‏  ﻿]", "", s)
    s = re.sub(r"[\r\n\t]+", " ", s)
    return s.encode("cp1252", errors="replace").decode("cp1252")


def width(s, size, bold=False, mono=False):
    if mono:
        return len(s) * 600 * size / 1000.0
    total = 0
    for ch in s:
        o = ord(ch)
        total += _W[o - 32] if 32 <= o <= 126 else 556
    return total * size / 1000.0 * (1.07 if bold else 1.0)


def wrap(s, size, max_w, bold=False):
    """Break a string into lines that fit max_w points (long words are split)."""
    lines, cur = [], ""
    for word in _pdf_text(s).split(" "):
        if not word:
            continue
        while width(word, size, bold) > max_w:               # a word longer than the line
            k = len(word)
            while k > 1 and width(word[:k], size, bold) > max_w:
                k -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:k])
            word = word[k:]
        trial = word if not cur else cur + " " + word
        if width(trial, size, bold) <= max_w:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def _esc(s):
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _latin(s):
    return s.encode("cp1252", errors="replace").decode("latin-1")


def _rgb(hex6, default=(0.2, 0.22, 0.26)):
    m = re.match(r"^#?([0-9a-fA-F]{6})$", hex6 or "")
    if not m:
        return default
    v = m.group(1)
    return tuple(int(v[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _readable(c):
    """Text colours that would vanish on white paper are made dark."""
    if not c:
        return INK
    return INK if (0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]) > 0.78 else c


def layout(runs, size, max_w):
    """Runs -> lines; a line is a list of [text, font name, colour, width]."""
    lines, cur, x = [], [], 0.0
    space_pending = False

    def end_line():
        nonlocal cur, x
        lines.append(cur)
        cur, x = [], 0.0

    def put(text, font, color, w):
        nonlocal x
        if cur and cur[-1][1] == font and cur[-1][2] == color:
            cur[-1][0] += text
            cur[-1][3] += w
        else:
            cur.append([text, font, color, w])
        x += w

    for text, bold, italic, mono, color in runs:
        color = _readable(color)
        font = font_for(bold, italic, mono)
        for pi, part in enumerate((text or "").split("\n")):
            if pi > 0:
                end_line()
                space_pending = False
            part = _pdf_text(part)
            if not part:
                continue
            if part[0] == " " and (cur or x):
                space_pending = True
            for k, word in enumerate(part.split(" ")):
                if not word:
                    if k > 0:
                        space_pending = True
                    continue
                if k > 0:
                    space_pending = True
                ww = width(word, size, bold, mono)
                sw = width(" ", size, bold, mono) if (space_pending and cur) else 0.0
                if cur and x + sw + ww > max_w:
                    end_line()
                    sw = 0.0
                while ww > max_w and max_w > 10:             # a word longer than the line
                    n = len(word)
                    while n > 1 and width(word[:n], size, bold, mono) > max_w:
                        n -= 1
                    put(word[:n], font, color, width(word[:n], size, bold, mono))
                    end_line()
                    word = word[n:]
                    ww = width(word, size, bold, mono)
                    sw = 0.0
                if word:
                    put((" " if sw else "") + word, font, color, ww + sw)
                space_pending = False
    if cur or not lines:
        lines.append(cur)
    return lines


def _line_width(line):
    return sum(seg[3] for seg in line)


def _longest_word(runs, size):
    best = 0.0
    for text, bold, italic, mono, color in runs:
        for w in re.split(r"\s+", _pdf_text(text)):
            best = max(best, width(w, size, bold, mono))
    return best


class Document:
    def __init__(self, title, footer="", numbered=True):
        self.numbered = numbered
        self.title = _pdf_text(title)
        self.footer = _pdf_text(footer)
        self.pages = [[]]
        self.y = PAGE_H - MARGIN
        self.bottom = MARGIN + 18
        self._size = 10
        self.marks = []                 # bookmarks: (title, page index, y from the page bottom)

    # ---- layout helpers
    def _op(self, s):
        self.pages[-1].append(s)

    def bookmark(self, title, gap=0.0):
        """A bookmark (outline entry) pointing at the current place."""
        self.marks.append((_pdf_text(title), len(self.pages) - 1, self.y + gap))

    def space(self, pts):
        self.y -= pts

    def need(self, pts):
        """Start a new page when fewer than pts points are left."""
        if self.y - pts < self.bottom:
            self.pages.append([])
            self.y = PAGE_H - MARGIN

    def _show(self, segs, left, base):
        """Draw one line's segments with the baseline at base (points from the page bottom)."""
        for text, font, color, w in segs:
            self._op("BT /%s %.1f Tf %.3f %.3f %.3f rg %.2f %.2f Td (%s) Tj ET"
                     % (font, self._size, color[0], color[1], color[2], left, base, _esc(_latin(text))))
            left += w

    def text(self, s, size=10, bold=False, x=0.0, color=INK, max_w=None, leading=None, keep=0.0):
        """A wrapped paragraph at x points in from the margin. keep: points that must fit with its first line."""
        return self.rich([(s, bold, False, False, color)], size=size, x=x, max_w=max_w, leading=leading, keep=keep)

    def rich(self, runs, size=10, x=0.0, max_w=None, leading=None, keep=0.0, bg=None):
        lead = leading or size * 1.35
        left = MARGIN + x
        avail = max_w if max_w else PAGE_W - 2 * MARGIN - x
        lines = layout(runs, size, avail)
        self._size = size
        self.need(lead + keep)
        for line in lines:
            self.need(lead)
            self.y -= lead
            if bg:
                self._op("%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f"
                         % (bg[0], bg[1], bg[2], left - 5, self.y, avail + 10, lead))
            self._show(line, left, self.y + lead * 0.22)
        return len(lines)

    def code(self, s, size=8.2, x=0.0, color=(0.12, 0.31, 0.55), bg=(0.965, 0.973, 0.985)):
        """A block of code: every line kept as written, on a light panel (long lines are cut at the edge)."""
        lead = size * 1.4
        avail = PAGE_W - 2 * MARGIN - x - 12
        per = max(10, int(avail / (0.6 * size)))
        out = []
        for raw in (s or "").replace("\t", "    ").split("\n"):
            raw = _pdf_text(raw.rstrip())
            while len(raw) > per:
                out.append(raw[:per])
                raw = "  " + raw[per:]
            out.append(raw)
        while out and not out[0].strip():
            out.pop(0)
        while out and not out[-1].strip():
            out.pop()
        self._size = size
        left = MARGIN + x
        self.y -= 2
        for k, line in enumerate(out):
            self.need(lead + (lead if k == 0 else 0))
            self.y -= lead
            self._op("%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f"
                     % (bg[0], bg[1], bg[2], left, self.y, avail + 12, lead))
            self._show([[line, "F5", color, 0]], left + 6, self.y + lead * 0.26)
        self.y -= 3

    def table(self, rows, x=0.0, size=8.3, border=(0.78, 0.8, 0.84)):
        """rows: [[{"runs": [...], "bg": (r, g, b) or None}, ...], ...]. Columns are sized to what they hold."""
        rows = [r for r in rows if r]
        if not rows:
            return
        pad, lead = 4.0, size * 1.32
        avail = PAGE_W - 2 * MARGIN - x
        n = max(len(r) for r in rows)
        nat, low = [30.0] * n, [30.0] * n
        for r in rows:
            for ci, cell in enumerate(r):
                lines = layout(cell["runs"], size, 100000)
                nat[ci] = max(nat[ci], max(_line_width(ln) for ln in lines))
                low[ci] = max(low[ci], min(_longest_word(cell["runs"], size), 110))
        inner = avail - 2 * pad * n
        if sum(nat) <= inner:                                  # all of it fits on one line each: share the spare room
            extra = (inner - sum(nat)) / n
            widths = [w + extra for w in nat]
        elif sum(low) >= inner:
            widths = [w * inner / sum(low) for w in low]
        else:
            spare = inner - sum(low)
            want = [max(0.0, a - b) for a, b in zip(nat, low)]
            tot = sum(want) or 1.0
            widths = [b + spare * w / tot for b, w in zip(low, want)]
        cols = [w + 2 * pad for w in widths]
        usable = PAGE_H - 2 * MARGIN - 20
        self._size = size
        self.y -= 3
        for r in rows:
            laid = [layout(c["runs"], size, cols[ci] - 2 * pad) for ci, c in enumerate(r)]
            max_lines = max(len(ln) for ln in laid)
            h = max_lines * lead + 2 * pad - 2
            if h > usable:
                keep = int((usable - 2 * pad) / lead)
                laid = [ln[:keep] for ln in laid]
                h = keep * lead + 2 * pad - 2
            self.need(h)
            top = self.y
            left = MARGIN + x
            for ci in range(n):
                cell = r[ci] if ci < len(r) else {"runs": [], "bg": None}
                bg = cell.get("bg")
                if bg:
                    self._op("%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f" % (bg[0], bg[1], bg[2], left, top - h, cols[ci], h))
                self._op("%.3f %.3f %.3f RG 0.5 w %.2f %.2f %.2f %.2f re S" % (border[0], border[1], border[2], left, top - h, cols[ci], h))
                if ci < len(laid):
                    for li, line in enumerate(laid[ci]):
                        self._show(line, left + pad, top - pad - (li + 1) * lead + lead * 0.28)
                left += cols[ci]
            self.y = top - h
        self.y -= 4

    def text_at(self, s, x, y, size=10, bold=False, color=INK):
        """One short line at an exact place (no wrapping)."""
        self._size = size
        self._show([[_pdf_text(s), font_for(bold), color, 0]], MARGIN + x, y)

    def square(self, x, y, side, fill=None, stroke=None):
        """A square with its lower-left corner at x points in from the margin and y from the page bottom."""
        left = MARGIN + x
        if fill:
            self._op("%.3f %.3f %.3f rg %.2f %.2f %.2f %.2f re f" % (fill[0], fill[1], fill[2], left, y, side, side))
        if stroke:
            self._op("%.3f %.3f %.3f RG 0.8 w %.2f %.2f %.2f %.2f re S" % (stroke[0], stroke[1], stroke[2], left, y, side, side))

    def rule(self, color=(0.82, 0.84, 0.88), gap=6):
        self.need(gap * 2)
        self.y -= gap
        self._op("%.3f %.3f %.3f RG 0.6 w %.2f %.2f m %.2f %.2f l S"
                 % (color[0], color[1], color[2], MARGIN, self.y, PAGE_W - MARGIN, self.y))
        self.y -= gap

    # ---- output
    def render(self):
        n = len(self.pages)
        for k, ops in enumerate(self.pages, start=1):
            if not self.numbered:
                break
            ops.append("BT /F1 8 Tf 0.45 0.48 0.53 rg %.2f %.2f Td (%s) Tj ET"
                       % (MARGIN, MARGIN - 4, _esc(_latin(self.footer))))
            label = "Page %d of %d" % (k, n)
            ops.append("BT /F1 8 Tf 0.45 0.48 0.53 rg %.2f %.2f Td (%s) Tj ET"
                       % (PAGE_W - MARGIN - width(label, 8), MARGIN - 4, label))
        objs = []                                            # 1-based object numbers are index + 1

        def add(body):
            objs.append(body)
            return len(objs)

        add(None)                                            # 1: catalog (filled below)
        add(None)                                            # 2: pages
        fonts = {name: add("<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % base)
                 for name, base in FONT_NAMES.items()}
        res = " ".join("/%s %d 0 R" % (k, v) for k, v in fonts.items())
        kids = []
        for ops in self.pages:
            stream = "\n".join(ops).encode("latin-1", errors="replace")
            c = add("<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream.decode("latin-1")))
            p = add("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %.2f %.2f] /Resources << /Font << %s >> >> /Contents %d 0 R >>"
                    % (PAGE_W, PAGE_H, res, c))
            kids.append(p)
        info = add("<< /Title (%s) /Producer (SkillOps) >>" % _esc(_latin(self.title)))
        outlines = ""
        if self.marks:
            root = len(objs) + 1
            first = root + 1
            add(None)                                        # the outline root (filled below)
            for k, (title, pg, y) in enumerate(self.marks):
                me = first + k
                parts = ["/Title (%s)" % _esc(_latin(title)), "/Parent %d 0 R" % root,
                         "/Dest [%d 0 R /XYZ 0 %.2f null]" % (kids[pg], y)]
                if k:
                    parts.append("/Prev %d 0 R" % (me - 1))
                if k + 1 < len(self.marks):
                    parts.append("/Next %d 0 R" % (me + 1))
                add("<< %s >>" % " ".join(parts))
            objs[root - 1] = "<< /Type /Outlines /First %d 0 R /Last %d 0 R /Count %d >>" % (first, first + len(self.marks) - 1, len(self.marks))
            outlines = " /Outlines %d 0 R /PageMode /UseOutlines" % root
        objs[0] = "<< /Type /Catalog /Pages 2 0 R%s >>" % outlines
        objs[1] = "<< /Type /Pages /Kids [%s] /Count %d >>" % (" ".join("%d 0 R" % k for k in kids), len(kids))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(len(out))
            out += ("%d 0 obj\n" % i).encode("latin-1") + body.encode("latin-1", errors="replace") + b"\nendobj\n"
        xref = len(out)
        out += ("xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)).encode("latin-1")
        for off in offsets:
            out += ("%010d 00000 n \n" % off).encode("latin-1")
        out += ("trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, info, xref)).encode("latin-1")
        return bytes(out)
