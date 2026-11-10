"""Render part of a .docx body as safe HTML that looks like the Word file.

Kept: cell fills, text colour, bold / italic / underline, alignment, merged cells, numbered and
bulleted lists, images, one-cell monospace tables as code blocks (with a Copy button), command
strips, coloured bands (action and sub-step headers), small capital labels.
Every piece of text is escaped; nothing from the document is passed through as raw HTML.
"""
import html
import posixpath

from .docxread import A, MONO_FONTS, R, V, W, fill_of, on, para_text, run_text, wval

SAFE_IMG = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
            ".bmp": "image/bmp", ".svg": None}


def esc(s):
    return html.escape(s or "", quote=True)


def _hex(v):
    v = (v or "").strip().upper()
    if len(v) == 6 and all(c in "0123456789ABCDEF" for c in v):
        return "#" + v
    return None


def _dark(hexcol):
    try:
        r, g, b = int(hexcol[1:3], 16), int(hexcol[3:5], 16), int(hexcol[5:7], 16)
    except (TypeError, ValueError):
        return False
    return (0.299 * r + 0.587 * g + 0.114 * b) < 140


HIGHLIGHT = {"yellow": "#fff3a3", "green": "#c9f2c7", "cyan": "#c4f1f4", "magenta": "#f6c8f2", "lightGray": "#e8e8e8",
             "red": "#ffc9c9", "blue": "#c9d8ff", "darkYellow": "#f0dc82", "darkGray": "#cfcfcf"}


