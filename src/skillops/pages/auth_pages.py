"""Sign in, first password, forgot password, sign out, and finishing a form after signing in again."""
import secrets

from ..core import audit, auth, config, store, util
from ..core.http import q, safe_next
from ..core.util import UserError
from ..web import route


def _done_signing_in(ctx, ident, next_path, pending):
    sid_cookie = auth.create_session(ident["email"], ident["role"], ident["name"])[1]
    audit.write(ident["email"], ident["role"], "signin", result="ok", request_id=ctx.req.request_id)
    ctx.cookies.append(sid_cookie)
    target = safe_next(next_path)
    if pending and auth.get_pending(pending, ctx.req):
        target = "/resume/" + q(pending) + "?next=" + q(target)
    return ctx.redirect(target)


def _signin_page(ctx, status=200, error=None, email=""):
    users = auth.local_users() if config.local_mode() else []
    src = ctx.req.form() if ctx.req.method == "POST" else ctx.req.query
    return ctx.render("signin.html", status=status, title="Sign in", error=error, email=email,
                      next=safe_next(src.get("next", "/")), pending=src.get("pending", ""), local_users=users)


@route("GET", "/signin", access="public")
def signin_form(ctx):
    sess = auth.load_session(ctx.req)
    if sess and not ctx.req.query.get("pending"):
        return ctx.redirect(safe_next(ctx.req.query.get("next", "/")))
    return _signin_page(ctx)


@route("POST", "/signin", access="public", audit_it=False)
def signin_submit(ctx):
    f = ctx.form()
    email = (f.get("email") or "").strip().lower()
    try:
        if config.local_mode() and f.get("local_user"):
            ident = auth.local_sign_in(f["local_user"])
            return _done_signing_in(ctx, ident, f.get("next"), f.get("pending"))
        status, data = auth.password_sign_in(email, f.get("password", ""))
    except UserError as e:
        audit.write(email or "(blank)", "", "signin", result="failed", detail=e.message,
                    request_id=ctx.req.request_id)
        return _signin_page(ctx, status=e.status, error=e, email=email)
    if status == "new_password":
        cid = secrets.token_urlsafe(12)
        store.t("CORE").put_item(Item={"pk": f"PENDING#{cid}", "sk": "META", "kind": "challenge", "email": email,
                                       "session": data, "next": safe_next(f.get("next")),
                                       "pending": f.get("pending", ""), "ttl": int(util.now_ts() + 900)})
        return ctx.redirect("/signin/new-password?c=" + q(cid))
    if status == "reset":
        return ctx.redirect("/signin/reset?email=" + q(email),
                            flash="Your supervisor has reset your password. Check your email for the code.")
    return _done_signing_in(ctx, data, f.get("next"), f.get("pending"))


def _challenge(cid):
    item = auth.get_pending(cid)
    if not item or item.get("kind") != "challenge":
        raise UserError("This step has expired.", "Sign in again with your temporary password.")
    return item


@route("GET", "/signin/new-password", access="public")
def new_password_form(ctx):
    ch = _challenge(ctx.req.query.get("c"))
    return ctx.render("signin_new_password.html", title="Choose your password", email=ch["email"],
                      c=ctx.req.query.get("c"), error=None)


@route("POST", "/signin/new-password", access="public", audit_it=False)
def new_password_submit(ctx):
    f = ctx.form()
    ch = _challenge(f.get("c"))
    try:
        if f.get("password") != f.get("password2"):
            raise UserError("The two passwords are not the same.", "Type the same new password twice.")
        ident = auth.complete_new_password(ch["email"], ch["session"], f.get("password", ""))
    except UserError as e:
        return ctx.render("signin_new_password.html", status=e.status, title="Choose your password",
                          email=ch["email"], c=f.get("c"), error=e)
    store.t("CORE").delete_item(Key={"pk": f"PENDING#{f.get('c')}", "sk": "META"})
    return _done_signing_in(ctx, ident, ch.get("next"), ch.get("pending"))


@route("GET", "/signin/forgot", access="public")
def forgot_form(ctx):
    return ctx.render("signin_forgot.html", title="Forgot password", error=None,
                      email=ctx.req.query.get("email", ""))


@route("POST", "/signin/forgot", access="public", audit_it=False)
def forgot_submit(ctx):
    email = (ctx.form().get("email") or "").strip().lower()
    try:
        auth.start_forgot(email)
    except UserError as e:
        return ctx.render("signin_forgot.html", status=e.status, title="Forgot password", error=e, email=email)
    return ctx.redirect("/signin/reset?email=" + q(email),
                        flash="If this email has a SkillOps account, a code is on its way. It can take a few minutes.")


@route("GET", "/signin/reset", access="public")
def reset_form(ctx):
    return ctx.render("signin_reset.html", title="Set a new password", error=None,
                      email=ctx.req.query.get("email", ""))


@route("POST", "/signin/reset", access="public", audit_it=False)
def reset_submit(ctx):
    f = ctx.form()
    email = (f.get("email") or "").strip().lower()
    try:
        if f.get("password") != f.get("password2"):
            raise UserError("The two passwords are not the same.", "Type the same new password twice.")
        auth.confirm_forgot(email, f.get("code"), f.get("password"))
    except UserError as e:
        return ctx.render("signin_reset.html", status=e.status, title="Set a new password", error=e, email=email)
    audit.write(email, "", "password reset", result="ok", request_id=ctx.req.request_id)
    return ctx.redirect("/signin", flash="Your new password is saved. Sign in with it now.")


@route("POST", "/signout", name="signout")
def signout(ctx):
    ctx.cookies.append(auth.end_session(ctx.req))
    return ctx.redirect("/signin", flash="You have signed out.")


@route("GET", "/resume/(?P<pid>[A-Za-z0-9_\\-]{1,40})")
def resume(ctx, pid):
    item = auth.get_pending(pid, ctx.req)
    if not item or item.get("kind") != "form":
        return ctx.redirect(safe_next(ctx.req.query.get("next", "/")),
                            flash="The saved form has expired (after one hour). Please do it again.", kind="warn")
    shown = {k: v for k, v in item["form"].items() if k not in ("idem", "back", "c") and v}
    return ctx.render("resume.html", title="Finish saving", item=item, shown=shown,
                      next=safe_next(ctx.req.query.get("next", "/")))
