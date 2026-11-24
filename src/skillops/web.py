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
    def area(self):
        return area_of(self.req.path) if self.staff and self.staff.is_super else "monitor"

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
        is_super = bool(self.staff and self.staff.is_super)
        area = self.area
        bid = (self.page.get("batch") or {}).get("id")
        ctx = dict(staff=self.staff, csrf=self.csrf, flash=self.flash, req=self.req, page=self.page,
                   nav=build_nav(self.staff, self.req.path) if is_super else None,
                   batchbar=build_batchbar(self.staff, self.req.path) if self.staff and not is_super else None,
                   settingsbar=build_settingsbar(self.req.path) if is_super and area == "settings" else None,
                   role="admin" if is_super else "trainer" if self.staff else None, area=area,
                   # Settings opens in a pane over the page: the pane's own pages load inside it, without the page chrome
                   embed=self.req.headers.get("sec-fetch-dest") == "iframe",
                   settings_groups=SETTINGS_NAV if is_super else None,
                   # The admin changes things only in Settings; the monitoring pages show the same data read-only.
                   can_edit=not is_super or area == "settings",
                   # where a batch's own pages live: Settings > Batches for the admin there, else the batch page
                   bbase=(f"/admin/batches/{bid}" if is_super and area == "settings" else f"/b/{bid}") if bid else "",
                   trail=None)
        ctx.update(kw)
        if self.staff and ctx["trail"] is None:
            try:
                ctx["trail"] = breadcrumbs(self.req.path, self.staff, kw.get("title", ""))
            except Exception:  # noqa: BLE001  (a page never fails because of its trail)
                ctx["trail"] = []
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


# The admin's side menu is for MONITORING only: every page it opens is read-only. Everything that changes
# something (set-up, people, content, tools, data) lives in Settings, opened from the profile menu.
MONITOR_NAV = [
    ("Overview", [("Dashboard", "/dashboard", "i-grid")]),
    ("Delivery", [("Courses", "/monitor/courses", "i-steps"),
                  ("Runbooks", "/monitor/runbooks", "i-book")]),
    ("Tools", [("Live Data Simulator", "/monitor/live", "i-db"), ("MCQ tests", "/monitor/mcq", "i-test")]),
    ("System", [("Audit log", "/monitor/audit", "i-list"), ("Health", "/health", "i-pulse")]),
]
SUPER_NAV = MONITOR_NAV        # old name, kept for callers

# Settings: the admin's operations area (profile menu > Settings). Its own bar runs across the top of each page.
SETTINGS_NAV = [
    ("Organisation", [("Institutions", "/admin/institutions", "i-building"), ("Batches", "/admin/batches", "i-users"),
                      ("Trainers", "/admin/trainers", "i-user")]),
    ("Content", [("Courses", "/admin/courses", "i-steps"), ("Runbooks", "/admin/runbooks", "i-book")]),
    ("Tools", [("Live Data Simulator", "/admin/live", "i-db"), ("MCQ connections", "/admin/mcq", "i-test")]),
    ("Data", [("Import from CSV", "/admin/import", "i-upload"), ("Export", "/admin/export", "i-download")]),
]
SETTINGS_DESC = {
    "/admin/institutions": "Partner institutions, their programmes and logos.",
    "/admin/batches": "Trainers, class days, current project, runbook versions and classroom tools of each batch.",
    "/admin/trainers": "Trainer accounts, invitations, institutions and batches.",
    "/admin/courses": "Robomy courses and their projects in teaching order.",
    "/admin/runbooks": "Upload, map and publish runbook versions.",
    "/admin/live": "Upload data, pause or resume feeds, set the clock.",
    "/admin/mcq": "Connect each institution's MCQ Google Sheet.",
    "/admin/import": "Set up institutions, batches and trainers from one CSV file.",
    "/admin/export": "Download everything as one file.",
}


def area_of(path):
    """'settings' for the admin's operations pages, else 'monitor'."""
    return "settings" if path == "/settings" or path.startswith(("/settings/", "/admin")) else "monitor"


