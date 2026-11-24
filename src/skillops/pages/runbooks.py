"""Runbook pages: the supervisor's library (upload, map, preview, publish, versions) and the
navigator trainers use with their batches (one item at a time, ticks, notes, search, present mode)."""
import re
import secrets

from ..core import config, idem, org, perms, store, util
from ..core.http import Response, json_response, q
from ..core.util import NotFound, UserError
from ..runbook import progress, service, tree
from ..web import route

RB = "(?P<rid>r-[a-z0-9]{4,12})"
VER = "(?P<vid>v-[a-z0-9]{4,12})"
NODE = "(?P<nid>n[0-9]{1,6})"
BATCH = "(?P<bid>b-[a-z0-9]{4,12})"
PROJ = "(?P<jid>j-[a-z0-9]{4,12})"
STATUS_TEXT = {"uploaded": "Waiting to be read", "reading": "Reading the document", "mapping": "Ready to map",
               "publishing": "Publishing", "published": "Published", "failed": "Could not be read"}


def _version_of(rid, vid):
    v = service.get_version(vid)
    if v["rid"] != rid:
        raise NotFound("This version does not belong to this runbook.")
    return v


def _projects_using(rid):
    """The course projects that use this runbook: [{"course", "project"}]."""
    return [{"course": c, "project": j} for c, js in org.all_projects() for j in js if j.get("runbook_id") == rid]


def _programme_uses(rid):
    """Where this runbook is taught: [{"programme", "project"}] for every programme with such a project."""
    out = []
    for p in org.list_programmes():
        for j in org.list_projects(p["id"]):
            if j.get("runbook_id") == rid:
                out.append({"programme": p, "project": j})
    return out


# ================================================================== library

@route("GET", "/admin/runbooks", access="super")
def library(ctx):
    items = service.list_runbooks()
    for r in items:
        vs = service.list_versions(r["id"])
        r["versions"] = vs
        r["used_by"] = _projects_using(r["id"])
        r["latest_v"] = next((v for v in vs if v["id"] == r.get("latest")), None)
        r["newest"] = vs[0] if vs else None
    return ctx.render("rb_library.html", title="Runbooks", items=items, templates=service.list_templates())


@route("POST", "/admin/runbooks", access="super", name="create runbook")
def create(ctx):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")
    result = idem.run_once(f.get("idem"), ctx.staff.email, lambda: {
        "redirect": f"/admin/runbooks/{service.create_runbook(f.get('name'))}",
        "flash": "Runbook added to the library. Now upload its Word file."})
    return ctx.redirect(result["redirect"], flash=result.get("flash"))


def _upload_form(ctx, rid):
    """Where the browser sends the file: straight to S3 in AWS (no size limit from Lambda), or to us locally."""
    if config.local_mode():
        return {"action": f"/admin/runbooks/{rid}/upload", "fields": {}, "direct": False}
    token = secrets.token_urlsafe(12)
    key = f"uploads/incoming/{token}.docx"
    post = store.s3().generate_presigned_post(
        Bucket=config.runbook_bucket(), Key=key, ExpiresIn=3600,
        Fields={"success_action_redirect": f"{ctx.req.base_url()}/admin/runbooks/{rid}/received",
                "x-amz-meta-filename": "runbook.docx"},
        Conditions=[["content-length-range", 1, 40 * 1024 * 1024],
                    {"success_action_redirect": f"{ctx.req.base_url()}/admin/runbooks/{rid}/received"},
                    ["starts-with", "$x-amz-meta-filename", ""]])
    return {"action": post["url"], "fields": post["fields"], "direct": True}


def _pdf_upload_form(ctx, rid, vid):
    """The same idea for a version's PDF: straight to S3 in AWS, or to us locally."""
    if config.local_mode():
        return {"action": f"/admin/runbooks/{rid}/v/{vid}/pdf", "fields": {}, "direct": False}
    token = secrets.token_urlsafe(12)
    back = f"{ctx.req.base_url()}/admin/runbooks/{rid}/v/{vid}/pdf-received"
    post = store.s3().generate_presigned_post(
        Bucket=config.runbook_bucket(), Key=f"uploads/incoming/{token}.pdf", ExpiresIn=3600,
        Fields={"success_action_redirect": back, "x-amz-meta-filename": "runbook.pdf"},
        Conditions=[["content-length-range", 1, service.PDF_MAX], {"success_action_redirect": back},
                    ["starts-with", "$x-amz-meta-filename", ""]])
    return {"action": post["url"], "fields": post["fields"], "direct": True}


