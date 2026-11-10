"""SkillOps Live Data pages: the batch's join codes, students and fetch activity (trainers of the
batch and the supervisor), feeds and clock (supervisor; trainers read only), and the public
student pages (register, Developer docs, status)."""
import json
import os

from ..core import config, html, idem, perms
from ..core.http import Response
from ..core.util import UserError
from ..livedata import ops, students
from ..livedata import feedops
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"


def _batch(ctx, bid):
    perms.require_batch(ctx.staff, bid)
    from ..core import org
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    ctx.audit_batch = bid
    return bc


@route("GET", f"/b/{BATCH}/live")
def batch_live(ctx, bid):
    bc = _batch(ctx, bid)
    try:
        rows = ops.student_rows(bid)
        codes = ops.batch_codes(bid)
        acts = ops.activity(bid)
        feeds = ops.feeds()
        error = None
    except Exception:  # noqa: BLE001  (the rest of the page must still show)
        import traceback
        traceback.print_exc()
        rows, codes, acts, feeds, error = [], [], [], [], "SkillOps Live Data is not answering. Everything else works."
    return ctx.render("live_batch.html", title=f"Live data · {bc['batch']['name']}", bc=bc, rows=rows, codes=codes,
                      acts=acts, feeds=feeds, error=error, base_url=ctx.req.base_url(), tab="live",
                      new_key=None)


@route("POST", f"/b/{BATCH}/live/codes", name="create join code")
def code_create(ctx, bid):
    _batch(ctx, bid)
    f = ctx.form()

    def work():
        try:
            c = ops.create_code(bid, f.get("max_uses", 40), f.get("open_minutes", 30), f.get("approval") == "1",
                                ctx.staff.email)
        except ops.LiveError as e:
            raise UserError(str(e))
        return {"redirect": f"/b/{bid}/live", "flash": f"Join code {c['code']} is open. Show it to the class."}
    result = idem.run_once(f.get("idem"), ctx.staff.email, work)
    ctx.audit_target = result.get("flash", "")[10:24]
    return ctx.redirect(result["redirect"], flash=result.get("flash"))


@route("POST", f"/b/{BATCH}/live/codes/close", name="close join code")
def code_close(ctx, bid):
    _batch(ctx, bid)
    code = ctx.form().get("code", "")
    ctx.audit_target = code
    try:
        ops.close_code(bid, code)
    except ops.LiveError as e:
        raise UserError(str(e))
    return ctx.redirect(f"/b/{bid}/live", flash=f"{code} is closed. Nobody new can register with it.")


@route("POST", f"/b/{BATCH}/live/students", name="change live-data student")
def student_action(ctx, bid):
    bc = _batch(ctx, bid)
    f = ctx.form()
    ctx.audit_target = f"{f.get('student_id')} {f.get('action')}"
    try:
        if f.get("action") == "approve_all":
            n = ops.approve_all(bid, ctx.staff.email)
            return ctx.redirect(f"/b/{bid}/live", flash=f"{n} student(s) approved.")
        result = ops.set_student_status(bid, f.get("student_id", ""), f.get("action", ""), ctx.staff.email)
    except ops.LiveError as e:
        raise UserError(str(e))
    if "api_key" in result:
        # The new key is shown once, on this page only; it is never stored in plain text.
        return ctx.render("live_batch.html", title=f"Live data · {bc['batch']['name']}", bc=bc,
                          rows=ops.student_rows(bid), codes=ops.batch_codes(bid), acts=ops.activity(bid),
                          feeds=ops.feeds(), error=None, base_url=ctx.req.base_url(), tab="live",
                          new_key={"name": result["name"], "key": result["api_key"]})
    return ctx.redirect(f"/b/{bid}/live", flash=f"{result['name']}: now {result['status']}.")


@route("GET", f"/b/{BATCH}/live/student")
def student_log(ctx, bid):
    bc = _batch(ctx, bid)
    sid = ctx.req.query.get("id", "")
    s = students.get_student(sid)
    if not s or s["batch"] != bid:
        raise UserError("That student is not in this batch.")
    return ctx.render("live_student.html", title=s["name"], bc=bc, s=s, log=students.student_log(sid), tab="live")


# ================================================================== feeds and clock

@route("GET", "/admin/live", access="super")
def feeds_page(ctx):
    try:
        feeds, error = ops.feeds(), None
    except Exception:  # noqa: BLE001
        feeds, error = [], "SkillOps Live Data is not answering. Everything else works."
    return ctx.render("live_feeds.html", title="Live data feeds", feeds=feeds, error=error,
                      base_url=ctx.req.base_url())


def _feed_action(ctx, fn):
    try:
        msgs = fn()
    except (ops.LiveError, feedops.FeedError, ValueError) as e:
        raise UserError(str(e))
    return ctx.redirect("/admin/live", flash=" ".join(msgs) or "Done.")


@route("POST", "/admin/live/pause", access="super", name="pause feed")
def feed_pause(ctx):
    ctx.audit_target = ctx.form().get("feed", "")
    return _feed_action(ctx, lambda: ops.pause(ctx.form().get("feed", "")))


@route("POST", "/admin/live/resume", access="super", name="resume feed")
def feed_resume(ctx):
    ctx.audit_target = ctx.form().get("feed", "")
    return _feed_action(ctx, lambda: ops.pause(ctx.form().get("feed", ""), resume=True))


@route("POST", "/admin/live/clock", access="super", name="set feed clock")
def feed_clock(ctx):
    f = ctx.form()
    ctx.audit_target = f.get("feed", "")
    ctx.audit_detail = f"go-live {f.get('go_live')} speed {f.get('speed')} force={bool(f.get('force'))}"
    return _feed_action(ctx, lambda: ops.set_clock(f.get("feed", ""), f.get("go_live", ""), f.get("speed", ""),
                                                   force=f.get("force") == "1", from_now=f.get("from_now") == "1"))


@route("POST", "/admin/live/dataset", access="super", name="activate dataset")
def dataset_active(ctx):
    f = ctx.form()
    ctx.audit_target = f"{f.get('dataset')} {f.get('active')}"
    return _feed_action(ctx, lambda: ops.set_dataset_active(f.get("dataset", ""), f.get("active") == "1"))


# ================================================================== public student pages

PUBLIC = os.path.join(html.SRC, "public")


def _public_page(name):
    with open(os.path.join(PUBLIC, name), encoding="utf-8") as fh:
        text = fh.read()
    text = text.replace("{static_version}", html.static("x")[len("/static/x"):])
    h = {"content-type": "text/html; charset=utf-8", "cache-control": "no-cache"}
    from ..core.http import SECURITY_HEADERS
    h.update(SECURITY_HEADERS)
    return Response(200, text, h)


@route("GET", "/live", access="public")
def public_home(ctx):
    return _public_page("index.html")


@route("GET", "/register", access="public")
def public_register(ctx):
    return _public_page("register.html")


@route("GET", "/docs", access="public")
def public_docs(ctx):
    return _public_page("docs.html")


@route("GET", "/status", access="public")
def public_status(ctx):
    return _public_page("status.html")


@route("GET", "/public-config.js", access="public")
def public_config(ctx):
    cfg = {"platformName": config.live_data_name(), "apiUrl": ctx.req.base_url(),
           "practiceDataset": os.environ.get("PRACTICE_DATASET", "practice_orders"),
           "localMode": config.local_mode(), "static": html.static("x")[len("/static/x"):]}
    body = "window.SIMLAB_CONFIG = " + json.dumps(cfg) + ";\n"
    return Response(200, body, {"content-type": "application/javascript; charset=utf-8", "cache-control": "no-cache"})
