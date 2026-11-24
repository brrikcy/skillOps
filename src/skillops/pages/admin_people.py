"""Supervisor pages: trainers (a list of their own), the institutions they work with and their batches."""
from urllib.parse import unquote

from ..core import idem, org
from ..core.http import q
from ..core.util import UserError
from ..web import route

EMAIL = "(?P<email>[^/]{3,200})"


def _email(raw):
    return unquote(raw).strip().lower()


@route("GET", "/admin/trainers", access="super")
def trainers(ctx):
    people = org.trainer_directory()
    insts = org.list_institutions()
    show = ctx.req.query.get("inst", "all")
    if show == "none":
        shown = [t for t in people if not t["institutions"]]
    elif show != "all":
        shown = [t for t in people if any(i["id"] == show for i in t["institutions"])]
    else:
        shown = people
    counts = {i["id"]: sum(1 for t in people if any(x["id"] == i["id"] for x in t["institutions"])) for i in insts}
    return ctx.render("admin_trainers.html", title="Trainers", trainers=shown, show=show, insts=insts,
                      counts=counts, total=len(people), unplaced=sum(1 for t in people if not t["institutions"]),
                      web_address=ctx.req.base_url())


@route("POST", "/admin/trainers", access="super", name="create trainer")
def trainer_create(ctx):
    f = ctx.form()
    email = (f.get("email") or "").strip().lower()
    ctx.audit_target = email

    def work():
        org.create_trainer(email, f.get("name"), (f.get("inst_id") or "").strip() or None)
        return {"redirect": f"/admin/trainers/{q(email)}",
                "flash": "Trainer added. Cognito has emailed them a temporary password. "
                         "Add them to the institutions they work with, then link their batches."}
    result = idem.run_once(f.get("idem"), ctx.staff.email, work)
    return ctx.redirect(result["redirect"], flash=result.get("flash"))


@route("GET", f"/admin/trainers/{EMAIL}", access="super")
def trainer_page(ctx, email):
    t = org.get_trainer(_email(email))
    mine = org.trainer_institution_ids(t["email"])
    # only the batches of the institutions they work with can be linked
    tree = [i for i in org.scope(None) if i["institution"]["id"] in mine]
    insts = org.list_institutions()
    return ctx.render("admin_trainer.html", title=t["name"], t=t, tree=tree, linked=org.trainer_batch_ids(t["email"]),
                      member_of=[i for i in insts if i["id"] in mine],
                      can_join=[i for i in insts if i["id"] not in mine], web_address=ctx.req.base_url())


@route("POST", f"/admin/trainers/{EMAIL}/update", access="super", name="rename trainer")
def trainer_update(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    org.rename_trainer(email, ctx.form().get("name"))
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Saved.")


@route("POST", f"/admin/trainers/{EMAIL}/institutions", access="super", name="add trainer to institution")
def trainer_join(ctx, email):
    email = _email(email)
    iid = ctx.form().get("inst_id", "")
    ctx.audit_target = f"{email} {iid}"
    if not iid:
        raise UserError("Please choose an institution.")
    org.add_trainer_to_institution(email, iid)
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Added. Their batches there can now be linked below.")


@route("POST", f"/admin/trainers/{EMAIL}/institutions/remove", access="super", name="remove trainer from institution")
def trainer_leave(ctx, email):
    email = _email(email)
    iid = ctx.form().get("inst_id", "")
    ctx.audit_target = f"{email} {iid}"
    gone = org.remove_trainer_from_institution(email, iid)
    extra = f" Unlinked from {', '.join(b['name'] for b in gone)}; their past ticks are kept." if gone else ""
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Taken off the institution." + extra)


@route("POST", f"/admin/trainers/{EMAIL}/batches", access="super", name="set trainer batches")
def trainer_batches(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    wanted = [b for b in ctx.req.form_list("batch") if b]
    added, removed = org.set_trainer_batches(email, wanted)
    ctx.audit_detail = f"added {','.join(added) or '-'}; removed {','.join(removed) or '-'}"
    return ctx.redirect(f"/admin/trainers/{q(email)}",
                        flash=f"Saved. {len(added)} batch(es) linked, {len(removed)} unlinked.")


@route("POST", f"/admin/trainers/{EMAIL}/delete", access="super", name="delete trainer")
def trainer_delete(ctx, email):
    from ..core import removal
    email = _email(email)
    t = org.get_trainer(email)
    ctx.audit_target = email
    removal.check_name(ctx.form().get("confirm_name"), t["name"], "trainer")
    removal.delete_trainer(email)
    return ctx.redirect("/admin/trainers", flash=f"{t['name']}'s account is deleted. Their past ticks keep their name.")


@route("POST", f"/admin/trainers/{EMAIL}/disable", access="super", name="turn off trainer")
def trainer_disable(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    org.set_trainer_enabled(email, False)
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Account turned off. They are signed out at once.")


@route("POST", f"/admin/trainers/{EMAIL}/enable", access="super", name="turn on trainer")
def trainer_enable(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    org.set_trainer_enabled(email, True)
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Account turned on.")


@route("POST", f"/admin/trainers/{EMAIL}/resend", access="super", name="resend invitation")
def trainer_resend(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    msg = org.resend_invitation(email)
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash=msg)


@route("POST", f"/admin/trainers/{EMAIL}/reset", access="super", name="reset trainer password")
def trainer_reset(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    msg = org.reset_password(email)
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash=msg)

