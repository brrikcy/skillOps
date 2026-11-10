"""Runbook pages: the supervisor's library (upload, map, preview, publish, versions) and the
navigator trainers use with their batches (one item at a time, ticks, notes, search, present mode)."""
import re
import secrets

from ..core import config, idem, org, perms, store, util
from ..core.http import json_response, q
from ..core.util import NotFound, UserError
from ..runbook import progress, service, tree
from ..web import route

RB = "(?P<rid>r-[a-z0-9]{4,12})"
VER = "(?P<vid>v-[a-z0-9]{4,12})"
NODE = "(?P<nid>n[0-9]{1,6})"
BATCH = "(?P<bid>b-[a-z0-9]{4,12})"
PROJ = "(?P<jid>j-[a-z0-9]{4,12})"
PROG = "(?P<pid>p-[a-z0-9]{4,12})"
STATUS_TEXT = {"uploaded": "Waiting to be read", "reading": "Reading the document", "mapping": "Ready to map",
               "publishing": "Publishing", "published": "Published", "failed": "Could not be read"}


def _version_of(rid, vid):
    v = service.get_version(vid)
    if v["rid"] != rid:
        raise NotFound("This version does not belong to this runbook.")
    return v


def _projects_using(rid):
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
    for u in _projects_using(rid):
        for b in org.list_batches(u["programme"]["id"]):
            pinned = (b.get("versions") or {}).get(u["project"]["id"])
            batches.append({"batch": b, "programme": u["programme"], "project": u["project"],
                            "version": next((v for v in versions if v["id"] == pinned), None)})
    busy = any(v["status"] in ("uploaded", "reading", "publishing") for v in versions)
    return ctx.render("rb_runbook.html", title=rb["name"], rb=rb, versions=versions, batches=batches,
                      upload=_upload_form(ctx, rid), used_by=_projects_using(rid), busy=busy, pubs=pubs)


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
    return ctx.render("rb_map.html", title=f"Map the structure · {rb['name']}", rb=rb, v=v,
                      templates=service.template_scores(v["patterns"]), names=v.get("level_names") or tree.LEVEL_NAMES)


@route("POST", f"/admin/runbooks/{RB}/v/{VER}/map", access="super", name="map runbook structure")
def map_save(ctx, rid, vid):
    _version_of(rid, vid)
    f = ctx.form()
    ctx.audit_target = vid
    levels = {k[4:]: v for k, v in f.items() if k.startswith("lvl:")}
    names = [f.get(f"name{i}", "") for i in range(1, 5)]
    service.save_mapping(vid, levels, f.get("checkable"), names,
                         f.get("template_name", "").strip() if f.get("save_template") else "")
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/preview", flash="Mapping saved. Check the tree below.")


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

@route("POST", f"/admin/programmes/{PROG}/projects/{PROJ}/runbook", access="super", name="choose project runbook")
def choose_runbook(ctx, pid, jid):
    rid = ctx.form().get("runbook_id", "")
    ctx.audit_target = f"{jid} {rid}"
    if rid:
        rb = service.get_runbook(rid)
        if not rb.get("latest"):
            raise UserError("This runbook has no published version yet.", "Publish it first.")
    org.set_project_runbook(pid, jid, rid)
    return ctx.redirect(f"/admin/programmes/{pid}", flash="Runbook chosen for this project.")


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


@route("GET", f"/b/{BATCH}/p/{PROJ}")
def continue_here(ctx, bid, jid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    # "Continue" opens the class view where the class is (the item after the furthest tick).
    return ctx.redirect(f"/b/{bid}/p/{jid}/teach")


@route("GET", f"/b/{BATCH}/p/{PROJ}/i/{NODE}")
def item_page(ctx, bid, jid, nid):
    bc, proj, vid = _nav_context(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
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
                      can_untick=bool(summ["ticks"].get(nid)) and (ctx.staff.is_super or
                                                                   summ["ticks"][nid]["by"] == ctx.staff.email))


def _tick_response(ctx, bid, jid, nid, state, t=None):
    if ctx.req.wants_json():
        return json_response({"ok": True, "state": state, "by": (t or {}).get("by_name", ""),
                              "at": util.show_time((t or {}).get("at", ""))})
    return ctx.redirect(f"/b/{bid}/p/{jid}/i/{nid}")


@route("POST", f"/b/{BATCH}/p/{PROJ}/i/{NODE}/tick", name="mark item complete")
def tick(ctx, bid, jid, nid):
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
    bc, proj, vid = _nav_context(ctx, bid, jid)
    ctx.audit_batch, ctx.audit_target = bid, f"{proj['name']} {nid}"
    if not vid:
        raise UserError("This project has no runbook yet.")
    progress.untick(bid, vid, nid, ctx.staff, jid)
    return _tick_response(ctx, bid, jid, nid, "open")


@route("POST", f"/b/{BATCH}/p/{PROJ}/i/{NODE}/note", name="save item note")
def note(ctx, bid, jid, nid):
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
    summ = progress.summary(bid, vid)
    return ctx.render("rb_item.html", title="About this runbook", bc=bc, proj=proj, pub=summ["pub"], summ=summ,
                      node=None, html_body=service.item_html(vid, "preamble"), prev_n=None,
                      next_n=next((n for n in summ["pub"]["nodes"] if n.get("page")), None), tick=None, note=None,
                      children=[], tree_view=_tree_view(summ["pub"], summ, ""), base=f"/b/{bid}/p/{jid}",
                      path="", present=ctx.req.query.get("present") == "1", can_untick=False)


# ---- the supervisor's read-only look at a version (no batch)

@route("GET", f"/admin/runbooks/{RB}/v/{VER}/view", access="super")
def version_view(ctx, rid, vid):
    _version_of(rid, vid)
    first = next((n for n in service.published(vid)["nodes"] if n.get("page")), None)
    if not first:
        raise NotFound("This version has no items.")
    return ctx.redirect(f"/admin/runbooks/{rid}/v/{vid}/i/{first['id']}")


@route("GET", f"/admin/runbooks/{RB}/v/{VER}/i/{NODE}", access="super")
def version_item(ctx, rid, vid, nid):
    rb = service.get_runbook(rid)
    _version_of(rid, vid)
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
                      tree_view=_tree_view(pub, empty, nid), base=f"/admin/runbooks/{rid}/v/{vid}", path=service.path_label(pub, node),
                      present=ctx.req.query.get("present") == "1", can_untick=False, readonly=True)

