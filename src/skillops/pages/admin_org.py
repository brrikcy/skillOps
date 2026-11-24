"""Supervisor pages: Robomy's courses and projects; institutions, their programmes, batches and trainers."""
from ..core import config, idem, org, store, util
from ..core.util import UserError
from ..web import route

COURSE = "(?P<cid>c-[a-z0-9]{4,12})"
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
    """Programmes live under their institution."""
    return ctx.redirect("/admin/institutions")


# ------------------------------------------------------------------ courses (Robomy's) and their projects

@route("GET", "/admin/courses", access="super")
def courses(ctx):
    items = org.list_courses()
    for c in items:
        c["projects"] = org.course_projects(c["id"])
        c["programmes"] = org.programmes_using_course(c["id"])
    return ctx.render("admin_courses.html", title="Courses", items=items)


@route("POST", "/admin/courses", access="super", name="create course")
def course_create(ctx):
    f = ctx.form()
    ctx.audit_target = f.get("name", "")

    def work():
        cid = org.create_course(f.get("name"))
        return {"redirect": f"/admin/courses/{cid}", "flash": "Course created. Add its projects in teaching order."}
    return _once(ctx, work)


@route("GET", f"/admin/courses/{COURSE}", access="super")
def course_page(ctx, cid):
    course = org.get_course(cid)
    from ..runbook import service
    all_rb = service.list_runbooks()
    insts = {i["id"]: i for i in org.list_institutions(include_archived=True)}
    progs = org.programmes_using_course(cid)
    for p in progs:
        p["institution"] = insts.get(p["inst_id"], {})
    return ctx.render("admin_course.html", title=course["name"], course=course,
                      projects=org.course_projects(cid), runbooks=[r for r in all_rb if r.get("latest")],
                      rb_names={r["id"]: r["name"] for r in all_rb}, programmes=progs)


@route("POST", f"/admin/courses/{COURSE}/update", access="super", name="rename course")
def course_update(ctx, cid):
    ctx.audit_target = cid
    org.rename_course(cid, ctx.form().get("name"))
    return ctx.redirect(f"/admin/courses/{cid}", flash="Saved.")


@route("POST", f"/admin/courses/{COURSE}/delete", access="super", name="delete course")
def course_delete(ctx, cid):
    from ..core import removal
    c = org.get_course(cid)
    ctx.audit_target = f"{c['name']} ({cid})"
    removal.check_name(ctx.form().get("confirm_name"), c["name"], "course")
    removal.delete_course(cid)
    return ctx.redirect("/admin/courses", flash=f"{c['name']} is deleted, with its projects.")


@route("POST", f"/admin/courses/{COURSE}/projects", access="super", name="add project")
def project_add(ctx, cid):
    f = ctx.form()
    ctx.audit_target = f"{cid} {f.get('name', '')}"

    def work():
        org.add_project(cid, f.get("name"))
        return {"redirect": f"/admin/courses/{cid}",
                "flash": "Project added at the end. Programmes that use this course get it too."}
    return _once(ctx, work)


@route("POST", f"/admin/projects/{PROJ}/move", access="super", name="move project")
def project_move(ctx, jid):
    ctx.audit_target = jid
    direction = ctx.form().get("direction")
    if direction not in ("up", "down"):
        raise UserError("Choose up or down.")
    j = org.project_item(jid)

    def work():          # a double click must move the project only once
        org.move_project(jid, direction)
        return {"redirect": f"/admin/courses/{j['course_id']}"}
    return _once(ctx, work)


@route("POST", f"/admin/projects/{PROJ}/rename", access="super", name="rename project")
def project_rename(ctx, jid):
    ctx.audit_target = jid
    j = org.project_item(jid)
    org.rename_project(jid, ctx.form().get("name"))
    return ctx.redirect(f"/admin/courses/{j['course_id']}", flash="Project renamed.")


@route("POST", f"/admin/projects/{PROJ}/delete", access="super", name="delete project")
def project_delete(ctx, jid):
    from ..core import removal
    j = removal.delete_project(jid)
    ctx.audit_target = f"{j['name']} ({jid})"
    return ctx.redirect(f"/admin/courses/{j['course_id']}",
                        flash=f"Project {j['name']} is deleted. Every programme that had it has lost it.")


