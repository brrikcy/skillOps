"""Runbook page HTML -> PDF. Reads the page's blocks (paragraphs, headings, lists, code, tables, shaded boxes) and
lays them out on a pdf.Document. Pictures and buttons are left out.

    draw(doc, html, size=9.5)
"""
import re
from html.parser import HTMLParser

from . import pdf

_SKIP = {"button", "svg", "form", "script", "style", "input", "noscript", "select", "textarea", "img", "video", "audio", "iframe"}
_VOID = {"br", "hr", "img", "input", "meta", "link", "wbr"}
_COLOR = re.compile(r"(?:^|;)\s*color\s*:\s*(#[0-9a-fA-F]{6})")
_BG = re.compile(r"(?:^|;)\s*background(?:-color)?\s*:\s*(#[0-9a-fA-F]{6})")
_HEADS = {"h1": 14, "h2": 13, "h3": 11.5, "h4": 10.5, "h5": 10, "h6": 10}


def _style_color(style, rx):
    m = rx.search(style or "")
    return pdf._rgb(m.group(1)) if m else None


class _Blocks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.stack = []                 # [(tag, undo)] for the inline styles
        self.bold = self.italic = self.mono = 0
        self.colors = []
        self.runs = []                  # the paragraph being read (or the table cell)
        self.kind = "p"
        self.skip = 0
        self.ctx = []                   # shaded boxes / indents: {"indent", "bg"}
        self.lists = []                 # [kind, counter]
        self.code = None                # text of a code block being read
        self.table = None
        self.band = 0
        self.divs = []                  # what each open <div> is: "box", "band" or ""
        self.prefix = ""

    # ---- helpers
    def _ctx(self):
        indent = sum(c["indent"] for c in self.ctx)
        bg = next((c["bg"] for c in reversed(self.ctx) if c.get("bg")), None)
        return indent, bg

    def _flush(self):
        if self.table and self.table["cell"] is not None:
            return
        runs = self._tidy(self.runs)
        self.runs = []
        kind, self.kind = self.kind, "p"
        if not runs:
            return
        indent, bg = self._ctx()
        prefix, self.prefix = self.prefix, ""
        if prefix:
            runs = [(prefix, False, False, False, None)] + runs
        self.blocks.append({"t": "p", "runs": runs, "kind": kind, "indent": indent + 12 * len(self.lists), "bg": bg})

    def _tidy(self, runs):
        out = []
        for text, b, i, m, c in runs:
            text = re.sub(r"[ \t\r\f\v]+", " ", text.replace("\n", " ") if text != "\n" else text)
            if out and out[-1][0].endswith((" ", "\n")) and text.startswith(" "):
                text = text.lstrip(" ")
            if not text:
                continue
            out.append((text, b, i, m, c))
        while out and out[0][0].strip(" ") == "":
            out.pop(0)
        if out:
            out[0] = (out[0][0].lstrip(" "),) + out[0][1:]
        while out and out[-1][0].strip(" \n") == "":
            out.pop()
        if out:
            out[-1] = (out[-1][0].rstrip(" \n"),) + out[-1][1:]
        return [r for r in out if r[0] != ""]

    def _push(self, tag, bold=0, italic=0, mono=0, color=None):
        self.bold += bold
        self.italic += italic
        self.mono += mono
        if color:
            self.colors.append(color)
        self.stack.append((tag, bold, italic, mono, bool(color)))

    def _pop_to(self, tag):
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                while len(self.stack) > k:
                    t, b, i, m, c = self.stack.pop()
                    self.bold -= b
                    self.italic -= i
                    self.mono -= m
                    if c:
                        self.colors.pop()
                return

    # ---- parser
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        style = a.get("style") or ""
        if tag in _SKIP:
            if tag not in _VOID:
                self.skip += 1
            return
        if self.skip:
            return
        if self.code is not None:
            return
        if tag == "br":
            self.runs.append(("\n", 0, 0, 0, None))
            return
        if tag == "hr":
            self._flush()
            return
        color = _style_color(style, _COLOR)
        if tag == "div" and "code" in cls:
            self._flush()
            self.code = ""
            self.stack.append((tag, 0, 0, 0, False))
            return
        if tag == "pre":
            self._flush()
            self.code = ""
            self.stack.append((tag, 0, 0, 0, False))
            return
        if tag in ("p", "div", "section", "article", "header", "footer", "ul", "ol", "li", "blockquote",
                   "h1", "h2", "h3", "h4", "h5", "h6", "table", "tr") and not (tag == "p" and self.band):
            self._flush()
        if tag == "div":
            if "box" in cls:
                self.divs.append("box")
                self.ctx.append({"indent": 8, "bg": _style_color(style, _BG)})
            elif "band" in cls:
                self.divs.append("band")
                self.band += 1
            else:
                self.divs.append("")
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
        elif tag == "li":
            if self.lists:
                self.lists[-1][1] += 1
                self.prefix = (f"{self.lists[-1][1]}. " if self.lists[-1][0] == "ol" else "• ")
        elif tag == "p" and "label-p" in cls:
            self.kind = "label"
        elif tag in _HEADS:
            self.kind = tag
        elif tag == "table":
            self.table = {"rows": [], "row": None, "cell": None}
        elif tag == "tr" and self.table is not None:
            self.table["row"] = []
        elif tag in ("td", "th") and self.table is not None:
            self.table["cell"] = {"runs": [], "bg": _style_color(style, _BG), "head": tag == "th"}
            self.runs, self._saved_runs = self.table["cell"]["runs"], self.runs
        bold = 1 if tag in ("strong", "b", "th") or tag in _HEADS or (tag == "p" and "label-p" in cls) else 0
        italic = 1 if tag in ("em", "i", "cite", "blockquote") else 0
        mono = 1 if tag in ("code", "kbd", "samp", "tt") else 0
        if tag not in _VOID:
            self._push(tag, bold, italic, mono, color)

    def handle_endtag(self, tag):
        if self.skip:
            if tag in _SKIP:
                self.skip -= 1
            return
        if self.code is not None:
            if tag in ("div", "pre"):
                self._pop_to(tag)
                text, self.code = self.code, None
                indent, _ = self._ctx()
                self.blocks.append({"t": "code", "text": text, "indent": indent + 12 * len(self.lists)})
            return
        if tag in ("td", "th") and self.table is not None and self.table["cell"] is not None:
            cell = self.table["cell"]
            cell["runs"][:] = self._tidy(cell["runs"])
            self.runs = self._saved_runs
            self.table["cell"] = None
            if self.table["row"] is not None:
                self.table["row"].append(cell)
        elif tag == "tr" and self.table is not None and self.table["row"] is not None:
            if self.table["row"]:
                self.table["rows"].append(self.table["row"])
            self.table["row"] = None
        elif tag == "table" and self.table is not None:
            indent, _ = self._ctx()
            self.blocks.append({"t": "table", "rows": self.table["rows"], "indent": indent + 12 * len(self.lists)})
            self.table = None
        elif tag == "p" and self.table is not None and self.table["cell"] is not None:
            self.runs.append(("\n", 0, 0, 0, None))
        if tag == "b" and self.band:
            self.runs.append((" ", 1, 0, 0, None))
        if tag in ("p", "div", "section", "article", "header", "footer", "ul", "ol", "li", "blockquote",
                   "h1", "h2", "h3", "h4", "h5", "h6") and not (tag == "p" and self.band):
            self._flush()
        if tag == "div" and self.divs:
            what = self.divs.pop()
            if what == "box" and self.ctx:
                self.ctx.pop()
            elif what == "band":
                self.band -= 1
                self._flush()
        if tag in ("ul", "ol") and self.lists:
            self.lists.pop()
        self._pop_to(tag)

    def handle_data(self, data):
        if self.skip:
            return
        if self.code is not None:
            self.code += data
            return
        if not data:
            return
        color = self.colors[-1] if self.colors else None
        bold = bool(self.bold) or (self.band > 0)
        self.runs.append((data, bold, bool(self.italic), bool(self.mono), color))


def blocks(html):
    p = _Blocks()
    p.feed(html or "")
    p.close()
    p._flush()
    return p.blocks


def draw(doc, html, size=9.5, x=0.0):
    """Lay a runbook page's HTML out on the document, from the current position."""
    for b in blocks(html):
        ind = x + b.get("indent", 0)
        if b["t"] == "p":
            kind = b["kind"]
            if kind == "label":
                doc.space(4)
                doc.rich([(t, True, i, m, c) for t, _, i, m, c in b["runs"]], size=size - 1.2, x=ind, bg=b["bg"], keep=24)
            elif kind in _HEADS:
                doc.space(4)
                doc.rich(b["runs"], size=_HEADS[kind] - 2.5 + (size - 9.5), x=ind, bg=b["bg"], keep=24)
            else:
                doc.space(2.5)
                doc.rich(b["runs"], size=size, x=ind, bg=b["bg"])
        elif b["t"] == "code":
            doc.space(2)
            doc.code(b["text"], x=ind)
        elif b["t"] == "table":
            doc.space(3)
            doc.table(b["rows"], x=ind)
