"""Supervisor pages: trainers and the batches they are linked to."""
from urllib.parse import unquote

from ..core import idem, org
from ..core.http import q
from ..web import route

EMAIL = "(?P<email>[^/]{3,200})"


def _email(raw):
    return unquote(raw).strip().lower()


@route("GET", "/admin/trainers", access="super")
def trainers(ctx):
    items = org.list_trainers()
    names = {b["id"]: b["name"] for b in org.list_batches(include_archived=True)}
    for t in items:
        t["batch_names"] = sorted(names.get(b, b) for b in org.trainer_batch_ids(t["email"]))
    return ctx.render("admin_trainers.html", title="Trainers", items=items,
                      web_address=ctx.req.base_url())


@route("POST", "/admin/trainers", access="super", name="create trainer")
def trainer_create(ctx):
    f = ctx.form()
    email = (f.get("email") or "").strip().lower()
    ctx.audit_target = email

    def work():
        org.create_trainer(email, f.get("name"))
        return {"redirect": f"/admin/trainers/{q(email)}",
                "flash": "Trainer added. Cognito has emailed them a temporary password. "
                         "Send them this web address and link their batches below."}
    result = idem.run_once(f.get("idem"), ctx.staff.email, work)
    return ctx.redirect(result["redirect"], flash=result.get("flash"))


@route("GET", f"/admin/trainers/{EMAIL}", access="super")
def trainer_page(ctx, email):
    t = org.get_trainer(_email(email))
    tree = org.scope(None)
    linked = org.trainer_batch_ids(t["email"])
    return ctx.render("admin_trainer.html", title=t["name"], t=t, tree=tree, linked=linked,
                      web_address=ctx.req.base_url())


@route("POST", f"/admin/trainers/{EMAIL}/update", access="super", name="rename trainer")
def trainer_update(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    org.rename_trainer(email, ctx.form().get("name"))
    return ctx.redirect(f"/admin/trainers/{q(email)}", flash="Saved.")


@route("POST", f"/admin/trainers/{EMAIL}/batches", access="super", name="set trainer batches")
def trainer_batches(ctx, email):
    email = _email(email)
    ctx.audit_target = email
    wanted = [b for b in ctx.req.form_list("batch") if b]
    added, removed = org.set_trainer_batches(email, wanted)
    ctx.audit_detail = f"added {','.join(added) or '-'}; removed {','.join(removed) or '-'}"
    return ctx.redirect(f"/admin/trainers/{q(email)}",
                        flash=f"Saved. {len(added)} batch(es) linked, {len(removed)} unlinked.")


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

