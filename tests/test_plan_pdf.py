"""The prepared plan as a PDF."""
from skillops.core import pdf


def test_the_pdf_writer_makes_a_valid_multi_page_file():
    doc = pdf.Document("T", footer="Batch A")
    for i in range(120):
        doc.text(f"Line {i} (with brackets) and a very long run of words to wrap " * 3, size=10)
    data = doc.render()
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert data.count(b"/Type /Page ") >= 3
    assert b"Page 1 of" in data


def test_wrapping_splits_long_words_and_keeps_to_the_width():
    for line in pdf.wrap("x" * 400 + " short words here", 10, 200):
        assert pdf.width(line, 10) <= 200


def test_text_the_fonts_cannot_show_does_not_break_the_file():
    doc = pdf.Document("T")
    doc.text("Tamil தமிழ் and arrows → ✓ and “quotes” — dash")
    assert doc.render().startswith(b"%PDF")


SAMPLE = (
    '<p class="label-p"><span style="color:#2B7DE9"><strong>WHAT IT IS</strong></span></p>'
    '<p><span style="color:#1F4E8C"><code>practice.db</code></span><span style="color:#333333"> is a small SQLite file. '
    'It holds <em>four</em> tables.</span></p>'
    '<div class="box" style="background:#EFF5FD;border-left:4px solid #EFF5FD"><p><strong>Remember</strong> the order.</p></div>'
    '<div class="band"><b style="background:#16233D">1</b><span style="background:#E8EEF6;"><p>Open it in sqlime</p></span></div>'
    '<ul><li>First point</li><li>Second point</li></ul>'
    '<div class="code" style="background:#F7F9FC"><button type="button" class="copy" data-copy="">Copy</button>'
    '<code>SELECT name\nFROM sqlite_master\nWHERE type = \'table\';</code></div>'
    '<div class="table-wrap"><table><tbody>'
    '<tr><td style="background:#E4EAF2"><p><strong>Table</strong></p></td><td style="background:#E4EAF2"><p><strong>Rows</strong></p></td></tr>'
    '<tr><td><p><code>shop_customers</code></p></td><td><p>6</p><p>second line</p></td></tr>'
    '</tbody></table></div>'
)


def test_runbook_html_becomes_blocks_in_reading_order():
    from skillops.core import pdfhtml
    kinds = [b["t"] + (":" + b["kind"] if b["t"] == "p" else "") for b in pdfhtml.blocks(SAMPLE)]
    assert kinds == ["p:label", "p:p", "p:p", "p:p", "p:p", "p:p", "code", "table"]
    bl = pdfhtml.blocks(SAMPLE)
    assert "".join(r[0] for r in bl[0]["runs"]) == "WHAT IT IS"
    assert "".join(r[0] for r in bl[3]["runs"]).startswith("1 Open it")          # a numbered band reads on one line
    assert "".join(r[0] for r in bl[4]["runs"]) == "\u2022 First point"
    assert bl[6]["text"].startswith("SELECT name\nFROM sqlite_master") and "Copy" not in bl[6]["text"]
    assert [[c["runs"][0][0] for c in row] for row in bl[7]["rows"]] == [["Table", "Rows"], ["shop_customers", "6"]]


def test_the_content_is_laid_out_in_the_pdf():
    from skillops.core import pdfhtml
    doc = pdf.Document("T")
    pdfhtml.draw(doc, SAMPLE * 12)
    data = doc.render()
    assert data.startswith(b"%PDF") and data.count(b"/Type /Page ") >= 2
    for word in (b"WHAT IT IS", b"sqlite_master", b"shop_customers", b"Second point"):
        assert word in data
    assert b"Copy" not in data


def test_the_rupee_sign_and_maths_symbols_are_written_in_letters():
    assert pdf._pdf_text("\u20b9500 \u2265 5 and \u2260 6") == "Rs 500 >= 5 and != 6"
