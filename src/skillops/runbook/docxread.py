"""Read a Word .docx file directly from its XML (no outside libraries).

A .docx is a zip file. word/document.xml holds the body: paragraphs (w:p) and tables (w:tbl) in
order. Most formatting in Robomy runbooks is applied directly (not through styles), and the
meaning is often in table cell colours, so we keep all of that.

Docx(data).blocks() gives one small summary per body element (used to find the structure).
The renderer (render.py) goes back to the XML elements to make the HTML.
"""
import io
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
V = "{urn:schemas-microsoft-com:vml}"
PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"

MONO_FONTS = ("consolas", "courier", "cascadia", "menlo", "monaco", "lucida console", "source code",
              "fira code", "jetbrains", "roboto mono", "dejavu sans mono")


class DocxError(Exception):
    pass


def wval(el, path, attr="val"):
    if el is None:
        return None
    x = el.find(path)
    return None if x is None else x.get(W + attr)


def on(el, path):
    """True for an on/off property such as <w:b/> (but not <w:b w:val="0"/>)."""
    if el is None:
        return False
    x = el.find(path)
    if x is None:
        return False
    v = x.get(W + "val")
    return v not in ("0", "false", "off")


def run_text(r):
    out = []
    for c in r:
        tag = c.tag
        if tag == W + "t":
            out.append(c.text or "")
        elif tag == W + "tab":
            out.append("\t")
        elif tag in (W + "br", W + "cr"):
            out.append("\n")
        elif tag == W + "noBreakHyphen":
            out.append("-")
        elif tag == W + "sym":
            out.append("")
    return "".join(out)


def para_text(p):
    return "".join(run_text(r) for r in p.iter(W + "r"))


def fill_of(el):
    """Background colour of a cell or paragraph (hex, upper case) or None."""
    if el is None:
        return None
    shd = el.find(W + "shd")
    if shd is None:
        return None
    f = (shd.get(W + "fill") or "").upper()
    return None if f in ("", "AUTO", "FFFFFF") else f