@route("GET", f"/admin/runbooks/{RB}", access="super")
def runbook_page(ctx, rid):
    rb = service.get_runbook(rid)
    versions = service.list_versions(rid)
    pubs = [v for v in versions if v["status"] == "published"]
    for v in versions:
        v["status_text"] = STATUS_TEXT.get(v["status"], v["status"])
        v["unmatched"] = None
        if v["status"] == "published":
            older = [p for p in pubs if p["number"] < v["number"]]
            if older:
                try:
                    _, unmatched = service.match_items(service.published(older[0]["id"]), service.published(v["id"]))
                    v["unmatched"] = unmatched
                    v["compared_with"] = older[0]["number"]
                except UserError:
                    pass
    batches = []
    for u in _programme_uses(rid):
        for b in org.list_batches(u["programme"]["id"]):
            pinned = (b.get("versions") or {}).get(u["project"]["id"])
            batches.append({"batch": b, "programme": u["programme"], "project": u["project"],
                            "version": next((v for v in versions if v["id"] == pinned), None)})
    busy = any(v["status"] in ("uploaded", "reading", "publishing") for v in versions)
    for v in versions:
        v["blockers"] = service.version_blockers(v["id"])
        v["pdf_upload"] = _pdf_upload_form(ctx, rid, v["id"]) if v["status"] in ("mapping", "published") else None
    return ctx.render("rb_runbook.html", title=rb["name"], rb=rb, versions=versions, batches=batches,
                      upload=_upload_form(ctx, rid), used_by=_projects_using(rid), busy=busy, pubs=pubs,
                      rb_blockers=service.runbook_blockers(rid))


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/delete", access="super", name="delete runbook version")
def version_delete(ctx, rid, vid):
    v = _version_of(rid, vid)
    ctx.audit_target = f"{service.get_runbook(rid)['name']} v{v['number']}"
    service.delete_version(vid)
    return ctx.redirect(f"/admin/runbooks/{rid}", flash=f"Version {v['number']} ({v['filename']}) is deleted.")


@route("POST", f"/admin/runbooks/{RB}/delete", access="super", name="delete runbook")
def runbook_delete(ctx, rid):
    rb = service.get_runbook(rid)
    ctx.audit_target = rb["name"]
    if (ctx.form().get("confirm_name") or "").strip().lower() != rb["name"].strip().lower():
        raise UserError("Type the runbook's name to confirm.", f'Type "{rb["name"]}" in the box, then press Delete.')
    service.delete_runbook(rid)
    return ctx.redirect("/admin/runbooks", flash=f'"{rb["name"]}" and its uploaded versions are deleted.')


@route("POST", f"/admin/runbooks/{RB}/upload", access="super", name="upload runbook version")
def upload(ctx, rid):
    ctx.audit_target = rid
    f = ctx.req.files().get("file")
    if not f:
        raise UserError("Please choose the Word (.docx) file.")
    vid = service.add_version(rid, f["filename"], f["data"], ctx.staff.email)
    ctx.audit_detail = vid
    return ctx.redirect(f"/admin/runbooks/{rid}",
                        flash="Uploaded. The document is being read now; refresh in a few seconds.")


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/pdf", access="super", name="attach runbook pdf")
def pdf_attach(ctx, rid, vid):
    v = _version_of(rid, vid)
    ctx.audit_target = f"{service.get_runbook(rid)['name']} v{v['number']}"
    f = ctx.req.files().get("file")
    if not f:
        raise UserError("Please choose the runbook's PDF file.")
    pages = service.attach_pdf(vid, f["filename"], f["data"], ctx.staff.email)
    ctx.audit_detail = f"{pages} pages"
    return ctx.redirect(f"/admin/runbooks/{rid}", flash="PDF attached. Session PDFs will be cut from it.")


