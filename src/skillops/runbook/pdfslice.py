"""Cut a session out of the runbook's own PDF, so it keeps the runbook's original look.

Once per runbook version (when its PDF is attached or the version is published) `index` finds where every
part starts in the PDF. A part is found by the PDF's bookmarks when it has them (Word writes one per heading),
otherwise by its heading text, in document order. The result is small and kept with the version.

At download time `regions` gives, for a part, the stretch of PDF from its heading to the next heading after it
(a section runs to the end of its last action), and `add_slices` copies those stretches onto fresh pages without
re-drawing anything: each one is the original page, shown through a window. Page headers and footers that repeat
on every page are left out, and the stretches are stacked onto pages in the planned order.

Page layout only: nothing here knows about S3 or the plan.
"""
import io
import math
import re
from collections import Counter

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

MIN_PIECE = 6.0            # a stretch shorter than this (points) is just white space
EDGE = 10.0                # room kept above the first and below the last line of a page
CUT_OUTLINE = 3.0          # how far above a bookmark's top a part is cut
CUT_TEXT = 9.0             # at most how far above a heading's text a part is cut (less when the line above is close)


def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _box(page):
    mb = page.mediabox
    return [float(mb.left), float(mb.bottom), float(mb.width), float(mb.height)]


# ================================================================== reading the PDF

def _lines(page):
    """The page's text as lines, top to bottom: [{"text", "norm", "y", "top", "bot", "fs"}] (page coordinates)."""
    chunks = []

    def visit(text, cm, tm, font_dict, font_size):
        if not text or not text.strip():
            return
        try:
            y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
            x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
            scale = (math.hypot(tm[2], tm[3]) or 1.0) * (math.hypot(cm[2], cm[3]) or 1.0)
        except Exception:  # noqa: BLE001  (an odd matrix: skip the chunk)
            return
        chunks.append((x, y, abs(font_size or 10) * scale, text.strip()))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:  # noqa: BLE001  (a page whose text cannot be read still gets cut, by bookmarks)
        return []
    chunks.sort(key=lambda c: (-round(c[1], 1), c[0]))
    lines = []
    for x, y, fs, text in chunks:
        if lines and abs(lines[-1]["y"] - y) <= 0.45 * max(fs, lines[-1]["fs"]):
            lines[-1]["parts"].append((x, text))
            lines[-1]["fs"] = max(lines[-1]["fs"], fs)
        else:
            lines.append({"y": y, "fs": fs, "parts": [(x, text)]})
    out = []
    for ln in lines:
        text = " ".join(t for _, t in sorted(ln["parts"]))
        out.append({"text": text, "norm": _norm(text), "y": ln["y"], "fs": ln["fs"],
                    "top": ln["y"] + 0.85 * ln["fs"], "bot": ln["y"] - 0.25 * ln["fs"]})
    out.sort(key=lambda l: -l["y"])
    return out


def _frame(all_lines, geoms):
    """Header and footer lines (the same text, in the same place, on many pages): marked, and how deep they reach."""
    n = len(all_lines)
    top_gap = bot_gap = 18.0
    if n < 4:
        return top_gap, bot_gap
    count = Counter()
    keys = []
    for pi, lines in enumerate(all_lines):
        x0, y0, w, h = geoms[pi]
        seen, row = set(), []
        for ln in lines:
            rel = ln["y"] - y0
            if rel > h * 0.88 or rel < h * 0.12:
                key = (re.sub(r"\d+", "#", ln["text"].lower()).strip(), round(rel / 3))
                row.append((ln, key))
                if key not in seen:
                    seen.add(key)
                    count[key] += 1
        keys.append(row)
    need = max(3, math.ceil(0.4 * n))
    for pi, row in enumerate(keys):
        x0, y0, w, h = geoms[pi]
        for ln, key in row:
            if count[key] >= need:
                ln["frame"] = True
                if ln["y"] - y0 > h / 2:
                    top_gap = max(top_gap, (y0 + h) - ln["bot"] + 3)
                else:
                    bot_gap = max(bot_gap, ln["top"] - y0 + 3)
    return top_gap, bot_gap


