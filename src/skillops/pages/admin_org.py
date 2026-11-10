"""Supervisor pages: institutions, programmes (with their ordered projects) and batches."""
from ..core import config, idem, org, store, util
from ..core.util import UserError
from ..web import route

INST = "(?P<iid>i-[a-z0-9]{4,12})"
PROG = "(?P<pid>p-[a-z0-9]{4,12})"
PROJ = "(?P<jid>j-[a-z0-9]{4,12})"
BATCH = "(?P<bid>b-[a-z0-9]{4,12})"

LOGO_TYPES = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg")}


def _once(ctx, fn):
    """Run a write once per form (double clicks and retries are safe), then redirect."""
    result = idem.run_once(ctx.form().get("idem"), ctx.staff.email, fn)
    return ctx.redirect(result.get("redirect", "/admin"), flash=result.get("flash"))


# ------------------------------------------------------------------ index

@route("GET", "/admin", access="super")
def admin_index(ctx):
    return ctx.redirect("/dashboard")


@route("GET", "/admin/programmes", access="super")
def programmes(ctx):
    show_archived = ctx.req.query.get("archived") == "1"
    insts = {i["id"]: i for i in org.list_institutions(include_archived=True)}
    items = org.list_programmes(include_archived=show_archived)
    for p in items:
        p["institution"] = insts.get(p["inst_id"], {})
        p["projects"] = org.list_projects(p["id"])
        p["batch_count"] = len(org.list_batches(p["id"]))
    items.sort(key=lambda p: (p["institution"].get("name", "").lower(), p["name"].lower()))
    return ctx.render("admin_programmes.html", title="Programmes", items=items, show_archived=show_archived,
                      insts=org.list_institutions())


@route("POST", "/admin/programmes", access="super", name="create programme")
def programme_create_any(ctx):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")

    def work():
        pid = org.create_programme(f.get("inst_id", ""), f.get("name"))
        return {"redirect": f"/admin/programmes/{pid}", "flash": "Programme created. Add its projects in order, then its batches."}
    return _once(ctx, work)


# ------------------------------------------------------------------ institutions

@route("GET", "/admin/institutions", access="super")
def institutions(ctx):
    show_archived = ctx.req.query.get("archived") == "1"
    items = org.list_institutions(include_archived=show_archived)
    for i in items:
        i["programme_count"] = len(org.list_programmes(i["id"]))
    return ctx.render("admin_institutions.html", title="Institutions", items=items, show_archived=show_archived)


@route("POST", "/admin/institutions", access="super", name="create institution")
def institution_create(ctx):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")

    def work():
        iid = org.create_institution(f.get("name"), f.get("code"))
        return {"redirect": f"/admin/institutions/{iid}", "flash": "Institution created. Now add its programmes."}
    return _once(ctx, work)


@route("GET", f"/admin/institutions/{INST}", access="super")
def institution_page(ctx, iid):
    inst = org.get_institution(iid)
    show_archived = ctx.req.query.get("archived") == "1"
    programmes = org.list_programmes(iid, include_archived=show_archived)
    for p in programmes:
        p["batch_count"] = len(org.list_batches(p["id"]))
        p["project_count"] = len(org.list_projects(p["id"]))
    ctx.page = {"institution": inst, "logo": "/" + inst["logo"] if inst.get("logo") else ""}
    return ctx.render("admin_institution.html", title=inst["name"], inst=inst, programmes=programmes,
                      show_archived=show_archived)


@route("POST", f"/admin/institutions/{INST}/update", access="super", name="update institution")
def institution_update(ctx, iid):
    f = ctx.form()
    ctx.audit_target = iid
    org.update_institution(iid, f.get("name"), f.get("code"))
    return ctx.redirect(f"/admin/institutions/{iid}", flash="Saved.")