@route("GET", f"/admin/runbooks/{RB}/v/{VER}/pdf-received", access="super", name="attach runbook pdf (S3)")
def pdf_received(ctx, rid, vid):
    _version_of(rid, vid)
    key = ctx.req.query.get("key", "")
    if not re.match(r"^uploads/incoming/[A-Za-z0-9_\-]{8,40}\.pdf$", key):
        raise UserError("The upload did not finish.", "Upload the file again.")
    obj = store.s3().get_object(Bucket=config.runbook_bucket(), Key=key)
    pages = service.attach_pdf(vid, (obj.get("Metadata") or {}).get("filename") or "runbook.pdf", obj["Body"].read(), ctx.staff.email)
    from ..core import audit
    audit.write(ctx.staff.email, ctx.staff.role, "attach runbook pdf", target=rid, detail=f"{vid}: {pages} pages",
                request_id=ctx.req.request_id)
    return ctx.redirect(f"/admin/runbooks/{rid}", flash="PDF attached. Session PDFs will be cut from it.")


@route("GET", f"/admin/runbooks/{RB}/received", access="super", name="upload runbook version (S3)")
def received(ctx, rid):
    key = ctx.req.query.get("key", "")
    if not re.match(r"^uploads/incoming/[A-Za-z0-9_\-]{8,40}\.docx$", key):
        raise UserError("The upload did not finish.", "Upload the file again.")
    obj = store.s3().get_object(Bucket=config.runbook_bucket(), Key=key)
    data = obj["Body"].read()
    filename = (obj.get("Metadata") or {}).get("filename") or "runbook.docx"
    vid = service.add_version_once(rid, key, filename[:200], data, ctx.staff.email)
    from ..core import audit
    audit.write(ctx.staff.email, ctx.staff.role, "upload runbook version", target=rid, detail=vid,
                request_id=ctx.req.request_id)
    return ctx.redirect(f"/admin/runbooks/{rid}", flash="Uploaded. The document is being read now; refresh in a few seconds.")


# ================================================================== mapping

@route("GET", f"/admin/runbooks/{RB}/v/{VER}/map", access="super")
def map_page(ctx, rid, vid):
    rb = service.get_runbook(rid)
    v = _version_of(rid, vid)
    if v["status"] in ("uploaded", "reading"):
        return ctx.redirect(f"/admin/runbooks/{rid}", flash="This version is still being read. Refresh in a few seconds.",
                            kind="warn")
    for p in v["patterns"]:
        p["level"] = int(v["mapping"].get(p["sig"], 0))
        p["words_list"] = sorted(p.get("words", {}).items(), key=lambda kv: -kv[1]["count"])
        p["plain"] = plain_pattern(p)
    # headings first (in level order, then document order), then the patterns read as text
    heads = sorted((p for p in v["patterns"] if p["level"]), key=lambda p: (p["level"], p.get("first", 0)))
    texts = sorted((p for p in v["patterns"] if not p["level"]), key=lambda p: -p["count"])
    templates = service.template_scores(v["patterns"])
    applied = next((t for t in templates if t["id"] == v.get("template_id")), None)
    older = [x for x in service.list_versions(rid) if x["number"] < v["number"] and x.get("mapping")]
    return ctx.render("rb_map.html", title=f"Confirm the structure · {rb['name']}", rb=rb, v=v, heads=heads,
                      texts=texts, templates=templates, applied=applied,
                      prev_number=older[0]["number"] if older else None,
                      template_name=applied["name"] if applied else rb["name"],
                      names=v.get("level_names") or tree.LEVEL_NAMES)


PLAIN = {
    "T|section": "Coloured band: a word and a number (like PART 1)",
    "T|action": "Coloured band: an action word and an ID (like DO 2.4)",
    "T|marker": "Small coloured marker: a number, letter or OK / FIX",
    "T|command": "Dark strip with a command",
    "T|phrase": "Coloured chip with capital words",
    "T|band": "Other one-row coloured table",
    "P|lead": "Paragraph that starts with a number",
    "P|bold-caps": "Short line in bold capitals",
    "P|bold": "Short bold line",
}


