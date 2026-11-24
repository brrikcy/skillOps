"""Session plans: a batch's trainers lay out what they will teach on a date, picking runbook parts in any order.

One plan per batch per date, shared by the batch's trainers (they co-teach). Entries are runbook pages
(a section or an action) from any of the batch's projects, or a short line of the trainer's own
("Break", "Quiz on joins"). The supervisor can read plans but not change them.

Data:  CORE  PLAN#<bid>  <yyyy-mm-dd>   {entries: [...], note, rev, by, by_name, at}
"""
import re

from botocore.exceptions import ClientError
from boto3.dynamodb.conditions import Key

from .core import store, util
from .core.util import Conflict, UserError

MAX_ENTRIES = 80
DATE_RE = re.compile(r"^20\d\d-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")


def check_date(day):
    day = (day or "")[:10]
    if not DATE_RE.match(day):
        raise UserError("Please choose a date.")
    return day


def get(bid, day):
    item = store.get("CORE", f"PLAN#{bid}", day, consistent=True) or {}
    return {"date": day, "entries": item.get("entries") or [], "note": item.get("note", ""),
            "rev": int(item.get("rev") or 0), "by_name": item.get("by_name", ""), "at": item.get("at", "")}


def list_dates(bid, limit=60):
    """[{date, count}] newest first."""
    r = store.t("CORE").query(KeyConditionExpression=Key("pk").eq(f"PLAN#{bid}"), ScanIndexForward=False,
                              Limit=limit, ProjectionExpression="sk, entries")
    return [{"date": i["sk"], "count": len(i.get("entries") or [])} for i in r.get("Items", []) if i.get("entries")]


def _pubs(bid):
    """A resolver: pid -> (project, version id, published tree) or None, for the runbooks this batch uses."""
    from .core import org
    from .runbook import progress, service
    b = org.get_batch(bid)
    cache = {}

    def get(pid):
        if pid not in cache:
            try:
                proj = org.get_project(b["prog_id"], pid)
                vid = progress.batch_version(b, proj, pin=False)
                cache[pid] = (proj, vid, service.published(vid)) if vid else None
            except Exception:  # noqa: BLE001  (an unknown project is simply left out)
                cache[pid] = None
        return cache[pid]
    return get


def clean(bid, raw):
    """Check every entry against the batch's runbooks; keep only what is real. Titles come from the runbook.

    Returns (entries, dropped): dropped = {"missing": parts no longer in the runbook, "overlap": parts left out
    because their whole section is in the plan}."""
    from .runbook import service
    if not isinstance(raw, list):
        raise UserError("The plan could not be read.")
    if len(raw) > MAX_ENTRIES:
        raise UserError(f"A plan can hold up to {MAX_ENTRIES} entries.")
    get = _pubs(bid)
    out, seen = [], set()
    dropped = {"missing": 0, "overlap": 0}
    for e in raw:
        if not isinstance(e, dict):
            continue
        if e.get("kind") == "text":
            text = util.clean_text(e.get("text"), "the line", 200, required=False)
            if text:
                out.append({"kind": "text", "text": text})
            continue
        pid, nid = str(e.get("proj", "")), str(e.get("node", ""))
        got = get(pid)
        n = got[2]["index"].get(nid) if got else None
        if not n or not n.get("page"):
            dropped["missing"] += 1
            continue
        if (pid, nid) in seen:                                     # each part once in a plan
            continue
        seen.add((pid, nid))
        proj, vid, pub = got
        out.append({"kind": "item", "proj": pid, "node": nid, "title": n["title"], "label": n.get("label") or "",
                    "fill": n.get("fill") or "", "checkable": bool(n.get("checkable")),
                    "path": service.path_label(pub, n), "project_name": proj["name"], "_parent": n.get("parent") or ""})
    # a part whose whole section is in the plan is already covered: keep the section only
    kept = []
    for e in out:
        if e.get("kind") == "item" and e["_parent"] and (e["proj"], e["_parent"]) in seen:
            dropped["overlap"] += 1
            continue
        kept.append(e)
    for e in kept:
        e.pop("_parent", None)
    return kept, dropped


def _clean_entries(bid, raw):
    return clean(bid, raw)[0]


def annotate(bid, entries):
    """The stored entries, each part marked missing=True when the batch's runbook no longer has it."""
    get = _pubs(bid)
    out = []
    for e in entries or []:
        if e.get("kind") == "text":
            out.append(e)
            continue
        got = get(str(e.get("proj", "")))
        n = got[2]["index"].get(str(e.get("node", ""))) if got else None
        out.append(dict(e, missing=not (n and n.get("page"))))
    return out