@route("POST", f"/admin/institutions/{INST}/logo", access="super", name="set institution logo")
def institution_logo(ctx, iid):
    ctx.audit_target = iid
    org.get_institution(iid)
    if ctx.form().get("remove"):
        org.set_institution_logo(iid, "")
        return ctx.redirect(f"/admin/institutions/{iid}", flash="Logo removed. (The file is kept.)")
    f = ctx.req.files().get("logo")
    if not f:
        raise UserError("Please choose a logo file.", "Use a PNG or JPEG image under 300 KB.")
    if len(f["data"]) > 300 * 1024:
        raise UserError("The logo file is too large.", "Use a PNG or JPEG image under 300 KB.")
    kind = next((v for k, v in LOGO_TYPES.items() if f["data"].startswith(k)), None)
    if not kind:
        raise UserError("The logo must be a PNG or JPEG image.", "Save the logo as PNG and try again.")
    key = f"media/logos/{iid}-{util.new_id('l')}.{kind[0]}"
    store.s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=f["data"], ContentType=kind[1],
                          CacheControl="public, max-age=86400")
    org.set_institution_logo(iid, key)
    return ctx.redirect(f"/admin/institutions/{iid}", flash="Logo saved.")


def _archive_route(kind, prefix, pattern, param, back):
    @route("POST", f"{prefix}/{pattern}/archive", access="super", name=f"archive {kind}")
    def archive(ctx, **kw):
        ctx.audit_target = kw[param]
        if kind == "batch":
            ctx.audit_batch = kw[param]
        org.set_archived(kind, kw[param], True)
        return ctx.redirect(back(kw), flash=f"The {kind} is archived. Trainers no longer see it; its history is kept.")

    @route("POST", f"{prefix}/{pattern}/unarchive", access="super", name=f"unarchive {kind}")
    def unarchive(ctx, **kw):
        ctx.audit_target = kw[param]
        if kind == "batch":
            ctx.audit_batch = kw[param]
        org.set_archived(kind, kw[param], False)
        return ctx.redirect(back(kw), flash=f"The {kind} is active again.")


_archive_route("institution", "/admin/institutions", INST, "iid", lambda kw: f"/admin/institutions/{kw['iid']}")
_archive_route("programme", "/admin/programmes", PROG, "pid", lambda kw: f"/admin/programmes/{kw['pid']}")
_archive_route("batch", "/admin/batches", BATCH, "bid", lambda kw: f"/admin/batches/{kw['bid']}")


# ------------------------------------------------------------------ programmes

@route("POST", f"/admin/institutions/{INST}/programmes", access="super", name="create programme")
def programme_create(ctx, iid):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")

    def work():
        pid = org.create_programme(iid, f.get("name"))
        return {"redirect": f"/admin/programmes/{pid}", "flash": "Programme created. Add its projects in order, then its batches."}
    return _once(ctx, work)


@route("GET", f"/admin/programmes/{PROG}", access="super")
def programme_page(ctx, pid):
    prog = org.get_programme(pid)
    inst = org.get_institution(prog["inst_id"])
    show_archived = ctx.req.query.get("archived") == "1"
    batches = org.list_batches(pid, include_archived=show_archived)
    for b in batches:
        b["trainers"] = org.batch_trainers(b["id"])
    ctx.page = {"institution": inst, "programme": prog, "logo": "/" + inst["logo"] if inst.get("logo") else ""}
    from ..runbook import service
    runbooks = [r for r in service.list_runbooks() if r.get("latest")]
    names = {r["id"]: r["name"] for r in service.list_runbooks()}
    return ctx.render("admin_programme.html", title=prog["name"], prog=prog, inst=inst, runbooks=runbooks,
                      rb_names=names, projects=org.list_projects(pid), batches=batches, show_archived=show_archived)


@route("POST", f"/admin/programmes/{PROG}/update", access="super", name="rename programme")
def programme_update(ctx, pid):
    ctx.audit_target = pid
    org.rename_programme(pid, ctx.form().get("name"))
    return ctx.redirect(f"/admin/programmes/{pid}", flash="Saved.")


@route("POST", f"/admin/programmes/{PROG}/projects", access="super", name="add project")
def project_add(ctx, pid):
    f = ctx.form()
    ctx.audit_target = f"{pid} {f.get('name', '')}"

    def work():
        org.add_project(pid, f.get("name"))
        return {"redirect": f"/admin/programmes/{pid}", "flash": "Project added at the end of the list."}
    return _once(ctx, work)


