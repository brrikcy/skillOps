"""Cutting a session out of the runbook's own PDF."""
import io
import re

from pypdf import PdfReader, PdfWriter

from skillops.core import pdf
from skillops.runbook import pdfslice

TITLES = [("n1", "Setting up the practice database", "", False), ("n2", "1.1 Install the tool", "n1", True),
          ("n3", "1.2 Open the practice file", "n1", True), ("n4", "Asking questions of the data", "", False),
          ("n5", "2.1 Count the orders per customer", "n4", True), ("n6", "2.2 Find the books still out", "n4", True)]


def _pub():
    nodes = [{"id": i, "title": t, "parent": p, "page": True, "checkable": c, "children": []} for i, t, p, c in TITLES]
    by = {n["id"]: n for n in nodes}
    for n in nodes:
        if n["parent"]:
            by[n["parent"]]["children"].append(n["id"])
    return {"nodes": nodes, "index": by}


def _runbook_pdf(bookmarks=True, toc=True, lines=40):
    """A runbook as Word would save it: a contents page, headings, a footer that repeats on every page."""
    doc = pdf.Document("Runbook", footer="Airtel runbook")
    if toc:
        doc.text("Contents", size=18, bold=True)
        for _, title, _, _ in TITLES:
            doc.text(title + " ........ 3", size=10)
        doc.pages.append([])
        doc.y = pdf.PAGE_H - pdf.MARGIN
    for nid, title, parent, check in TITLES:
        doc.space(10)
        doc.need(120)
        if bookmarks:
            doc.bookmark(title, gap=4)
        doc.text(title, size=11 if check else 15, bold=True)
        for k in range(lines):
            doc.text(f"{nid} body line {k}: the quick brown fox jumps over the lazy dog again and again.", size=9)
    return doc.render()


def _where(m, nid):
    p, y = m["nodes"][nid]
    return (p, -y)


def test_parts_are_found_by_the_bookmarks_and_the_contents_page_is_not_mistaken_for_them():
    m = pdfslice.index(_runbook_pdf(bookmarks=True), _pub())
    assert m["mode"] == "bookmarks" and m["found"] == m["total"] == 6
    places = [_where(m, n[0]) for n in TITLES]
    assert places == sorted(places)                                              # in reading order
    assert m["top_gap"] >= 18 and m["bot_gap"] > 30                              # the repeating footer is noticed


def test_parts_are_found_by_their_heading_text_when_the_pdf_has_no_bookmarks():
    m = pdfslice.index(_runbook_pdf(bookmarks=False, toc=True), _pub())
    assert m["mode"] == "text" and m["found"] == 6
    assert m["nodes"]["n1"][0] >= 1                                              # not the contents page (page 0)
    places = [_where(m, n[0]) for n in TITLES]
    assert places == sorted(places)


def test_a_part_runs_from_its_heading_to_the_next_heading_and_a_section_to_the_end_of_its_last_action():
    pub, data = _pub(), _runbook_pdf()
    m = pdfslice.index(data, pub)
    act = pdfslice.regions(pub, m, "n2")
    sec = pdfslice.regions(pub, m, "n1")
    assert act and sec
    assert act[0][:2] == (m["nodes"]["n2"][0], m["nodes"]["n2"][1])
    nxt = m["nodes"]["n3"]
    assert (act[-1][0], act[-1][2]) == (nxt[0], nxt[1]) or act[-1][0] < nxt[0]   # it stops where the next part starts
    assert sec[0][:2] == (m["nodes"]["n1"][0], m["nodes"]["n1"][1])
    assert (sec[-1][0], sec[-1][2]) == (m["nodes"]["n4"][0], m["nodes"]["n4"][1])
    assert len(sec) > len(act)
    assert all(top > bottom for _, top, bottom in sec)
    assert pdfslice.regions(pub, {"nodes": {}, "pages": m["pages"]}, "n2") is None   # not found -> the caller falls back


