"""The page router: one Lambda answers every staff page.

For each request: find the route, check the sign-in, check the role and the batch, check that a
POST came from our own page (origin + CSRF token), run the page, and write the audit log for
every POST. Errors become a plain page with what to do next and a request ID; the full details
go to the CloudWatch log only.
"""
import mimetypes
import os
import re
import traceback

from .core import audit, auth, html, perms, util
from .core.http import Request, Response, html as html_response, json_response, redirect, safe_next, q
from .core.util import Forbidden, UserError

ROUTES = []


class Route:
    def __init__(self, method, pattern, fn, access, name, audit_it):
        self.method = method
        self.regex = re.compile("^" + pattern + "$")
        self.pattern = pattern
        self.fn = fn
        self.access = access          # "public", "staff" or "super"
        self.name = name
        self.audit = audit_it


def route(method, pattern, access="staff", name=None, audit_it=True):
    def deco(fn):
        ROUTES.append(Route(method, pattern, fn, access, name or fn.__name__, audit_it))
        return fn
    return deco


class Ctx:
    """Everything a page handler needs."""

    def __init__(self, req):
        self.req = req
        self.staff = None
        self.session = None
        self.cookies = []
        self.flash = None
        self.audit_batch = ""
        self.audit_target = ""
        self.audit_detail = ""
        self.page = {}          # header context: institution, programme, batch, project

    @property
    def csrf(self):
        return (self.session or {}).get("csrf", "")

    def form(self):
        return self.req.form()

    def render(self, template, status=200, **kw):
        if self.flash is None:
            self.flash, clear = auth.read_flash(self.req)
            if clear:
                self.cookies.append(clear)
        ctx = dict(staff=self.staff, csrf=self.csrf, flash=self.flash, req=self.req, page=self.page,
                   nav=build_nav(self.staff, self.req.path) if self.staff else None)
        ctx.update(kw)
        return html_response(html.render(template, **ctx), status=status, cookies=self.cookies)

    def redirect(self, path, flash=None, kind="ok"):
        cookies = list(self.cookies)
        if flash:
            cookies.append(auth.flash_cookie(flash, kind))
        return redirect(path, cookies=cookies)

    def set_batch_page(self, batch_ctx, project=None):
        self.page = dict(batch_ctx)
        if project:
            self.page["project"] = project
        inst = self.page.get("institution") or {}
        if inst.get("logo"):
            self.page["logo"] = "/" + inst["logo"]


SUPER_NAV = [
    ("Delivery", [("Dashboard", "/dashboard", "i-grid"), ("Batches", "/admin/batches", "i-users"),
                  ("Runbooks", "/admin/runbooks", "i-book")]),
    ("Tools", [("Live data", "/admin/live", "i-db"), ("MCQ tests", "/admin/mcq", "i-test")]),
    ("Organisation", [("Institutions", "/admin/institutions", "i-building"), ("Programmes", "/admin/programmes", "i-steps"),
                      ("Trainers", "/admin/trainers", "i-user"), ("Import from CSV", "/admin/import", "i-upload")]),
    ("System", [("Export", "/admin/export", "i-download"), ("Audit log", "/admin/audit", "i-list"),
                ("Health", "/health", "i-pulse")]),
]


def build_nav(staff, path):
    """Side menu: [(section, [(label, href, icon, is_current)])]. The longest matching link is marked."""
    if staff.is_super:
        sections = [(s, list(items)) for s, items in SUPER_NAV]
    else:
        tree = perms.visible_scope(staff)
        batches = [(f"{b['name']}", f"/b/{b['id']}", "i-users") for i in tree for p in i["programmes"]
                   for b in p["batches"]]
        sections = [("", [("Home", "/", "i-home")]), ("My batches", batches)]
    best = ""
    for _, items in sections:
        for _, href, _ in items:
            if (path == href or (href != "/" and path.startswith(href + "/")) or path.startswith(href + "?"))                     and len(href) > len(best):
                best = href
    if path == "/":
        best = "/"
    return [(s, [(label, href, ico, href == best) for label, href, ico in items]) for s, items in sections]


# Pages register themselves on import.
from . import livedata  # noqa: E402,F401  (registers the live-data tables)
from .pages import admin_misc, admin_org, admin_people, auth_pages, home, live, mcq, runbooks, teach  # noqa: E402,F401


def _serve_static(req):
    name = req.path[len("/static/"):]
    if not re.match(r"^[A-Za-z0-9_\-]+\.[a-z0-9]+$", name):
        return None
    path = os.path.join(html.STATIC, name)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as f:
        data = f.read()
    ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype.endswith("javascript"):
        ctype += "; charset=utf-8"
    return Response(200, data, {"content-type": ctype, "cache-control": "public, max-age=3600"}, binary=True)