class Docx:
    def __init__(self, data):
        try:
            self.zip = zipfile.ZipFile(io.BytesIO(data))
            self.doc = ET.fromstring(self.zip.read("word/document.xml"))
        except (zipfile.BadZipFile, KeyError, ET.ParseError) as e:
            raise DocxError("This file is not a Word .docx document (or it is damaged).") from e
        self.body = self.doc.find(W + "body")
        self.elements = [el for el in self.body if el.tag in (W + "p", W + "tbl")]
        self.rels = self._rels()
        self.styles = self._styles()
        self.numbering = self._numbering()

    # ---------------------------------------------------------------- parts
    def _read(self, name):
        try:
            return ET.fromstring(self.zip.read(name))
        except KeyError:
            return None

    def _rels(self):
        root = self._read("word/_rels/document.xml.rels")
        out = {}
        if root is not None:
            for r in root.findall(PKG_REL + "Relationship"):
                out[r.get("Id")] = {"target": r.get("Target"), "type": r.get("Type", "").rsplit("/", 1)[-1],
                                    "external": r.get("TargetMode") == "External"}
        return out

    def _styles(self):
        root = self._read("word/styles.xml")
        out = {}
        if root is None:
            return out
        for s in root.findall(W + "style"):
            sid = s.get(W + "styleId")
            name = wval(s, W + "name") or sid
            ppr = s.find(W + "pPr")
            rpr = s.find(W + "rPr")
            fonts = rpr.find(W + "rFonts") if rpr is not None else None
            out[sid] = {"name": name, "outline": wval(ppr, W + "outlineLvl"), "based": wval(s, W + "basedOn"),
                        "bold": on(rpr, W + "b"), "size": wval(rpr, W + "sz"),
                        "font": (fonts.get(W + "ascii") if fonts is not None else None)}
        return out

    def _numbering(self):
        root = self._read("word/numbering.xml")
        abstract, nums = {}, {}
        if root is None:
            return nums
        for an in root.findall(W + "abstractNum"):
            levels = {}
            for lvl in an.findall(W + "lvl"):
                levels[int(lvl.get(W + "ilvl", "0"))] = {"fmt": wval(lvl, W + "numFmt") or "decimal",
                                                          "start": int(wval(lvl, W + "start") or 1)}
            abstract[an.get(W + "abstractNumId")] = levels
        for n in root.findall(W + "num"):
            nums[n.get(W + "numId")] = abstract.get(wval(n, W + "abstractNumId"), {})
        return nums

    def style_name(self, sid):
        return (self.styles.get(sid) or {}).get("name", sid or "")

    def outline_level(self, sid):
        seen = 0
        while sid and seen < 10:
            st = self.styles.get(sid) or {}
            if st.get("outline") is not None:
                return int(st["outline"])
            sid = st.get("based")
            seen += 1
        return None

    def media(self, rid):
        rel = self.rels.get(rid)
        if not rel or rel["external"]:
            return None, None
        path = posixpath.normpath(posixpath.join("word", rel["target"]))
        try:
            return path, self.zip.read(path)
        except KeyError:
            return None, None

    # ---------------------------------------------------------------- summaries
    def run_info(self, r, pstyle=None):
        rpr = r.find(W + "rPr")
        fonts = rpr.find(W + "rFonts") if rpr is not None else None
        font = (fonts.get(W + "ascii") or fonts.get(W + "hAnsi")) if fonts is not None else None
        if not font and pstyle:
            font = (self.styles.get(pstyle) or {}).get("font")
        sz = wval(rpr, W + "sz")
        return {"bold": on(rpr, W + "b"), "caps": on(rpr, W + "caps"), "size": int(sz) if sz and sz.isdigit() else None,
                "font": font or ""}

    def is_mono(self, el):
        for r in el.iter(W + "r"):
            if not run_text(r).strip():
                continue
            f = self.run_info(r)["font"].lower()
            return any(m in f for m in MONO_FONTS)
        return False

    def blocks(self):
        out = []
        for i, el in enumerate(self.elements):
            if el.tag == W + "p":
                out.append(self._para_block(i, el))
            else:
                out.append(self._table_block(i, el))
        return out

    def _para_block(self, i, p):
        ppr = p.find(W + "pPr")
        sid = wval(ppr, W + "pStyle")
        text = para_text(p)
        runs = [self.run_info(r, sid) for r in p.iter(W + "r") if run_text(r).strip()]
        style_bold = (self.styles.get(sid) or {}).get("bold", False)
        outline = wval(ppr, W + "outlineLvl")
        num = ppr.find(W + "numPr") if ppr is not None else None
        sizes = [r["size"] for r in runs if r["size"]]
        return {
            "i": i, "kind": "p", "text": text.strip(), "style": self.style_name(sid) if sid else "",
            "outline": int(outline) if outline is not None else self.outline_level(sid),
            "bold": bool(runs) and all(r["bold"] or style_bold for r in runs),
            "caps": bool(runs) and (all(r["caps"] for r in runs) or (text.strip().upper() == text.strip() and
                                                                     any(c.isalpha() for c in text))),
            "size": max(sizes) if sizes else None,
            "num": [wval(num, W + "numId"), int(wval(num, W + "ilvl") or 0)] if num is not None else None,
            "image": p.find(".//" + A + "blip") is not None,
        }

    def _table_block(self, i, t):
        rows = t.findall(W + "tr")
        first = rows[0].findall(W + "tc") if rows else []
        b = {"i": i, "kind": "tbl", "rows": len(rows), "cols": len(first), "text": ""}
        if len(rows) == 1 and len(first) in (1, 2):
            texts = ["\n".join(para_text(p) for p in tc.iter(W + "p")).strip() for tc in first]
            b["left"] = texts[0]
            b["right"] = texts[1] if len(first) == 2 else ""
            b["fill"] = fill_of(first[0].find(W + "tcPr"))
            b["fill2"] = fill_of(first[1].find(W + "tcPr")) if len(first) == 2 else None
            b["mono"] = self.is_mono(first[0])
            b["text"] = (b["left"] + " " + b["right"]).strip()
        else:
            b["text"] = " ".join(para_text(p) for p in t.iter(W + "p"))[:400]
        return b


SPACE = re.compile(r"\s+")


def short(text, n=80):
    t = SPACE.sub(" ", text or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"