def plain_pattern(p):
    """How a pattern looks, in words a supervisor recognises from Word."""
    sig = p["sig"]
    if sig.startswith("P|style:"):
        return f'Word heading style "{sig[8:].title()}"'
    for k, text in PLAIN.items():
        if sig.startswith(k):
            return text
    return p.get("kind") or "Pattern"


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/map/accept", access="super", name="accept runbook structure")
def map_accept(ctx, rid, vid):
    """The one-click path: keep the structure as found, remember it as a template, go to the tree."""
    rb = service.get_runbook(rid)
    v = _version_of(rid, vid)
    ctx.audit_target = vid
    applied = next((t for t in service.list_templates() if t["id"] == v.get("template_id")), None)
    service.save_mapping(vid, v["mapping"], v["checkable"], v.get("level_names") or tree.LEVEL_NAMES,
                         applied["name"] if applied else rb["name"])
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/preview",
                        flash="Structure kept and remembered for the next upload. Check the tree, then publish.")


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/map", access="super", name="map runbook structure")
def map_save(ctx, rid, vid):
    _version_of(rid, vid)
    f = ctx.form()
    ctx.audit_target = vid
    levels = {k[4:]: v for k, v in f.items() if k.startswith("lvl:")}
    names = [f.get(f"name{i}", "") for i in range(1, 5)]
    service.save_mapping(vid, levels, f.get("checkable"), names,
                         f.get("template_name", "").strip() if f.get("save_template") else "")
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/preview", flash="Structure saved. Check the tree, then publish.")


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/template", access="super", name="apply mapping template")
def map_template(ctx, rid, vid):
    _version_of(rid, vid)
    tid = ctx.form().get("template_id", "")
    ctx.audit_target = f"{vid} {tid}"
    service.apply_template(vid, tid)
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/map", flash="Template applied. Check it and press Save.")


# ================================================================== preview, edits, publish

@route("GET", f"/admin/runbooks/{RB}/v/{VER}/preview", access="super")
def preview_page(ctx, rid, vid):
    rb = service.get_runbook(rid)
    v, blocks, nodes = service.preview(vid)
    _version_of(rid, vid)
    roots, kids = [], {}
    for n in nodes:
        if n["parent"]:
            kids.setdefault(n["parent"], []).append(n)
        else:
            roots.append(n)
    sel = next((n for n in nodes if n["id"] == ctx.req.query.get("node")), None)
    sel_blocks = []
    if sel:
        lo, hi = max(0, sel["start"] - 2), min(len(blocks), sel["own_end"] + 1)
        sel_blocks = [dict(b, is_start=(b["i"] == sel["start"])) for b in blocks[lo:hi]][:12]
    counts = tree.counts(nodes)
    return ctx.render("rb_preview.html", title=f"Preview · {rb['name']}", rb=rb, v=v, roots=roots, kids=kids,
                      counts=counts, names=v.get("level_names") or tree.LEVEL_NAMES, sel=sel, sel_blocks=sel_blocks)


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/edit", access="super", name="edit runbook tree")
def preview_edit(ctx, rid, vid):
    _version_of(rid, vid)
    f = ctx.form()
    ctx.audit_target = f"{vid} {f.get('node')} {f.get('action')}"
    service.edit_tree(vid, f.get("action"), f.get("node"), f.get("value", ""))
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/preview?node={q(f.get('node', ''))}", flash="Changed.")


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/reset-edits", access="super", name="undo runbook tree edits")
def preview_reset(ctx, rid, vid):
    _version_of(rid, vid)
    service.set_version(vid, {"overrides": {}})
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/preview", flash="All renames, merges and moves are undone.")


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/publish", access="super", name="publish runbook version")
def publish(ctx, rid, vid):
    _version_of(rid, vid)
    ctx.audit_target = vid
    idem.run_once(ctx.form().get("idem"), ctx.staff.email, lambda: {"job": service.publish(vid, ctx.staff.email)})
    return ctx.redirect(f"/admin/runbooks/{rid}", flash="Publishing has started. Refresh in a few seconds.")


@route("GET", f"/admin/runbooks/{RB}/v/{VER}/status.json", access="super", audit_it=False)
def version_status(ctx, rid, vid):
    v = _version_of(rid, vid)
    return json_response({"status": v["status"], "message": v.get("message", "")})


# ================================================================== projects and batches