# ------------------------------------------------------------------ institutions

@route("GET", "/admin/institutions", access="super")
def institutions(ctx):
    show_archived = ctx.req.query.get("archived") == "1"
    items = org.list_institutions(include_archived=show_archived)
    for i in items:
        i["programme_count"] = len(org.list_programmes(i["id"]))
        i["trainer_count"] = len(org.institution_trainers(i["id"]))
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
    members = org.institution_trainers(iid)
    here = {b["id"]: b for b in org.list_batches(include_archived=True) if b.get("inst_id") == iid}
    for t in members:
        t["batches_here"] = sorted((here[b]["name"] for b in org.trainer_batch_ids(t["email"]) if b in here))
    member_emails = {t["email"] for t in members}
    others = [t for t in org.list_trainers() if t["email"] not in member_emails]
    return ctx.render("admin_institution.html", title=inst["name"], inst=inst, programmes=programmes,
                      trainers=members, others=others, show_archived=show_archived)


@route("POST", f"/admin/institutions/{INST}/trainers", access="super", name="add trainer to institution")
def institution_add_trainer(ctx, iid):
    """Add an existing trainer, or a new one (name and email), to this institution."""
    f = ctx.form()
    inst = org.get_institution(iid)
    email = (f.get("email") or "").strip().lower()
    ctx.audit_target = f"{iid} {email}"
    if not email:
        raise UserError("Please choose a trainer, or give a new trainer's name and email.")

    def work():
        if f.get("name") and not org.get_trainer(email, required=False):
            org.create_trainer(email, f.get("name"), iid)
            return {"redirect": f"/admin/institutions/{iid}",
                    "flash": f"Trainer added to {inst['name']}. Cognito has emailed them a temporary password."}
        org.add_trainer_to_institution(email, iid)
        return {"redirect": f"/admin/institutions/{iid}", "flash": f"Trainer added to {inst['name']}."}
    return _once(ctx, work)


@route("POST", f"/admin/institutions/{INST}/trainers/remove", access="super", name="remove trainer from institution")
def institution_remove_trainer(ctx, iid):
    email = (ctx.form().get("email") or "").strip().lower()
    ctx.audit_target = f"{iid} {email}"
    inst = org.get_institution(iid)
    gone = org.remove_trainer_from_institution(email, iid)
    extra = f" Unlinked from {', '.join(b['name'] for b in gone)}; their past ticks are kept." if gone else ""
    return ctx.redirect(f"/admin/institutions/{iid}", flash=f"Trainer taken off {inst['name']}.{extra}")


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


def _delete_route(kind, prefix, pattern, param, get, remove, done):
    @route("POST", f"{prefix}/{pattern}/delete", access="super", name=f"delete {kind}")
    def delete(ctx, **kw):
        from ..core import removal
        thing = get(kw[param])
        ctx.audit_target = f"{thing['name']} ({kw[param]})"
        if kind == "batch":
            ctx.audit_batch = kw[param]
        removal.check_name(ctx.form().get("confirm_name"), thing["name"], kind)
        remove(kw[param])
        return ctx.redirect(done(thing), flash=f"{thing['name']} is deleted, with everything in it.")


def _deletes():
    from ..core import removal
    _delete_route("institution", "/admin/institutions", INST, "iid", org.get_institution, removal.delete_institution,
                  lambda t: "/admin/institutions")
    _delete_route("programme", "/admin/programmes", PROG, "pid", org.get_programme, removal.delete_programme,
                  lambda t: f"/admin/institutions/{t['inst_id']}")
    _delete_route("batch", "/admin/batches", BATCH, "bid", org.get_batch, removal.delete_batch,
                  lambda t: f"/admin/programmes/{t['prog_id']}")