def _fills(reader, page):
    """The filled rectangles of a page (x, y, w, h): the bands and boxes a heading sits in."""
    from pypdf.generic import ContentStream
    contents = page.get_contents()
    if contents is None:
        return []
    ctm, stack, pend, out = [1, 0, 0, 1, 0, 0], [], [], []

    def mul(a, b):
        return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
                a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]
    try:
        for operands, op in ContentStream(contents, reader).operations:
            o = op.decode() if isinstance(op, bytes) else op
            if o == "q":
                stack.append(ctm)
            elif o == "Q":
                ctm = stack.pop() if stack else ctm
            elif o == "cm":
                ctm = mul([float(x) for x in operands], ctm)
            elif o == "re":
                x, y, w, h = [float(v) for v in operands]
                x1, y1 = x * ctm[0] + y * ctm[2] + ctm[4], x * ctm[1] + y * ctm[3] + ctm[5]
                w2, h2 = w * ctm[0], h * ctm[3]
                pend.append((min(x1, x1 + w2), min(y1, y1 + h2), abs(w2), abs(h2)))
            elif o in ("f", "F", "f*", "B", "B*", "b", "b*"):
                out += pend
                pend = []
            elif o in ("n", "S", "s"):
                pend = []
    except Exception:  # noqa: BLE001  (a page we cannot read the shapes of: cut by its text alone)
        return out
    return out


def _text_cut(fills, line, prev_bot):
    """Where to cut above a heading line: above the band it sits in, inside the gap to the line above it."""
    base = line["top"]
    band = None
    for rx, ry, rw, rh in fills:
        top = ry + rh
        if rh <= 80 and rw >= 30 and ry - 1 <= line["y"] <= top + 1 and top > base and top - base <= 45:
            band = max(band or 0.0, top)
    if band:
        base = band
    pad = CUT_TEXT if prev_bot is None else max(0.0, min(CUT_TEXT, 0.65 * (prev_bot - base)))
    return base + pad


# ================================================================== finding each part

def _outline_anchors(reader):
    out = []

    def walk(items):
        for it in items:
            if isinstance(it, list):
                walk(it)
                continue
            try:
                p = reader.get_destination_page_number(it)
            except Exception:  # noqa: BLE001
                continue
            if p is None or p < 0:
                continue
            top = getattr(it, "top", None)
            try:
                top = float(top) if top is not None else None
            except (TypeError, ValueError):
                top = None
            out.append({"norm": _norm(getattr(it, "title", "") or ""), "p": p, "y": top})

    try:
        walk(reader.outline)
    except Exception:  # noqa: BLE001
        return []
    return out


def _hit_outline(t, a):
    return bool(a) and (a == t or (len(a) >= 8 and len(t) >= 8 and (a in t or t in a)))


def _hit_text(t, lines, j):
    l = lines[j]["norm"]
    k = min(len(t), 40)
    for cand in (l, l + (lines[j + 1]["norm"] if j + 1 < len(lines) else "")):
        i = cand.find(t[:k])
        # a heading that wraps starts on this line (at least a few characters of it), not on the next one
        if 0 <= i <= 12 and (cand is l or i + 5 <= len(l)):
            return True
    return len(l) >= 10 and t.startswith(l)


def _match(titles, anchors, hit):
    found, ptr = {}, 0
    for nid, t in titles:
        if len(t) < 4:
            continue
        for j in range(ptr, len(anchors)):
            if hit(t, anchors, j):
                found[nid] = anchors[j]
                ptr = j + 1
                break
    return found