@route("POST", f"/admin/projects/{PROJ}/runbook", access="super", name="choose project runbook")
def choose_runbook(ctx, jid):
    rid = ctx.form().get("runbook_id", "")
    ctx.audit_target = f"{jid} {rid}"
    j = org.project_item(jid)
    if rid:
        rb = service.get_runbook(rid)
        if not rb.get("latest"):
            raise UserError("This runbook has no published version yet.", "Publish it first.")
    org.set_project_runbook(jid, rid)
    return ctx.redirect(f"/admin/courses/{j['course_id']}", flash="Runbook chosen for this project.")


@route("POST", f"/admin/batches/{BATCH}/projects/{PROJ}/version", access="super", name="move batch to runbook version")
def move_version(ctx, bid, jid):
    b = org.get_batch(bid)
    proj = org.get_project(b["prog_id"], jid)
    vid = ctx.form().get("version_id", "")
    ctx.audit_batch, ctx.audit_target = bid, f"{jid} {vid}"
    v = service.get_version(vid)
    if v["rid"] != proj.get("runbook_id") or v["status"] != "published":
        raise UserError("Choose a published version of this project's runbook.")
    result = progress.move_batch(b, proj, vid, ctx.staff)
    msg = f"Moved to version {v['number']}. {result['copied']} tick(s) carried across."
    if result["unmatched"]:
        msg += f" {len(result['unmatched'])} tick(s) could not be matched and stay on the old version."
    return ctx.redirect(f"/admin/batches/{bid}", flash=msg, kind="warn" if result["unmatched"] else "ok")


# ================================================================== the navigator

def _nav_context(ctx, bid, jid):
    batch = perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    proj = org.get_project(batch["prog_id"], jid)
    ctx.set_batch_page(bc, proj)
    vid = progress.batch_version(batch, proj)
    if not vid:
        return bc, proj, None
    return bc, proj, vid


def _tree_view(pub, summ, current_id):
    """Nested tree for the side panel: [(node, children[(node, leaves[])])]."""
    idx = pub["index"]
    chain = set()
    node = idx.get(current_id)
    while node:
        chain.add(node["id"])
        node = idx.get(node.get("parent"))
    return {"roots": [n for n in pub["nodes"] if n.get("page") and not n.get("parent")], "open": chain}


# ---- the runbook as its original PDF, read top to bottom in a pane, with the sections one click away

def _pdf_response(vid, name, download=False):
    """The version's PDF. Locally: the bytes. In AWS: a short-lived link to the file in S3 (it is too big for Lambda)."""
    disp = f'{"attachment" if download else "inline"}; filename="{name}"'
    if config.local_mode():
        data = store.s3().get_object(Bucket=config.runbook_bucket(), Key=service._pdf_key(vid))["Body"].read()
        return Response(200, data, {"content-type": "application/pdf", "content-disposition": disp,
                                    "cache-control": "private, max-age=300", "x-content-type-options": "nosniff"}, binary=True)
    url = store.s3().generate_presigned_url(
        "get_object", ExpiresIn=300,
        Params={"Bucket": config.runbook_bucket(), "Key": service._pdf_key(vid), "ResponseContentType": "application/pdf",
                "ResponseContentDisposition": disp,
                "ResponseCacheControl": "private, max-age=300"})
    # the pane re-opens the PDF at each section jump: let the browser keep the link (and the file) for a few minutes
    return Response(302, "", {"location": url, "cache-control": "private, max-age=240"})


def _file_name(title, extra=""):
    return (re.sub(r"[^A-Za-z0-9]+", "-", f"{title} {extra}").strip("-") or "runbook") + ".pdf"


def _viewer_nav(pub, summ, pmap):
    """What the viewer's sections list needs: the steps with their parts and actions, and each one's page in the PDF."""
    sections = progress.sections(pub)
    groups = []
    for s in sections:
        top = s
        while top.get("parent"):
            top = pub["index"][top["parent"]]
        if not groups or groups[-1]["top"]["id"] != top["id"]:
            groups.append({"top": top, "sections": []})
        groups[-1]["sections"].append(s)
    subs = {s["id"]: [{"id": n["id"], "title": n["title"], "label": n.get("label") or "", "fill": n.get("fill") or "",
                       "done": n["id"] in summ["done_ids"]} for n in progress.section_items(pub, s)] for s in sections}
    pg, pt = {}, {}
    for nid, (p, y) in (pmap.get("nodes") or {}).items():
        x0, y0, w, h = pmap["pages"][p]
        pg[nid] = p + 1
        pt[nid] = max(0, round(y0 + h - y))                # how far down the page the part starts
    return groups, subs, pg, pt, {s["id"]: k + 1 for k, s in enumerate(sections)}