def save(bid, day, entries, note, rev, staff):
    """Replace the plan for that date. rev is the one the page was showing (0 = new)."""
    day = check_date(day)
    entries, dropped = clean(bid, entries)
    note = util.clean_text(note, "the note", 500, required=False)
    item = {"pk": f"PLAN#{bid}", "sk": day, "entries": entries, "note": note, "rev": int(rev) + 1,
            "by": staff.email, "by_name": staff.name, "at": util.now_iso()}
    try:
        if int(rev):
            store.t("CORE").put_item(Item=item, ConditionExpression="#r = :r",
                                     ExpressionAttributeNames={"#r": "rev"}, ExpressionAttributeValues={":r": int(rev)})
        else:
            store.t("CORE").put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as e:
        if store.is_conditional_fail(e):
            cur = get(bid, day)
            raise Conflict(f"{cur['by_name'] or 'Another trainer'} changed the plan for this date a moment ago.",
                           "Reload the page to see their plan, then make your change again.")
        raise
    return {"rev": item["rev"], "entries": entries, "note": note, "dropped": dropped}


def change(bid, day, form, staff):
    """The no-JavaScript path: one small change from a form button."""
    cur = get(bid, check_date(day))
    if int(form.get("rev") or 0) != cur["rev"]:
        raise Conflict("The plan for this date changed a moment ago.", "Reload the page and make your change again.")
    entries = list(cur["entries"])
    action = form.get("action")
    if action == "add":
        entries.append({"kind": "item", "proj": form.get("proj", ""), "node": form.get("node", "")})
    elif action == "text":
        entries.append({"kind": "text", "text": form.get("text", "")})
    elif action in ("remove", "up", "down"):
        try:
            k = int(form.get("index"))
            entries[k]
        except (TypeError, ValueError, IndexError):
            raise UserError("That entry is not in the plan.")
        if action == "remove":
            entries.pop(k)
        else:
            to = k - 1 if action == "up" else k + 1
            if 0 <= to < len(entries):
                entries[k], entries[to] = entries[to], entries[k]
    elif action == "note":
        return save(bid, day, entries, form.get("note", ""), cur["rev"], staff)
    else:
        raise UserError("Unknown change.")
    return save(bid, day, entries, cur["note"], cur["rev"], staff)


def planned_on(bid, day, proj):
    """{node_id: position (1-based)} of one project's entries in a date's plan (for the class view)."""
    out = {}
    for k, e in enumerate(get(bid, day)["entries"], start=1):
        if e.get("kind") == "item" and e.get("proj") == proj:
            out.setdefault(e["node"], k)
    return out