def build_settingsbar(path):
    """The bar across the top of every Settings page: [(label, href, icon, is_current)]."""
    best = ""
    for _, items in SETTINGS_NAV:
        for _, href, _ in items:
            if (path == href or path.startswith(href + "/")) and len(href) > len(best):
                best = href
    return [(label, href, ico, href == best) for _, items in SETTINGS_NAV for label, href, ico in items]


# GET pages that only send you on somewhere else: never a "Back" target.
_JUMP_ONLY = (re.compile(r"^/admin/programmes$"), re.compile(r"^/b/[^/]+/p/[^/]+$"), re.compile(r"^/b/[^/]+/p/[^/]+/teach$"),
              re.compile(r"^/(admin|monitor)/runbooks/[^/]+/v/[^/]+/view$"))


def _is_page(path):
    if any(rx.match(path) for rx in _JUMP_ONLY):
        return False
    return any(rt.method == "GET" and rt.regex.match(path) for rt in ROUTES)


def parent_of(path, staff):
    """Where the Back button goes when there is no page to go back to: the nearest page above this one.

    Menu pages (the top of each area) have no Back button. A batch page goes back to Batches for the
    supervisor; for a trainer it is a top page, reached from the batch bar."""
    path = path.rstrip("/") or "/"
    tops = {"/", "/dashboard", "/team", "/settings"} | {href for _, items in MONITOR_NAV for _, href, _ in items}
    if path in tops:
        return None
    if any(path == href for _, items in SETTINGS_NAV for _, href, _ in items):
        return "/settings"                                       # Settings > Institutions, and so on
    if re.match(r"^/b/[^/]+$", path):
        return "/dashboard" if staff.is_super else None           # a trainer's batch is a top page (batch bar)
    m = re.match(r"^/admin/programmes/(p-[a-z0-9]+)$", path)
    if m:                                                        # a programme lives under its institution
        from .core import org
        prog = org.get_programme(m.group(1), required=False)
        return f"/admin/institutions/{prog['inst_id']}" if prog else "/admin/institutions"
    parts = path.split("/")
    while len(parts) > 2:
        parts.pop()
        cand = "/".join(parts)
        if _is_page(cand):
            return cand
    return "/" if not staff.is_super else "/dashboard"


_SECTION_LABELS = {"live": "Live Data Simulator", "mcq": "MCQ tests", "tools": "Tools", "recap": "Recap",
                   "calendar": "Calendar", "today": "Today", "v": "Versions", "monitor": "Monitor", "results": "Results",
                   "register": "Marks register", "imports": "Uploads"}


def _label(path, staff):
    """A short name for one step of the trail: the menu label, or the name of the thing the path points at."""
    from .core import org
    for _, items in MONITOR_NAV + SETTINGS_NAV:
        for label, href, _ in items:
            if href == path:
                return label
    if path == "/":
        return "Home"
    fixed = {"/dashboard": "Dashboard", "/team": "Team progress", "/settings": "Settings"}
    if path in fixed:
        return fixed[path]
    last = path.rsplit("/", 1)[-1]
    try:
        if last.startswith("b-"):
            name = org.get_batch(last)["name"]
            return f"{name} · progress" if path.startswith("/dashboard/") else name
        if last.startswith("i-"):
            return org.get_institution(last)["name"]
        if last.startswith("c-"):
            return org.get_course(last)["name"]
        if last.startswith("p-"):
            return org.get_programme(last)["name"]
        if last.startswith("r-"):
            from .runbook import service
            return service.get_runbook(last)["name"]
        if last.startswith("v-"):
            from .runbook import service
            return f"v{service.get_version(last)['number']}"
        if last.startswith("imp-"):
            from .livedata import imports
            return imports.get(last)["filename"]
        if "%40" in last or "@" in last:
            from urllib.parse import unquote
            return org.get_trainer(unquote(last))["name"]
    except Exception:  # noqa: BLE001  (a name that cannot be found still gives a usable link)
        pass
    return _SECTION_LABELS.get(last, last.replace("-", " ").capitalize())