def _viewer(ctx, *, pub, summ, pmap, title, heading, subtitle, back, pdf_url, at="", tick_base="", bc=None, proj=None,
            file_name="runbook.pdf"):
    groups, subs, pg, pt, secno = _viewer_nav(pub, summ, pmap)
    return ctx.render("rb_viewer.html", title=title, heading=heading, subtitle=subtitle, back=back, pdf_url=pdf_url,
                      groups=groups, subs=subs, pg=pg, pt=pt, secno=secno, summ=summ, start=pg.get(at) or 1, start_top=pt.get(at) or 0,
                      tick_base=tick_base,
                      can_tick=bool(tick_base) and not (ctx.staff and ctx.staff.is_super), bc=bc, proj=proj,
                      pages=len(pmap["pages"]), file_name=file_name,
                      secpath={s["id"]: service.path_label(pub, s) for g in groups for s in g["sections"]})


def viewer_for_batch(ctx, bid, jid, at=""):
    """The batch's runbook as its PDF (a Response), or None when this version has no PDF attached (the old pages then open)."""
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return None
    pmap = service.pdf_map(vid)
    if not pmap:
        return None
    summ = progress.summary(bid, vid)
    if not at and summ["next_up"]:
        at = summ["next_up"]["id"]
    return _viewer(ctx, pub=summ["pub"], summ=summ, pmap=pmap, title=f"{proj['name']} runbook",
                   heading=f"{proj['name']} runbook", subtitle=f"{bc['programme']['name']} · {bc['batch']['name']}",
                   back=f"/b/{bid}/today", pdf_url=f"/b/{bid}/p/{jid}/runbook.pdf", at=at, tick_base=f"/b/{bid}/p/{jid}",
                   bc=bc, proj=proj, file_name=_file_name(proj["name"], "runbook"))