def to_pdf(bid, day, batch_name):
    """(file name, bytes) of a date's saved plan as a PDF: what the trainer will teach, in the planned order.

    Where the runbook version has its own PDF attached (and the part is found in it), the part is cut straight out of
    that PDF, so it keeps the runbook's look. Any part that is not (no PDF yet, or not found in it) is laid out from the
    runbook's pages instead, so a plan is never missing anything."""
    from concurrent.futures import ThreadPoolExecutor
    from datetime import date

    from .core import pdf, pdfhtml
    from .runbook import pdfslice, progress, service
    plan = get(bid, day)
    entries = annotate(bid, plan["entries"])
    get_pub = _pubs(bid)
    d0 = date.fromisoformat(day)
    pretty = d0.strftime("%A, ") + f"{d0.day} " + d0.strftime("%B %Y")
    footer = f"{batch_name}  |  Session plan for {pretty}"
    grey = (0.4, 0.43, 0.48)

    rows, sources = [], {}
    for e in entries:
        if e.get("kind") == "text":
            rows.append({"text": e["text"]})
            continue
        got = get_pub(str(e.get("proj", "")))
        node = got[2]["index"].get(str(e.get("node", ""))) if got else None
        if not node or not node.get("page"):
            rows.append({"gone": e.get("title") or "A part of the runbook"})
            continue
        proj, vid, pub = got
        if vid not in sources:
            sources[vid] = service.runbook_pdf(vid)
        src = sources[vid]
        row = {"node": node, "proj": proj, "vid": vid, "pub": pub, "sec": not node.get("checkable"),
               "acts": progress.section_items(pub, node), "pieces": None}
        if src:
            row["pieces"] = pdfslice.regions(pub, src[1], node["id"])
        rows.append(row)

    # pages of the runbook that have to be laid out (no PDF, or the part was not found in it)
    wanted = []
    for r in rows:
        if "node" in r and not r["pieces"]:
            if r["sec"]:
                wanted.append((r["vid"], r["node"]["id"]))
            wanted += [(r["vid"], a["id"]) for a in r["acts"]]
    wanted = list(dict.fromkeys(wanted))
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = dict(zip(wanted, pool.map(lambda w: service.item_html(w[0], w[1]), wanted)))

    n_acts = sum(len(r["acts"]) for r in rows if "acts" in r)
    saved = ""
    if plan.get("at"):
        d = util.parse_iso(plan["at"])
        if d:
            saved = "Saved " + d.astimezone(util.IST).strftime("%d %b %Y, %I:%M %p").lstrip("0")
            if plan.get("by_name"):
                saved += f" by {plan['by_name']}"
    from_pdf = any(r.get("pieces") for r in rows)

    def new_doc():
        return pdf.Document(f"Session plan - {batch_name} - {day}", footer=footer, numbered=not from_pdf)

    def title_block(doc):
        doc.text("Session plan", size=20, bold=True)
        doc.text(f"{batch_name}  |  {pretty}", size=11, color=grey)
        doc.text(f"{len(rows)} {'entry' if len(rows) == 1 else 'entries'}  |  {n_acts} {'action' if n_acts == 1 else 'actions'}"
                 + (f"  |  {saved}" if saved else ""), size=9, color=grey)
        if plan.get("note"):
            doc.space(8)
            doc.text("Note", size=9, bold=True, color=grey)
            doc.text(plan["note"], size=10)

    def head(doc, label, fill, title, size=11.5):
        """A heading line: colour square, type (SHOW, SAY...), title. Kept with the lines that follow."""
        doc.need(size * 1.4 + 40)
        top = doc.y - 2
        x0 = 0
        if fill:
            doc.square(0, top - size * 0.92, 7, fill=pdf._rgb(fill))
            x0 = 12
        if label:
            doc.text_at(label.upper(), x0, top - size * 0.88, size=7.5, bold=True, color=grey)
            x0 += pdf.width(label.upper(), 7.5, True) + 6
        doc.y = top + 2
        doc.text(title, size=size, bold=True, x=x0)

    def laid_out(doc, k, r):
        """One entry laid out from the runbook's pages."""
        node, proj, vid, pub = r["node"], r["proj"], r["vid"], r["pub"]
        doc.text(f"{k}.  {proj['name']}  |  {service.path_label(pub, node)}", size=8.5, color=grey, keep=60)
        if r["sec"]:
            head(doc, "", "", node["title"], size=13)
            pdfhtml.draw(doc, bodies.get((vid, node["id"]), ""))
            for a in r["acts"]:
                doc.space(7)
                head(doc, a.get("label") or "", a.get("fill") or "", a["title"], size=11)
                pdfhtml.draw(doc, bodies.get((vid, a["id"]), ""))
        else:
            head(doc, node.get("label") or "", node.get("fill") or "", node["title"], size=13)
            pdfhtml.draw(doc, bodies.get((vid, node["id"]), ""))

    def plain(doc, k, r):
        if "text" in r:
            doc.text(f"{k}.  {r['text']}", size=12, bold=True)
        elif "gone" in r:
            doc.text(f"{k}.  {r['gone']}  (no longer in the runbook)", size=11, color=grey)
        else:
            laid_out(doc, k, r)

    slug = re.sub(r"[^a-z0-9]+", "-", batch_name.lower()).strip("-") or "batch"
    filename = f"session-plan-{slug}-{day}.pdf"

    if not from_pdf:                                                  # the whole plan from the runbook's pages
        doc = new_doc()
        title_block(doc)
        doc.rule(gap=9)
        if not rows:
            doc.text("Nothing is planned for this date yet.", size=11, color=grey)
        for k, r in enumerate(rows, start=1):
            if k > 1:
                doc.rule(gap=8)
            plain(doc, k, r)
        return filename, doc.render()

    # a cover that lists the session, then the parts: cut from the PDF, or laid out where the PDF has none of it
    cover = new_doc()
    title_block(cover)
    cover.rule(gap=9)
    cover.text("In this session", size=11, bold=True)
    for k, r in enumerate(rows, start=1):
        if "text" in r:
            cover.text(f"{k}.  {r['text']}", size=10, bold=True, x=6)
        elif "gone" in r:
            cover.text(f"{k}.  {r['gone']}  (no longer in the runbook)", size=10, color=grey, x=6)
        else:
            cover.text(f"{k}.  {r['node']['title']}", size=10, bold=True, x=6)
            cover.text(f"{r['proj']['name']}  |  {service.path_label(r['pub'], r['node'])}", size=8.5, color=grey, x=20)
    parts, run, rest = [{"kind": "pdf", "bytes": cover.render()}], None, None

    def close_rest():
        nonlocal rest
        if rest is not None:
            parts.append({"kind": "pdf", "bytes": rest.render()})
            rest = None

    for k, r in enumerate(rows, start=1):
        if "node" not in r:
            continue                                                  # your own lines and gone parts: on the cover only
        if r["pieces"]:
            close_rest()
            src = sources[r["vid"]]
            if not (run and run["vid"] == r["vid"]):
                run = {"kind": "slices", "vid": r["vid"], "src": src[0], "mapping": src[1], "groups": []}
                parts.append(run)
            run["groups"].append(r["pieces"])
        else:
            run = None
            if rest is None:
                rest = new_doc()
            else:
                rest.rule(gap=8)
            laid_out(rest, k, r)
    close_rest()
    return filename, pdfslice.assemble(parts, footer=footer)