def index(pdf_bytes, pub):
    """Where each page of the published runbook starts in its PDF. Returns a small JSON-able dict."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    if reader.is_encrypted:
        raise ValueError("The PDF is password-protected.")
    pages = list(reader.pages)
    if not pages:
        raise ValueError("The PDF has no pages.")
    geoms = [_box(p) for p in pages]
    all_lines = [_lines(p) for p in pages]
    top_gap, bot_gap = _frame(all_lines, geoms)
    titles = [(n["id"], _norm(n["title"])) for n in pub["nodes"] if n.get("page")]
    total = len(titles)

    best, mode = {}, ""
    out_anchors = _outline_anchors(reader)
    if out_anchors:
        for a in out_anchors:
            if a["y"] is None:
                a["y"] = geoms[a["p"]][1] + geoms[a["p"]][3]
        found = _match(titles, out_anchors, lambda t, an, j: _hit_outline(t, an[j]["norm"]))
        best, mode = {nid: (a["p"], a["y"] + CUT_OUTLINE) for nid, a in found.items()}, "bookmarks"
    if len(best) < 0.7 * total:
        text_norms = ["".join(l["norm"] for l in lines) for lines in all_lines]
        toc = set()                  # contents pages: they list many titles, and little else
        for pi, tn in enumerate(text_norms):
            hits = sum(1 for _, t in titles if len(t) >= 5 and t[:30] in tn)
            real = sum(1 for l in all_lines[pi] if not l.get("frame")) or 1
            if hits >= 10 or (hits >= 4 and hits >= 0.6 * real):
                toc.add(pi)
        anchors = []
        for pi, lines in enumerate(all_lines):
            if pi in toc:
                continue
            body = [l for l in lines if not l.get("frame")]
            for j, l in enumerate(body):
                above = body[j - 1]["bot"] if j else None          # the line just above: the cut goes between the two
                anchors.append({"norm": l["norm"], "p": pi, "line": l, "prev": above, "_i": (pi, j)})
        # a heading is matched on its own line (and the next, when it wraps): give the matcher the lines of its page
        by_page = {}
        for pi, lines in enumerate(all_lines):
            by_page[pi] = [l for l in lines if not l.get("frame")]

        def hit(t, an, j):
            a = an[j]
            pl = by_page[a["p"]]
            k = a["_i"][1]
            return _hit_text(t, pl, k)
        found = _match(titles, anchors, hit)
        fills = {}
        text_best = {}
        for nid, a in found.items():
            if a["p"] not in fills:
                fills[a["p"]] = _fills(reader, pages[a["p"]])
            text_best[nid] = (a["p"], _text_cut(fills[a["p"]], a["line"], a["prev"]))
        if len(text_best) > len(best):
            best, mode = text_best, "text"
    nodes = {}
    for nid, (p, y) in best.items():
        x0, y0, w, h = geoms[p]
        nodes[nid] = [p, round(min(y, y0 + h - top_gap), 2)]       # never above the page's header
    span = []                                          # where the page's own text starts and ends (its blank edges are trimmed)
    for pi, lines in enumerate(all_lines):
        body = [l for l in lines if not l.get("frame")]
        span.append([round(max(l["top"] for l in body), 2), round(min(l["bot"] for l in body), 2)] if body else None)
    return {"v": 2, "mode": mode, "found": len(nodes), "total": total, "pages": geoms, "span": span,
            "top_gap": round(top_gap, 2), "bot_gap": round(bot_gap, 2), "nodes": nodes}


# ================================================================== which stretches belong to a part

def regions(pub, mapping, node_id):
    """[(page, top, bottom)] (page coordinates, top > bottom) from the part's heading to the next heading after it.
    None when the part was not found in the PDF."""
    nodes = mapping.get("nodes") or {}
    if node_id not in nodes or not mapping.get("pages"):
        return None
    order = [n for n in pub["nodes"] if n.get("page")]
    pos = {n["id"]: k for k, n in enumerate(order)}
    if node_id not in pos:
        return None
    index_ = pub["index"]

    def under(n, anc):
        seen = 0
        while n and n.get("parent") and seen < 20:
            if n["parent"] == anc:
                return True
            n = index_.get(n["parent"])
            seen += 1
        return False

    j = pos[node_id] + 1
    while j < len(order) and under(order[j], node_id):
        j += 1
    end = None
    while j < len(order):
        if order[j]["id"] in nodes:
            end = nodes[order[j]["id"]]
            break
        j += 1
    p1, y1 = nodes[node_id]
    pages = mapping["pages"]
    last = end[0] if end else len(pages) - 1
    if last < p1 or (end and end[0] == p1 and end[1] >= y1):
        return None
    out = []
    for p in range(p1, last + 1):
        x0, y0, w, h = pages[p]
        body_top, body_bot = y0 + h - mapping["top_gap"], y0 + mapping["bot_gap"]
        sp = (mapping.get("span") or [None] * len(pages))[p]
        if sp:                                              # a page's blank top and bottom are not part of the part
            body_top, body_bot = min(body_top, sp[0] + EDGE), max(body_bot, sp[1] - EDGE)
        top = y1 if p == p1 else body_top
        bottom = end[1] if (end and p == last) else body_bot
        if top - bottom > MIN_PIECE:
            out.append((p, round(top, 2), round(bottom, 2)))
    return out or None


# ================================================================== putting the stretches on pages

def _add(writer, obj):
    return writer._add_object(obj)


def add_slices(writer, pdf_bytes, mapping, groups, bottom_room=50.0, gap=16.0):
    """Copy stretches of the source PDF onto new pages of `writer`. `groups`: one list of (page, top, bottom) per part;
    parts follow each other down the page, with a gap, and start a new page when the next stretch does not fit.
    Returns the number of pages added."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    geoms = mapping["pages"]
    pieces = [pc for g in groups for pc in g]
    if not pieces:
        return 0
    W, H = geoms[pieces[0][0]][2], geoms[pieces[0][0]][3]
    tallest = max(top - bottom for _, top, bottom in pieces)
    top_room = 30.0
    bottom_room = max(8.0, min(bottom_room, H - top_room - tallest))
    forms = {}

    def form(p):
        if p not in forms:
            src = reader.pages[p]
            x0, y0, w, h = geoms[p]
            contents = src.get_contents()
            data = contents.get_data() if contents is not None else b""
            res = src.get("/Resources")
            res = res.get_object().clone(writer) if res is not None else DictionaryObject()
            f = DecodedStreamObject()
            f.set_data(data)
            f.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                      NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (x0, y0, x0 + w, y0 + h)]),
                      NameObject("/Resources"): res})
            try:
                f = f.flate_encode()
            except Exception:  # noqa: BLE001
                pass
            forms[p] = _add(writer, f)
        return forms[p]

    sheets, ops, used, cursor = [], [], {}, 0.0

    def finish():
        nonlocal ops, used
        if ops:
            sheets.append(("\n".join(ops), dict(used)))
        ops, used = [], {}

    def fresh():
        nonlocal cursor
        finish()
        cursor = H - top_room

    cursor = H - top_room
    for gi, g in enumerate(groups):
        started = False
        for p, top, bottom in g:
            ph = top - bottom
            if ops and cursor - ph - (gap if not started else 0.0) < bottom_room:
                fresh()
            elif ops and not started:
                cursor -= gap
            x0, y0, w, h = geoms[p]
            name = "Fm%d" % p
            used[name] = form(p)
            ops.append("q 1 0 0 1 %.2f %.2f cm %.2f %.2f %.2f %.2f re W n /%s Do Q"
                       % (-x0, cursor - top, x0, bottom, w, ph, name))
            cursor -= ph
            started = True
    finish()
    for content, xo in sheets:
        page = writer.add_blank_page(width=W, height=H)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/XObject"): DictionaryObject({NameObject("/" + k): v for k, v in xo.items()})})
        cs = DecodedStreamObject()
        cs.set_data(content.encode("latin-1"))
        page[NameObject("/Contents")] = _add(writer, cs)
    return len(sheets)


def stamp_numbers(writer, footer):
    """'Page n of N' and a footer line on every A4 page of the result (other sizes are left alone)."""
    from ..core import pdf as mypdf
    n = len(writer.pages)
    doc = mypdf.Document("stamp", footer=footer)
    doc.pages = [[] for _ in range(n)]
    over = PdfReader(io.BytesIO(doc.render()))
    for i, page in enumerate(writer.pages):
        mb = page.mediabox
        if abs(float(mb.width) - mypdf.PAGE_W) < 8 and abs(float(mb.height) - mypdf.PAGE_H) < 8:
            page.merge_page(over.pages[i])


def assemble(parts, footer=""):
    """parts: [{"kind": "pdf", "bytes"} or {"kind": "slices", "src", "mapping", "groups"}] in order -> PDF bytes."""
    writer = PdfWriter()
    for part in parts:
        if part["kind"] == "pdf":
            writer.append(PdfReader(io.BytesIO(part["bytes"])))
        else:
            add_slices(writer, part["src"], part["mapping"], part["groups"])
    stamp_numbers(writer, footer)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