@route("POST", f"/admin/programmes/{PROG}/projects/{PROJ}/move", access="super", name="move project")
def project_move(ctx, pid, jid):
    ctx.audit_target = jid
    direction = ctx.form().get("direction")
    if direction not in ("up", "down"):
        raise UserError("Choose up or down.")

    def work():          # a double click must move the project only once
        org.move_project(pid, jid, direction)
        return {"redirect": f"/admin/programmes/{pid}"}
    return _once(ctx, work)


@route("POST", f"/admin/programmes/{PROG}/projects/{PROJ}/rename", access="super", name="rename project")
def project_rename(ctx, pid, jid):
    ctx.audit_target = jid
    org.rename_project(pid, jid, ctx.form().get("name"))
    return ctx.redirect(f"/admin/programmes/{pid}", flash="Project renamed.")


@route("POST", f"/admin/programmes/{PROG}/projects/{PROJ}/archive", access="super", name="remove project")
def project_archive(ctx, pid, jid):
    ctx.audit_target = jid
    org.set_project_archived(pid, jid, True)
    return ctx.redirect(f"/admin/programmes/{pid}", flash="Project taken out of the programme. Its ticks are kept.")


# ------------------------------------------------------------------ batches

@route("POST", f"/admin/programmes/{PROG}/batches", access="super", name="create batch")
def batch_create(ctx, pid):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")

    def work():
        bid = org.create_batch(pid, f.get("name"), f.get("mcq_batch", ""))
        return {"redirect": f"/admin/batches/{bid}", "flash": "Batch created. Now link its trainers."}
    return _once(ctx, work)


@route("GET", "/admin/batches", access="super")
def batches(ctx):
    show_archived = ctx.req.query.get("archived") == "1"
    tree = org.scope(None, include_archived=show_archived)
    for i in tree:
        for p in i["programmes"]:
            for b in p["batches"]:
                b["trainers"] = org.batch_trainers(b["id"])
    return ctx.render("admin_batches.html", title="Batches", tree=tree, show_archived=show_archived)


@route("GET", f"/admin/batches/{BATCH}", access="super")
def batch_page(ctx, bid):
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    linked = org.batch_trainers(bid)
    linked_emails = {t["email"] for t in linked}
    others = [t for t in org.list_trainers() if t["email"] not in linked_emails]
    from ..runbook import service
    projects = []
    for j in org.list_projects(bc["batch"]["prog_id"]):
        pinned = (bc["batch"].get("versions") or {}).get(j["id"])
        vs = [v for v in service.list_versions(j["runbook_id"]) if v["status"] == "published"] if j.get("runbook_id") else []
        projects.append({"project": j, "versions": vs, "pinned": next((v for v in vs if v["id"] == pinned), None)})
    return ctx.render("admin_batch.html", title=bc["batch"]["name"], bc=bc, linked=linked, others=others,
                      projects=projects)


@route("POST", f"/admin/batches/{BATCH}/update", access="super", name="update batch")
def batch_update(ctx, bid):
    f = ctx.form()
    ctx.audit_batch = ctx.audit_target = bid
    org.update_batch(bid, f.get("name"), f.get("mcq_batch", ""))
    return ctx.redirect(f"/admin/batches/{bid}", flash="Saved.")


@route("POST", f"/admin/batches/{BATCH}/trainers", access="super", name="link trainer to batch")
def batch_add_trainer(ctx, bid):
    email = (ctx.form().get("email") or "").strip().lower()
    ctx.audit_batch, ctx.audit_target = bid, email
    if not email:
        raise UserError("Please choose a trainer.")
    org.associate(email, bid)
    return ctx.redirect(f"/admin/batches/{bid}", flash="Trainer linked to this batch.")


@route("POST", f"/admin/batches/{BATCH}/trainers/remove", access="super", name="unlink trainer from batch")
def batch_remove_trainer(ctx, bid):
    email = (ctx.form().get("email") or "").strip().lower()
    ctx.audit_batch, ctx.audit_target = bid, email
    org.dissociate(email, bid)
    return ctx.redirect(f"/admin/batches/{bid}", flash="Trainer unlinked. Their past ticks are kept.")