_deletes()
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
        return {"redirect": f"/admin/programmes/{pid}", "flash": "Programme created. Choose its courses, then add its batches."}
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
    rb_names = {r["id"]: r["name"] for r in service.list_runbooks()}
    courses = {c["id"]: c for c in org.list_courses()}
    for c in courses.values():
        c["count"] = len(org.course_projects(c["id"]))
    chosen = [courses[c] for c in prog.get("courses") or [] if c in courses]
    resolved = org.list_projects(pid, include_archived=True)
    in_prog = {p["id"] for p in resolved}
    outside = [(c, [j for j in js if j["id"] not in in_prog]) for c, js in org.all_projects()]
    return ctx.render("admin_programme.html", title=prog["name"], prog=prog, inst=inst, batches=batches,
                      show_archived=show_archived, all_courses=list(courses.values()), chosen=chosen,
                      projects=[p for p in resolved if not p["removed"]],
                      taken_out=[p for p in resolved if p["removed"]],
                      outside=[(c, js) for c, js in outside if js], rb_names=rb_names)


@route("POST", f"/admin/programmes/{PROG}/update", access="super", name="rename programme")
def programme_update(ctx, pid):
    ctx.audit_target = pid
    org.rename_programme(pid, ctx.form().get("name"))
    return ctx.redirect(f"/admin/programmes/{pid}", flash="Saved.")


@route("POST", f"/admin/programmes/{PROG}/courses", access="super", name="choose programme courses")
def programme_courses(ctx, pid):
    wanted = [c for c in ctx.req.form_list("course") if c]
    ctx.audit_target = f"{pid} {','.join(wanted) or '-'}"
    org.set_programme_courses(pid, wanted)
    return ctx.redirect(f"/admin/programmes/{pid}",
                        flash="Courses saved. Their projects are now the programme's projects." if wanted
                        else "No course is selected now.")


@route("POST", f"/admin/programmes/{PROG}/courses/{COURSE}/move", access="super", name="move programme course")
def programme_course_move(ctx, pid, cid):
    ctx.audit_target = f"{pid} {cid}"
    direction = ctx.form().get("direction")
    if direction not in ("up", "down"):
        raise UserError("Choose up or down.")

    def work():
        org.move_programme_course(pid, cid, direction)
        return {"redirect": f"/admin/programmes/{pid}"}
    return _once(ctx, work)


@route("POST", f"/admin/programmes/{PROG}/projects", access="super", name="add project to programme")
def programme_add_project(ctx, pid):
    jid = ctx.form().get("project", "")
    ctx.audit_target = f"{pid} {jid}"
    if not jid:
        raise UserError("Please choose a project.")
    result = org.add_programme_project(pid, jid)
    msg = {"back": "Project put back in the programme.", "added": "Project added at the end of the programme.",
           "already": "That project is already in the programme."}[result]
    return ctx.redirect(f"/admin/programmes/{pid}", flash=msg)


@route("POST", f"/admin/programmes/{PROG}/projects/{PROJ}/remove", access="super", name="take project out of programme")
def programme_remove_project(ctx, pid, jid):
    ctx.audit_target = f"{pid} {jid}"
    j = org.remove_programme_project(pid, jid)
    return ctx.redirect(f"/admin/programmes/{pid}",
                        flash=f"{j['name']} is taken out of this programme. Its ticks are kept; put it back any time.")


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
    others = [t for t in org.institution_trainers(bc["batch"]["inst_id"]) if t["email"] not in linked_emails]
    from ..runbook import service
    projects = []
    for j in org.list_projects(bc["batch"]["prog_id"]):
        pinned = (bc["batch"].get("versions") or {}).get(j["id"])
        vs = [v for v in service.list_versions(j["runbook_id"]) if v["status"] == "published"] if j.get("runbook_id") else []
        projects.append({"project": j, "versions": vs, "pinned": next((v for v in vs if v["id"] == pinned), None)})
    from .. import classdays
    from .. import dashboard
    return ctx.render("admin_batch.html", title=bc["batch"]["name"], bc=bc, linked=linked, others=others,
                      class_days=classdays.get(bc["batch"]),
                      projects=projects, d=dashboard.batch_detail(bid))


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
