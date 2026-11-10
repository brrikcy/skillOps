"""Find the structure of a runbook from the document itself.

Every body block gets a "signature" that describes how it is drawn (not what it says):
  P|style:Heading 1            a paragraph with a heading style or outline level
  P|lead:STEP                  a short paragraph that starts with a numbered word ("STEP 2 ...")
  P|bold-caps / P|bold:28      a short paragraph that is all bold (and all capitals / font size)
  T|section:PART|2B7DE9        a one-row, two-cell table whose left cell is "<WORD> <number>"
  T|action                     ... whose left cell is one capital word and right cell starts with an ID (2.4, K.1)
  T|marker|16233D              ... whose left cell is a short marker: 1, A, 2.1, 1-2, OK, FIX, ALL
  T|command|1E2A3A             ... whose left cell looks like a command (small letters, symbols)
  T|phrase|2B5DA8              ... whose left cell is a capital phrase (REJECT H0)
  T|band|<fill>                any other one-row, two-cell table

Blocks with the same signature form a pattern. The supervisor maps each pattern to Level 1-4 or
"content". suggest() proposes a mapping; the supervisor confirms it.
"""
import re

from .docxread import short

ID_RE = re.compile(r"^((?:\d+[A-Z]?|[A-Z])\.\d+[a-z]?)\b")
SECTION_RE = re.compile(r"^([A-Za-z]+)\s+\d+[A-Za-z]?$")
LEAD_RE = re.compile(r"^(STEP|PART|MODULE|UNIT|SECTION|CHAPTER|PHASE|LESSON|WEEK|DAY)\s+\d+[A-Z]?\b", re.I)
MARKER_RE = re.compile(r"^(\d{1,3}|[A-Z]|\d+(?:\.\d+)+|\d+\s*[–—-]\s*\d+|[A-Z]{2,4})$")
WORD_RE = re.compile(r"^[A-Z]{2,10}$")


def signature(b):
    """(signature, kind) for a block, or (None, None) if it cannot be a heading."""
    if b["kind"] == "p":
        text = b["text"]
        if not text:
            return None, None
        style = b.get("style") or ""
        if style.lower().startswith("heading") or style.lower() == "title" or b.get("outline") is not None:
            return f"P|style:{style or 'outline ' + str(b.get('outline'))}", "Heading style"
        if len(text) <= 90:
            m = LEAD_RE.match(text)
            if m:
                return f"P|lead:{m.group(1).upper()}", "Numbered paragraph"
            if b.get("bold"):
                if b.get("caps"):
                    return "P|bold-caps", "Short bold capitals"
                return f"P|bold:{b.get('size') or 'normal'}", "Short bold paragraph"
        return None, None
    if b.get("rows") == 1 and b.get("cols") == 2:
        left, right, fill = b.get("left", ""), b.get("right", ""), b.get("fill") or "none"
        left1 = left.strip()
        if SECTION_RE.match(left1) and not MARKER_RE.match(left1):
            return f"T|section:{SECTION_RE.match(left1).group(1).upper()}|{fill}", "Band: word + number"
        if WORD_RE.match(left1) and ID_RE.match(right.strip()):
            return "T|action", "Band: action word + ID"
        if MARKER_RE.match(left1):
            return f"T|marker|{fill}", "Band: short marker"
        if any(c.islower() for c in left1) or re.search(r"[\\/>|$_.=]", left1):
            return f"T|command|{fill}", "Band: command"
        if left1 and left1.upper() == left1:
            return f"T|phrase|{fill}", "Band: capital phrase"
        return f"T|band|{fill}", "Band: other"
    return None, None


def find_patterns(blocks):
    """Group heading-like blocks into patterns: [{sig, kind, count, examples, first, last, fill, words}]."""
    pats = {}
    for b in blocks:
        sig, kind = signature(b)
        b["sig"] = sig
        if not sig:
            continue
        p = pats.setdefault(sig, {"sig": sig, "kind": kind, "count": 0, "examples": [], "first": b["i"],
                                  "last": b["i"], "fill": b.get("fill"), "words": {}})
        p["count"] += 1
        p["last"] = b["i"]
        if b["kind"] == "tbl":
            label = f"{b.get('left', '')} | {short(b.get('right', ''), 60)}"
            if sig == "T|action":
                w = b["left"].strip()
                p["words"].setdefault(w, {"count": 0, "fill": b.get("fill")})
                p["words"][w]["count"] += 1
        else:
            label = short(b["text"], 70)
        if len(p["examples"]) < 3:
            p["examples"].append(label)
    out = sorted(pats.values(), key=lambda p: p["first"])
    # Short bold paragraphs that appear only once or twice are just emphasis.
    return [p for p in out if not (p["sig"].startswith("P|bold") and p["count"] < 3)]


def suggest(patterns):
    """A proposed mapping {sig: level} (0 = content) and the level to tick."""
    by = {p["sig"]: p for p in patterns}
    styles = sorted([p for p in patterns if p["sig"].startswith("P|style:")],
                    key=lambda p: (_heading_no(p["sig"]), p["first"]))
    order = []
    if styles:
        top = styles[0]
        order.append([top["sig"]])
        for p in styles[1:]:
            # A lower heading style that only appears before the first top heading is front matter.
            if p["last"] < top["first"]:
                continue
            order.append([p["sig"]])
            break
    leads = [p for p in patterns if p["sig"].startswith("P|lead:")]
    if not styles and leads:
        order.append([max(leads, key=lambda p: p["count"])["sig"]])
    sections = [p for p in patterns if p["sig"].startswith("T|section:")]
    if sections:
        order.append([max(sections, key=lambda p: p["count"])["sig"]])
    if "T|action" in by:
        order.append(["T|action"])
        markers = [p for p in patterns if p["sig"].startswith("T|marker|")]
        if markers:
            order.append([p["sig"] for p in markers])
    mapping = {p["sig"]: 0 for p in patterns}
    for level, sigs in enumerate(order[:4], start=1):
        for s in sigs:
            mapping[s] = level
    levels = sorted({v for v in mapping.values() if v})
    checkable = mapping.get("T|action") or (levels[2] if len(levels) >= 3 else (levels[-1] if levels else 1))
    return mapping, checkable


def _heading_no(sig):
    m = re.search(r"(\d+)", sig)
    return int(m.group(1)) if m else (0 if "title" in sig.lower() else 9)


def match_score(patterns, template_mapping):
    """How well a saved template fits this runbook, 0-100: the share of heading-like blocks whose
    signature the template knows, counting only signatures the template maps to a level."""
    total = sum(p["count"] for p in patterns)
    if not total:
        return 0
    known = sum(p["count"] for p in patterns if p["sig"] in template_mapping)
    heading_sigs = {s for s, lvl in template_mapping.items() if lvl}
    present = {p["sig"] for p in patterns}
    missing_headings = len(heading_sigs - present)
    score = 100 * known / total - 10 * missing_headings
    return max(0, min(100, round(score)))
