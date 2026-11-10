"""Build small .docx files for tests, drawn like a Robomy runbook (heading styles, coloured bands,
command strips, decision chips, code blocks, numbered lists, an image)."""
import base64
import io
import zipfile
from xml.sax.saxutils import escape

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFBQIAX8jx0gAAAABJRU5ErkJggg==")
FILLS = {"SAY": "2B7DE9", "DO": "16233D", "SHOW": "0E7C74", "CHECK": "2C7A4B", "ASK": "B8710B"}


def run(text, bold=False, font=None, color=None):
    rpr = ""
    if bold or font or color:
        rpr = "<w:rPr>" + ("<w:b/>" if bold else "") + (f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}"/>' if font else "") + \
              (f'<w:color w:val="{color}"/>' if color else "") + "</w:rPr>"
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(text, style=None, bold=False, num=None, font=None):
    ppr = ""
    if style or num:
        ppr = "<w:pPr>" + (f'<w:pStyle w:val="{style}"/>' if style else "") + \
              (f'<w:numPr><w:ilvl w:val="0"/><w:numId w:val="{num}"/></w:numPr>' if num else "") + "</w:pPr>"
    return f"<w:p>{ppr}{run(text, bold, font)}</w:p>"


def cell(inner, fill=None):
    shd = f'<w:tcPr><w:shd w:val="clear" w:fill="{fill}"/></w:tcPr>' if fill else ""
    return f"<w:tc>{shd}{inner}</w:tc>"


def band(left, right, fill, right_fill=None, left_font=None):
    return ("<w:tbl><w:tr>" + cell(para(left, bold=True, font=left_font), fill) + cell(para(right), right_fill) +
            "</w:tr></w:tbl>")


def code(text):
    lines = "".join(para(line, font="Consolas") for line in text.split("\n"))
    return f"<w:tbl><w:tr>{cell(lines, 'F7F9FC')}</w:tr></w:tbl>"


def image():
    return ('<w:p><w:r><w:drawing><wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing">'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData>'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:blipFill>'
            '<a:blip r:embed="rIdImg1"/></pic:blipFill></pic:pic></a:graphicData></a:graphic></wp:inline>'
            "</w:drawing></w:r></w:p>")


def runbook(steps=2, parts=2, actions=3, extra_title="", drop=None):
    """A runbook with `steps` Heading 1 steps, each with `parts` PART bands, each with `actions` action bands.
    Action IDs are like 1.1, 1.2 ... per step. `drop` = set of action IDs to leave out (for version tests)."""
    drop = drop or set()
    body = [para("Front matter", style="Heading2"), para("How to use this document.")]
    words = list(FILLS)
    for s in range(1, steps + 1):
        body.append(para(f"STEP {s} — Topic {s}{extra_title}", style="Heading1"))
        body.append(para(f"Intro text for step {s}."))
        n = 0
        for p in range(1, parts + 1):
            body.append(band(f"PART {p}", f"Part {p} of step {s}", "2B7DE9"))
            for a in range(1, actions + 1):
                n += 1
                aid = f"{s}.{n}"
                if aid in drop:
                    continue
                w = words[(n - 1) % len(words)]
                body.append(band(w, f"{aid} Action {aid} title", FILLS[w], "EEF2F8"))
                body.append(para(f"Body of action {aid}. Special word: needle{aid}."))
                body.append(band("1", "First sub-step", "16233D"))
                body.append(para("Item one", num="1"))
                body.append(para("Item two", num="1"))
                if n == 1:
                    body.append(band("git status", "shows what changed", "1E2A3A", left_font="Consolas"))
                    body.append(band("REJECT H₀", "The difference is real.", "2B5DA8"))
                    body.append(code("SELECT *\nFROM orders;"))
                    body.append(para("THE TASK", bold=True))
                    body.append(image())
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
           'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><w:body>' +
           "".join(body) + "</w:body></w:document>")
    styles = ('<?xml version="1.0" encoding="UTF-8"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
              '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:pPr><w:outlineLvl w:val="0"/></w:pPr></w:style>'
              '<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:pPr><w:outlineLvl w:val="1"/></w:pPr></w:style>'
              "</w:styles>")
    numbering = ('<?xml version="1.0" encoding="UTF-8"?><w:numbering xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                 '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/></w:lvl></w:abstractNum>'
                 '<w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rIdImg1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/image1.png"/>'
            "</Relationships>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("word/document.xml", doc)
        z.writestr("word/styles.xml", styles)
        z.writestr("word/numbering.xml", numbering)
        z.writestr("word/_rels/document.xml.rels", rels)
        z.writestr("word/media/image1.png", PNG)
    return buf.getvalue()