class Renderer:
    """render(start, end) -> HTML for body elements [start, end)."""

    def __init__(self, docx, image_url, heading_levels=None, sigs=None):
        self.d = docx
        self.image_url = image_url        # function(zip_path, data) -> URL (also stores the image)
        self.heading_levels = heading_levels or {}   # block index -> level, for bands inside an item
        self.sigs = sigs or {}            # block index -> signature
        self.counters = {}
        self.missing = set()              # what could not be shown (reported to the supervisor)

    # ---------------------------------------------------------------- runs
    def run_html(self, r, pstyle=None):
        out = []
        rpr = r.find(W + "rPr")
        for c in r:
            if c.tag == W + "t":
                out.append(esc(c.text))
            elif c.tag == W + "tab":
                out.append("&emsp;")
            elif c.tag in (W + "br", W + "cr"):
                out.append("<br>")
            elif c.tag == W + "noBreakHyphen":
                out.append("-")
            elif c.tag == W + "drawing":
                out.append(self.image_html(c))
            elif c.tag == W + "pict":
                out.append(self.vml_image(c))
        text = "".join(out)
        if not text:
            return ""
        styles = []
        color = _hex(wval(rpr, W + "color"))
        if color and color != "#000000":
            styles.append(f"color:{color}")
        hl = wval(rpr, W + "highlight")
        if hl in HIGHLIGHT:
            styles.append(f"background:{HIGHLIGHT[hl]}")
        shd = _hex(fill_of(rpr) if rpr is not None else None)
        if shd:
            styles.append(f"background:{shd}")
        fonts = rpr.find(W + "rFonts") if rpr is not None else None
        font = ((fonts.get(W + "ascii") or "") if fonts is not None else "").lower()
        if any(m in font for m in MONO_FONTS):
            text = f"<code>{text}</code>"
        if on(rpr, W + "b"):
            text = f"<strong>{text}</strong>"
        if on(rpr, W + "i"):
            text = f"<em>{text}</em>"
        u = wval(rpr, W + "u")
        if u and u != "none":
            text = f"<u>{text}</u>"
        if on(rpr, W + "strike"):
            text = f"<s>{text}</s>"
        va = wval(rpr, W + "vertAlign")
        if va == "superscript":
            text = f"<sup>{text}</sup>"
        elif va == "subscript":
            text = f"<sub>{text}</sub>"
        if styles:
            text = f'<span style="{";".join(styles)}">{text}</span>'
        return text

    def image_html(self, drawing):
        blip = drawing.find(".//" + A + "blip")
        if blip is None:
            return ""
        rid = blip.get(R + "embed")
        path, data = self.d.media(rid)
        if not data:
            self.missing.add("an image that is linked, not stored, in the file")
            return ""
        ext = posixpath.splitext(path)[1].lower()
        if ext not in SAFE_IMG or ext == ".svg":
            self.missing.add(f"{ext} images (browsers cannot show them)")
            return f'<span class="badge grey">[image: {esc(posixpath.basename(path))}]</span>'
        url = self.image_url(path, data)
        return f'<img src="{esc(url)}" alt="" loading="lazy">'

    def vml_image(self, pict):
        img = pict.find(".//" + V + "imagedata")
        if img is None:
            return ""
        path, data = self.d.media(img.get(R + "id"))
        if not data:
            return ""
        ext = posixpath.splitext(path)[1].lower()
        if ext not in SAFE_IMG or ext == ".svg":
            self.missing.add(f"{ext} images (browsers cannot show them)")
            return ""
        return f'<img src="{esc(self.image_url(path, data))}" alt="" loading="lazy">'

    # ---------------------------------------------------------------- paragraphs
    def para_inner(self, p):
        ppr = p.find(W + "pPr")
        sid = wval(ppr, W + "pStyle")
        parts = []
        for c in p:
            if c.tag == W + "r":
                parts.append(self.run_html(c, sid))
            elif c.tag in (W + "hyperlink", W + "smartTag", W + "ins", W + "fldSimple"):
                for r in c.iter(W + "r"):
                    parts.append(self.run_html(r, sid))
        return "".join(parts)

    def para_html(self, p):
        ppr = p.find(W + "pPr")
        inner = self.para_inner(p)
        text = para_text(p).strip()
        if not inner.strip():
            return ""
        sid = wval(ppr, W + "pStyle")
        style_name = (self.d.style_name(sid) or "").lower()
        attrs = []
        jc = wval(ppr, W + "jc")
        css = []
        if jc in ("center", "right"):
            css.append(f"text-align:{jc}")
        bg = _hex(fill_of(ppr) if ppr is not None else None)
        if bg:
            css.append(f"background:{bg};padding:6px 10px;border-radius:6px")
        if css:
            attrs.append(f'style="{";".join(css)}"')
        if style_name.startswith("heading") or style_name == "title":
            return f"<h3 {' '.join(attrs)}>{inner}</h3>"
        letters = [ch for ch in text if ch.isalpha()]
        runs = [r for r in p.iter(W + "r") if run_text(r).strip()]
        bold_all = runs and all(on(r.find(W + "rPr"), W + "b") for r in runs)
        if letters and text.upper() == text and bold_all and len(text) <= 60:
            return f'<p class="label-p" {" ".join(attrs)}>{inner}</p>'
        return f"<p {' '.join(attrs)}>{inner}</p>"

    # ---------------------------------------------------------------- tables
    def cell_html(self, tc):
        return self.flow(list(tc))

    def table_html(self, t, index=None):
        rows = t.findall(W + "tr")
        first = rows[0].findall(W + "tc") if rows else []
        if len(rows) == 1 and len(first) == 1:
            return self.box_html(first[0])
        if len(rows) == 1 and len(first) == 2 and index is not None:
            sig = self.sigs.get(index) or ""
            left = "\n".join(para_text(p) for p in first[0].iter(W + "p")).strip()
            if sig.startswith("T|command"):
                return f'<div class="cmd"><code>{esc(left)}</code><span>{self.cell_html(first[1])}</span></div>'
            if sig.startswith("T|") or index in self.heading_levels:
                lf = _hex(fill_of(first[0].find(W + "tcPr"))) or "#3b404a"
                rf = _hex(fill_of(first[1].find(W + "tcPr")))
                rstyle = f"background:{rf};" + ("color:#fff" if _dark(rf) else "") if rf else "background:#f3f4f6"
                return (f'<div class="band"><b style="background:{lf}">{esc(left)}</b>'
                        f'<span style="{rstyle}">{self.cell_html(first[1])}</span></div>')
        out = ['<div class="table-wrap"><table>']
        vmerge_counts = self._rowspans(rows)
        for ri, tr in enumerate(rows):
            out.append("<tr>")
            col = 0
            for tc in tr.findall(W + "tc"):
                tcpr = tc.find(W + "tcPr")
                span = int(wval(tcpr, W + "gridSpan") or 1)
                vm = tcpr.find(W + "vMerge") if tcpr is not None else None
                if vm is not None and vm.get(W + "val") != "restart":
                    col += span
                    continue
                attrs = []
                if span > 1:
                    attrs.append(f'colspan="{span}"')
                rs = vmerge_counts.get((ri, col), 1)
                if rs > 1:
                    attrs.append(f'rowspan="{rs}"')
                css = []
                fill = _hex(fill_of(tcpr))
                if fill:
                    css.append(f"background:{fill}")
                    if _dark(fill):
                        css.append("color:#fff")
                va = wval(tcpr, W + "vAlign")
                if va in ("center", "bottom"):
                    css.append(f"vertical-align:{'middle' if va == 'center' else 'bottom'}")
                if css:
                    attrs.append(f'style="{";".join(css)}"')
                out.append(f"<td {' '.join(attrs)}>{self.cell_html(tc)}</td>")
                col += span
            out.append("</tr>")
        out.append("</table></div>")
        return "".join(out)

    def _para_is_mono(self, p):
        runs = [r for r in p.iter(W + "r") if run_text(r).strip()]
        if not runs:
            return False
        # A code line starts in a code font; a comment at its end may be in another font.
        mono_chars = sum(len(run_text(r)) for r in runs
                         if any(m in (self.d.run_info(r)["font"] or "").lower() for m in MONO_FONTS))
        first = (self.d.run_info(runs[0])["font"] or "").lower()
        return any(m in first for m in MONO_FONTS) and mono_chars * 2 >= sum(len(run_text(r)) for r in runs)

    def _code_colour(self, paras):
        for p in paras:
            for r in p.iter(W + "r"):
                if run_text(r).strip():
                    c = _hex(wval(r.find(W + "rPr"), W + "color"))
                    if c and c != "#000000":
                        return c
        return None

    def box_html(self, tc):
        """A one-cell table: a code block, or a coloured box whose code lines become a code block."""
        tcpr = tc.find(W + "tcPr")
        fill = _hex(fill_of(tcpr))
        left = None
        borders = tcpr.find(W + "tcBorders") if tcpr is not None else None
        if borders is not None:
            lb = borders.find(W + "left")
            if lb is not None and lb.get(W + "val") not in (None, "nil", "none"):
                left = _hex(lb.get(W + "color"))
        children = list(tc)
        parts, run_code = [], []

        def flush():
            if run_code:
                text = "\n".join(para_text(p) for p in run_code)
                colour = self._code_colour(run_code)
                style = f' style="color:{colour}"' if colour else ""
                parts.append('<div class="code"><button type="button" class="copy" data-copy>Copy</button>'
                             f"<code{style}>{esc(text)}</code></div>")
                run_code.clear()
        others = []
        for el in children:
            if el.tag == W + "p" and self._para_is_mono(el):
                if others:
                    parts.append(self.flow(others))
                    others = []
                run_code.append(el)
            elif el.tag in (W + "p", W + "tbl"):
                flush()
                others.append(el)
        if others:
            parts.append(self.flow(others))
        flush()
        inner = "".join(parts)
        only_code = all(p.startswith('<div class="code">') for p in parts) and parts
        if only_code:
            bg = fill or "#F7F9FC"
            return inner.replace('<div class="code">', f'<div class="code" style="background:{bg};'
                                 f'border-left:3px solid {left or "#2B7DE9"}">', 1) if len(parts) == 1 else inner
        border = f"border-left:4px solid {left or fill or '#d6d9df'}"
        bg = f"background:{fill}" if fill else "background:#f8f9fb"
        return f'<div class="box" style="{bg};{border}">{inner}</div>'

    def _rowspans(self, rows):
        spans, starts = {}, {}
        for ri, tr in enumerate(rows):
            col = 0
            for tc in tr.findall(W + "tc"):
                tcpr = tc.find(W + "tcPr")
                span = int(wval(tcpr, W + "gridSpan") or 1)
                vm = tcpr.find(W + "vMerge") if tcpr is not None else None
                if vm is not None:
                    if vm.get(W + "val") == "restart":
                        starts[col] = (ri, col)
                        spans[(ri, col)] = 1
                    elif col in starts:
                        spans[starts[col]] += 1
                else:
                    starts.pop(col, None)
                col += span
        return spans

    # ---------------------------------------------------------------- lists and flow
    def flow(self, elements, indexes=None):
        """Render a sequence of w:p / w:tbl elements, grouping numbered paragraphs into lists."""
        out = []
        open_lists = []          # stack of (tag, numId, ilvl)
        for k, el in enumerate(elements):
            if el.tag == W + "p":
                ppr = el.find(W + "pPr")
                num = ppr.find(W + "numPr") if ppr is not None else None
                num_id = wval(num, W + "numId") if num is not None else None
                if num_id and num_id != "0" and para_text(el).strip():
                    ilvl = int(wval(num, W + "ilvl") or 0)
                    lvl = (self.d.numbering.get(num_id) or {}).get(ilvl, {"fmt": "decimal", "start": 1})
                    tag = "ul" if lvl["fmt"] in ("bullet", "none") else "ol"
                    while open_lists and (open_lists[-1][2] > ilvl or
                                          (open_lists[-1][2] == ilvl and open_lists[-1][1] != num_id)):
                        out.append(f"</{open_lists.pop()[0]}>")
                    key = (num_id, ilvl)
                    if not open_lists or open_lists[-1][2] < ilvl:
                        start = self.counters.get(key, lvl["start"])
                        out.append(f'<{tag} start="{start}">' if tag == "ol" else "<ul>")
                        open_lists.append((tag, num_id, ilvl))
                    self.counters[key] = self.counters.get(key, lvl["start"]) + 1
                    for deeper in [k2 for k2 in self.counters if k2[0] == num_id and k2[1] > ilvl]:
                        self.counters.pop(deeper)
                    out.append(f"<li>{self.para_inner(el)}</li>")
                    continue
                while open_lists:
                    out.append(f"</{open_lists.pop()[0]}>")
                out.append(self.para_html(el))
            elif el.tag == W + "tbl":
                while open_lists:
                    out.append(f"</{open_lists.pop()[0]}>")
                out.append(self.table_html(el, indexes[k] if indexes else None))
        while open_lists:
            out.append(f"</{open_lists.pop()[0]}>")
        return "".join(x for x in out if x)

    def render(self, start, end, skip_first=False):
        idx = list(range(start + (1 if skip_first else 0), end))
        return self.flow([self.d.elements[i] for i in idx], idx)