def test_the_stretches_are_copied_onto_new_pages_in_the_planned_order():
    pub, data = _pub(), _runbook_pdf()
    m = pdfslice.index(data, pub)
    groups = [pdfslice.regions(pub, m, "n5"), pdfslice.regions(pub, m, "n2")]    # planned: 2.1 first, then 1.1
    out = pdfslice.assemble([{"kind": "slices", "src": data, "mapping": m, "groups": groups}], footer="Batch A")
    r = PdfReader(io.BytesIO(out))
    assert len(r.pages) >= 2
    clips = []
    for page in r.pages:
        ops = page.get_contents().get_data().decode("latin-1")
        for mo in re.finditer(r"q 1 0 0 1 [-\d.]+ ([-\d.]+) cm [-\d.]+ ([-\d.]+) [-\d.]+ ([-\d.]+) re W n /Fm(\d+) Do Q", ops):
            clips.append((int(mo.group(4)), float(mo.group(2)), float(mo.group(3))))
    want = [(p, bottom, top - bottom) for g in groups for p, top, bottom in g]
    assert [(p, round(b, 1), round(h, 1)) for p, b, h in clips] == [(p, round(b, 1), round(h, 1)) for p, b, h in want]
    first = r.pages[0].extract_text()
    assert "2.1 Count the orders" in first and "Page 1 of" in first and "Batch A" in first


def test_stretches_never_run_off_the_page():
    pub, data = _pub(), _runbook_pdf()
    m = pdfslice.index(data, pub)
    out = pdfslice.assemble([{"kind": "slices", "src": data, "mapping": m, "groups": [pdfslice.regions(pub, m, "n1")]}])
    r = PdfReader(io.BytesIO(out))
    H = float(r.pages[0].mediabox.height)
    for page in r.pages:
        ops = page.get_contents().get_data().decode("latin-1")
        used = 0.0
        for mo in re.finditer(r"cm [-\d.]+ [-\d.]+ [-\d.]+ ([-\d.]+) re W n", ops):
            used += float(mo.group(1))
        assert used <= H - 40


def test_the_supervisor_attaches_the_runbook_pdf_to_a_version(boss):
    import docx_factory
    from helpers import body, setup_org
    from skillops.core import jobs
    from skillops.runbook import detect, service
    setup_org()
    jobs.INLINE = True
    try:
        rid = service.create_runbook("Sample")
        vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=2, parts=2, actions=2), "boss")
        mapping, checkable = detect.suggest(service.get_version(vid)["patterns"])
        service.save_mapping(vid, mapping, checkable, [])
        service.publish(vid, "boss")
        pub = service.published(vid)
        doc = pdf.Document("R", footer="R")
        for n in pub["nodes"]:
            if n.get("page"):
                doc.need(80)
                doc.bookmark(n["title"], gap=4)
                doc.text(n["title"], size=12, bold=True)
                doc.text("some text", size=9)
        data = doc.render()
        boundary = "----skillops"

        def post(filename, payload):
            raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\n{boss.csrf}\r\n"
                   f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
                   "Content-Type: application/pdf\r\n\r\n").encode() + payload + f"\r\n--{boundary}--\r\n".encode()
            return boss.request("POST", f"/admin/runbooks/{rid}/v/{vid}/pdf", raw=raw,
                                content_type=f"multipart/form-data; boundary={boundary}")

        assert post("notes.txt", b"hello")["statusCode"] == 400               # not a PDF
        assert post("runbook.pdf", b"%PDF-1.4 broken")["statusCode"] == 400   # damaged
        assert post("runbook.pdf", data)["statusCode"] == 303
    finally:
        jobs.INLINE = False
    v = service.get_version(vid)
    assert v["pdf_name"] == "runbook.pdf" and v["pdf_found"] == v["pdf_total"] > 0
    page = body(boss.get(f"/admin/runbooks/{rid}"))
    assert "runbook.pdf" in page and "pages found in the PDF" in page