def breadcrumbs(path, staff, title=""):
    """[(label, href)] from the top of the area down to this page; the last one (this page) has href None.
    Every earlier step is a link, so any level above is one click away. Menu pages have no trail."""
    path = path.rstrip("/") or "/"
    chain, p = [], path
    while len(chain) < 8:
        par = parent_of(p, staff)
        if not par or par == p:
            break
        chain.insert(0, par)
        p = par
    if not chain:
        return []
    out = [(_label(c, staff), c) for c in chain]
    m = re.match(r"^/b/(b-[a-z0-9]+)/p/(j-[a-z0-9]+)/(?!intro$)", path + "/")
    if m and not path.endswith("/intro"):
        from .core import org
        try:
            b = org.get_batch(m.group(1))
            out.append((org.get_project(b["prog_id"], m.group(2))["name"], f"/b/{m.group(1)}/p/{m.group(2)}/intro"))
        except Exception:  # noqa: BLE001
            pass
    here = (title or "").split(" · ")[0].strip() or _label(path, staff)
    if out and here == out[-1][0]:
        here = _label(path, staff)
    out.append((here, None))
    return out


def trainer_batches(staff):
    """A trainer's batches in menu order: [{"id", "name", "label", "where"}]. Two batches with the same name
    (in different programmes) are told apart by the programme's name."""
    rows = [{"id": b["id"], "name": b["name"], "programme": p["programme"]["name"],
             "where": f"{i['institution']['name']} · {p['programme']['name']}"}
            for i in perms.visible_scope(staff) for p in i["programmes"] for b in p["batches"]]
    names = [r["name"].lower() for r in rows]
    for r in rows:
        r["label"] = r["name"] if names.count(r["name"].lower()) == 1 else f"{r['name']} · {r['programme']}"
    return rows


def build_batchbar(staff, path):
    """The trainer's bar under the title: one tab per batch, and Team progress at the end."""
    m = re.match(r"^/b/(b-[a-z0-9]+)", path)
    here = m.group(1) if m else None
    return {"batches": [dict(r, href=f"/b/{r['id']}", on=r["id"] == here) for r in trainer_batches(staff)],
            "team": path.rstrip("/") == "/team", "home": path == "/"}


def build_nav(staff, path):
    """The supervisor's side menu: [(section, [(label, href, icon, is_current)])]. The longest matching link
    is marked. Trainers have no side menu; they get the batch bar instead."""
    sections = [(s, list(items)) for s, items in MONITOR_NAV]
    if area_of(path) == "settings":                              # in Settings no monitoring page is marked
        return [(s, [(label, href, ico, False) for label, href, ico in items]) for s, items in sections]
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
from .pages import admin_misc, admin_org, admin_people, auth_pages, home, live, mcq, monitor, plans, runbooks, teach  # noqa: E402,F401

# A batch's classroom tools (Live Data Simulator, MCQ tests) for the admin live under Settings > Batches > batch;
# the same pages under /b/<batch>/ are the monitoring (read-only) side for the admin and the trainer's own pages.
_BATCH_PREFIX = "/b/(?P<bid>b-[a-z0-9]{4,12})/"
for _rt in list(ROUTES):
    if _rt.pattern.startswith((_BATCH_PREFIX + "live", _BATCH_PREFIX + "mcq")):
        ROUTES.append(Route(_rt.method, "/admin/batches/(?P<bid>b-[a-z0-9]{4,12})/" + _rt.pattern[len(_BATCH_PREFIX):],
                            _rt.fn, "super", _rt.name, _rt.audit))


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
        if req.method == "POST" and ctx.staff and ctx.staff.is_super and re.match(r"^/b/[^/]+/(live|mcq)(/|$)", req.path):
            # The admin's batch pages are monitoring: they only show. Changes are made in Settings > Batches.
            # (Marking, notes and plans already refuse the admin with their own message.)
            raise Forbidden("Batch pages only show progress. Changes are made in Settings.",
                            "Open Settings from your profile menu, then Batches.")
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