@route("GET", f"/b/{BATCH}/p/{PROJ}/runbook.pdf", audit_it=False)
def batch_pdf(ctx, bid, jid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid or not service.pdf_map(vid):
        raise NotFound("This runbook has no PDF yet.", "Ask your supervisor to attach it.")
    return _pdf_response(vid, _file_name(proj["name"], "runbook"), ctx.req.query.get("download") == "1")


@route("GET", f"/b/{BATCH}/p/{PROJ}")
def continue_here(ctx, bid, jid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    shown = viewer_for_batch(ctx, bid, jid)
    if shown:
        return shown
    # "Continue" opens the class view where the class is (the item after the furthest tick).
    return ctx.redirect(f"/b/{bid}/p/{jid}/teach")


@route("GET", f"/b/{BATCH}/p/{PROJ}/i/{NODE}")
def item_page(ctx, bid, jid, nid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    shown = viewer_for_batch(ctx, bid, jid, at=nid)
    if shown:
        return shown
    summ = progress.summary(bid, vid)
    pub = summ["pub"]
    node = pub["index"].get(nid)
    if not node or not node.get("page"):
        raise NotFound("This item is not in the runbook version this batch uses.",
                       "Open the project again to continue where the batch stopped.")
    pages = [n for n in pub["nodes"] if n.get("page")]
    k = next(i for i, n in enumerate(pages) if n["id"] == nid)
    prev_n = pages[k - 1] if k > 0 else None
    next_n = pages[k + 1] if k + 1 < len(pages) else None
    notes = progress.notes(bid, vid)
    children = [pub["index"][c] for c in node.get("children", []) if c in pub["index"]]
    return ctx.render("rb_item.html", title=node["title"], bc=bc, proj=proj, pub=pub, summ=summ, node=node,
                      html_body=service.item_html(vid, nid), prev_n=prev_n, next_n=next_n,
                      tick=summ["ticks"].get(nid), note=notes.get(nid), children=children,
                      tree_view=_tree_view(pub, summ, nid), base=f"/b/{bid}/p/{jid}",
                      path=service.path_label(pub, node), present=ctx.req.query.get("present") == "1",
                      view_only=ctx.staff.is_super,
                      can_untick=bool(summ["ticks"].get(nid)) and summ["ticks"][nid]["by"] == ctx.staff.email)


def _tick_response(ctx, bid, jid, nid, state, t=None):
    if ctx.req.wants_json():
        return json_response({"ok": True, "state": state, "by": (t or {}).get("by_name", ""),
                              "at": util.show_time((t or {}).get("at", ""))})
    return ctx.redirect(f"/b/{bid}/p/{jid}/i/{nid}")


@route("POST", f"/b/{BATCH}/p/{PROJ}/i/{NODE}/tick", name="mark item complete")
def tick(ctx, bid, jid, nid):
    perms.require_trainer(ctx.staff)
    bc, proj, vid = _nav_context(ctx, bid, jid)
    ctx.audit_batch, ctx.audit_target = bid, f"{proj['name']} {nid}"
    if not vid:
        raise UserError("This project has no runbook yet.")
    t, new = progress.tick(bid, vid, nid, ctx.staff, jid)
    if not new:
        ctx.audit_detail = f"already marked by {t.get('by')}"
    return _tick_response(ctx, bid, jid, nid, "done", t)


@route("POST", f"/b/{BATCH}/p/{PROJ}/i/{NODE}/untick", name="un-mark item")
def untick(ctx, bid, jid, nid):
    perms.require_trainer(ctx.staff)
    bc, proj, vid = _nav_context(ctx, bid, jid)
    ctx.audit_batch, ctx.audit_target = bid, f"{proj['name']} {nid}"
    if not vid:
        raise UserError("This project has no runbook yet.")
    progress.untick(bid, vid, nid, ctx.staff, jid)
    return _tick_response(ctx, bid, jid, nid, "open")


@route("POST", f"/b/{BATCH}/p/{PROJ}/i/{NODE}/note", name="save item note")
def note(ctx, bid, jid, nid):
    perms.require_trainer(ctx.staff)
    bc, proj, vid = _nav_context(ctx, bid, jid)
    ctx.audit_batch, ctx.audit_target = bid, f"{proj['name']} {nid}"
    if not vid:
        raise UserError("This project has no runbook yet.")
    progress.save_note(bid, vid, nid, ctx.form().get("text", ""), ctx.staff)
    return ctx.redirect(f"/b/{bid}/p/{jid}/i/{nid}", flash="Note saved.")


@route("GET", f"/b/{BATCH}/p/{PROJ}/search")
def search(ctx, bid, jid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    words = (ctx.req.query.get("q") or "").strip()[:80]
    results = []
    if words:
        pub = service.published(vid)
        low = words.lower()
        for n in pub["nodes"]:
            if not n.get("page"):
                continue
            hay = (n["title"] + " " + n.get("text", ""))
            pos = hay.lower().find(low)
            if pos >= 0:
                lo = max(0, pos - 70)
                results.append({"node": n, "path": service.path_label(pub, n), "before": hay[lo:pos],
                                "hit": hay[pos:pos + len(words)], "after": hay[pos + len(words):pos + len(words) + 110]})
            if len(results) >= 60:
                break
    return ctx.render("rb_search.html", title=f"Search · {proj['name']}", bc=bc, proj=proj, words=words,
                      results=results, base=f"/b/{bid}/p/{jid}")


@route("GET", f"/b/{BATCH}/p/{PROJ}/intro")
def intro(ctx, bid, jid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    if service.pdf_map(vid):
        first = next((n["id"] for n in service.published(vid)["nodes"] if n.get("page")), "")
        shown = viewer_for_batch(ctx, bid, jid, at=first)          # from the very first page
        if shown:
            return shown
    summ = progress.summary(bid, vid)
    return ctx.render("rb_item.html", title="About this runbook", bc=bc, proj=proj, pub=summ["pub"], summ=summ,
                      node=None, html_body=service.item_html(vid, "preamble"), prev_n=None,
                      next_n=next((n for n in summ["pub"]["nodes"] if n.get("page")), None), tick=None, note=None,
                      children=[], tree_view=_tree_view(summ["pub"], summ, ""), base=f"/b/{bid}/p/{jid}",
                      path="", present=ctx.req.query.get("present") == "1", can_untick=False,
                      view_only=ctx.staff.is_super)


# ---- the supervisor's read-only look at a version (no batch)

def _viewer_root(ctx):
    """The read-only viewer is reached from Monitor (Runbooks) or from Settings; it stays on the side it came from."""
    return "/monitor" if ctx.req.path.startswith("/monitor/") else "/admin"


def _viewer_for_version(ctx, rid, vid, at=""):
    """The supervisor's read-only look at a version, as its PDF (None when it has none)."""
    pmap = service.pdf_map(vid)
    if not pmap:
        return None
    rb = service.get_runbook(rid)
    v = service.get_version(vid)
    pub = service.published(vid)
    empty = {"ticks": {}, "per": {}, "done": 0, "total": len(pub["checkables"]), "done_ids": set()}
    root = _viewer_root(ctx)
    return _viewer(ctx, pub=pub, summ=empty, pmap=pmap, title=f"{rb['name']} v{v['number']}",
                   heading=f"{rb['name']} runbook", subtitle=f"Version {v['number']} · {v.get('pdf_name') or 'PDF'}",
                   back=f"{root}/runbooks" if root == "/monitor" else f"/admin/runbooks/{rid}",
                   pdf_url=f"{root}/runbooks/{rid}/v/{vid}/runbook.pdf", at=at, file_name=_file_name(rb["name"], f"v{v['number']}"))


@route("GET", f"/monitor/runbooks/{RB}/v/{VER}/runbook.pdf", access="super", name="version_pdf_monitor", audit_it=False)
@route("GET", f"/admin/runbooks/{RB}/v/{VER}/runbook.pdf", access="super", audit_it=False)
def version_pdf(ctx, rid, vid):
    _version_of(rid, vid)
    if not service.pdf_map(vid):
        raise NotFound("This version has no PDF attached.", "Attach it on the runbook's page.")
    rb, v = service.get_runbook(rid), service.get_version(vid)
    return _pdf_response(vid, _file_name(rb["name"], f"v{v['number']}"), ctx.req.query.get("download") == "1")


@route("GET", f"/monitor/runbooks/{RB}/v/{VER}/view", access="super", name="version_view_monitor")
@route("GET", f"/admin/runbooks/{RB}/v/{VER}/view", access="super")
def version_view(ctx, rid, vid):
    _version_of(rid, vid)
    shown = _viewer_for_version(ctx, rid, vid)
    if shown:
        return shown
    first = next((n for n in service.published(vid)["nodes"] if n.get("page")), None)
    if not first:
        raise NotFound("This version has no items.")
    return ctx.redirect(f"{_viewer_root(ctx)}/runbooks/{rid}/v/{vid}/i/{first['id']}")


@route("GET", f"/monitor/runbooks/{RB}/v/{VER}/i/{NODE}", access="super", name="version_item_monitor")
@route("GET", f"/admin/runbooks/{RB}/v/{VER}/i/{NODE}", access="super")
def version_item(ctx, rid, vid, nid):
    rb = service.get_runbook(rid)
    _version_of(rid, vid)
    shown = _viewer_for_version(ctx, rid, vid, at=nid)
    if shown:
        return shown
    pub = service.published(vid)
    node = pub["index"].get(nid)
    if not node or not node.get("page"):
        raise NotFound("This item does not exist in this version.")
    pages = [n for n in pub["nodes"] if n.get("page")]
    k = next(i for i, n in enumerate(pages) if n["id"] == nid)
    empty = {"ticks": {}, "per": {}, "done": 0, "total": len(pub["checkables"]), "done_ids": set()}
    return ctx.render("rb_item.html", title=node["title"], bc=None, proj={"name": rb["name"]}, pub=pub, summ=empty,
                      node=node, html_body=service.item_html(vid, nid), prev_n=pages[k - 1] if k else None,
                      next_n=pages[k + 1] if k + 1 < len(pages) else None, tick=None, note=None,
                      children=[pub["index"][c] for c in node.get("children", []) if c in pub["index"]],
                      tree_view=_tree_view(pub, empty, nid), base=f"{_viewer_root(ctx)}/runbooks/{rid}/v/{vid}", path=service.path_label(pub, node),
                      present=ctx.req.query.get("present") == "1", can_untick=False, readonly=True)