def _no_session(ctx, rt):
    req = ctx.req
    if req.method == "GET":
        return redirect("/signin?next=" + q(req.full_path()))
    if req.wants_json():
        return json_response({"error": {"code": "signed_out", "message": "Your sign-in has ended.",
                                        "next_step": "Sign in again. Your work on this page is kept."},
                              "signin_url": "/signin?next=" + q(req.headers.get("x-page", "/"))}, 401)
    # A form sent after the sign-in ended: keep what was typed, ask to sign in, then offer to save it.
    # Only for a form that our own page sent (the browser says so in Origin/Referer); the kept form is
    # tied to this browser by a cookie, so a link to it is useless to anybody else.
    origin = req.headers.get("origin") or req.headers.get("referer") or ""
    if not origin or not req.same_origin():
        raise Forbidden("This form did not come from a SkillOps page.", "Open the page again and sign in.")
    form = req.form()
    pid, cookie = auth.stash_pending(req.path, form)
    back = form.get("back") or req.headers.get("x-page") or "/"
    return redirect("/signin?next=" + q(safe_next(back)) + "&pending=" + q(pid), cookies=[cookie])


def dispatch(req):
    if req.path.startswith("/static/") and req.method == "GET":
        resp = _serve_static(req)
        if resp:
            return resp
    match, allowed = None, []
    for rt in ROUTES:
        m = rt.regex.match(req.path)
        if m:
            if rt.method == req.method:
                match = (rt, m.groupdict())
                break
            allowed.append(rt.method)
    ctx = Ctx(req)
    if not match:
        if allowed and req.method == "HEAD":
            return Response(200, "")
        raise util.NotFound("This page does not exist.", "Check the address, or go to the home page.")
    rt, params = match
    if req.method == "POST" and not req.same_origin():
        raise Forbidden("This form did not come from a SkillOps page.", "Open the page again and retry.")
    if rt.access != "public":
        ctx.session = auth.load_session(req)
        if not ctx.session:
            return _no_session(ctx, rt)
        try:
            ctx.staff = perms.staff_from_session(ctx.session)
        except Forbidden:
            ctx.cookies.append(auth.end_session(req))
            raise
    if req.method == "POST":
        if ctx.session:
            sent = req.form().get("csrf") or req.headers.get("x-csrf", "")
            if sent != ctx.session.get("csrf"):
                raise Forbidden("This page is out of date.", "Refresh the page and try again.")
    try:
        if rt.access == "super":
            perms.require_super(ctx.staff)
        resp = rt.fn(ctx, **params)
        if req.method == "POST" and rt.audit and ctx.staff:
            audit.write(ctx.staff.email, ctx.staff.role, rt.name, ctx.audit_batch, ctx.audit_target, "ok",
                        ctx.audit_detail, req.request_id)
        return resp
    except UserError as e:
        if req.method == "POST" and rt.audit and ctx.staff:
            audit.write(ctx.staff.email, ctx.staff.role, rt.name, ctx.audit_batch, ctx.audit_target,
                        "refused" if e.status in (401, 403) else "not done", e.message, req.request_id)
        e.ctx = ctx
        raise
    except Exception:
        if req.method == "POST" and rt.audit and ctx.staff:
            audit.write(ctx.staff.email, ctx.staff.role, rt.name, ctx.audit_batch, ctx.audit_target,
                        "error", "server error", req.request_id)
        raise


def error_page(req, status, message, next_step, ctx=None):
    if req.wants_json():
        return json_response({"error": {"message": message, "next_step": next_step,
                                        "request_id": req.request_id}}, status)
    ctx = ctx or Ctx(req)
    back = req.headers.get("referer") or "/"
    try:
        return ctx.render("error.html", status=status, code=status, title="Something needs attention", message=message,
                          next_step=next_step, request_id=req.request_id, back=back)
    except Exception:  # noqa: BLE001  (the error page must never fail)
        traceback.print_exc()
        return Response(status, f"<h1>{status}</h1><p>Request ID {req.request_id}</p>",
                        {"content-type": "text/html; charset=utf-8"})


def handle(event, context=None):
    if (event.get("rawPath") or "").startswith("/v1/"):
        # The student API. In AWS it has its own Lambda; this path is for local mode and as a fallback.
        from urllib.parse import parse_qsl
        from .livedata import student_routes
        if event.get("queryStringParameters") is None and event.get("rawQueryString"):
            event = dict(event, queryStringParameters=dict(parse_qsl(event["rawQueryString"], keep_blank_values=True)))
        return student_routes.handle(event)
    req = Request(event)
    try:
        resp = dispatch(req)
    except UserError as e:
        resp = error_page(req, e.status, e.message, e.next_step, getattr(e, "ctx", None))
    except Exception:  # noqa: BLE001
        print(f"ERROR request_id={req.request_id} {req.method} {req.path}")
        traceback.print_exc()
        resp = error_page(req, 500, "Something went wrong on our side.",
                          "Try again in a minute. If it happens again, send the request ID below to your supervisor.")
    return resp.to_lambda()


def handler(event, context):
    return handle(event, context)

