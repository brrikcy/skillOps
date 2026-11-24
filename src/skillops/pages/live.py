"""Live Data Simulator pages: the batch's join codes, students and fetch activity (trainers of the
batch and the supervisor), feeds and clock (supervisor; trainers read only), and the public
student pages (register, Developer docs, status)."""
import os

from ..core import config, idem, perms, store
from ..core.util import UserError
from ..livedata import ops, students
from ..livedata import feedops, imports
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"


def _home(ctx, bid):
    """Back to the batch's tool page on the side the change was made from: Settings > Batches for the admin."""
    return f"/admin/batches/{bid}" if ctx.area == "settings" else f"/b/{bid}"


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
        rows, codes, acts, feeds, error = [], [], [], [], "The Live Data Simulator is not answering. Everything else works."
    return ctx.render("live_batch.html", title=f"Live Data Simulator · {bc['batch']['name']}", bc=bc, rows=rows, codes=codes,
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
        return {"redirect": f"{_home(ctx, bid)}/live", "flash": f"Join code {c['code']} is open. Show it to the class."}
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
    return ctx.redirect(f"{_home(ctx, bid)}/live", flash=f"{code} is closed. Nobody new can register with it.")


@route("POST", f"/b/{BATCH}/live/students", name="change live-data student")
def student_action(ctx, bid):
    bc = _batch(ctx, bid)
    f = ctx.form()
    ctx.audit_target = f"{f.get('student_id')} {f.get('action')}"
    try:
        if f.get("action") == "approve_all":
            n = ops.approve_all(bid, ctx.staff.email)
            return ctx.redirect(f"{_home(ctx, bid)}/live", flash=f"{n} student(s) approved.")
        result = ops.set_student_status(bid, f.get("student_id", ""), f.get("action", ""), ctx.staff.email)
    except ops.LiveError as e:
        raise UserError(str(e))
    if "api_key" in result:
        # The new key is shown once, on this page only; it is never stored in plain text.
        return ctx.render("live_batch.html", title=f"Live Data Simulator · {bc['batch']['name']}", bc=bc,
                          rows=ops.student_rows(bid), codes=ops.batch_codes(bid), acts=ops.activity(bid),
                          feeds=ops.feeds(), error=None, base_url=ctx.req.base_url(), tab="live",
                          new_key={"name": result["name"], "key": result["api_key"]})
    return ctx.redirect(f"{_home(ctx, bid)}/live", flash=f"{result['name']}: now {result['status']}.")


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
        feeds, error = [], "The Live Data Simulator is not answering. Everything else works."
    return ctx.render("live_feeds.html", title="Live Data Simulator", feeds=feeds, error=error,
                      base_url=ctx.req.base_url(), upload=_import_form(ctx), imports=imports.recent())


# ================================================================== adding data from the page

def _import_form(ctx):
    """Where the browser sends the file: straight to S3 in AWS (large files never pass Lambda), or to us locally."""
    if config.local_mode():
        return {"action": "/admin/live/imports", "fields": {}, "direct": False}
    iid, prefix = imports.new_prefix()
    back = f"{ctx.req.base_url()}/admin/live/imports/received"
    post = store.s3().generate_presigned_post(
        Bucket=config.runbook_bucket(), Key=prefix + "${filename}", ExpiresIn=3600,
        Fields={"success_action_redirect": back},
        Conditions=[["starts-with", "$key", prefix], ["content-length-range", 1, imports.MAX_BYTES],
                    {"success_action_redirect": back}])
    return {"action": post["url"], "fields": post["fields"], "direct": True}


@route("POST", "/admin/live/imports", access="super", name="upload Live Data Simulator file")
def import_upload(ctx):
    f = ctx.req.files().get("file")
    if not f:
        raise UserError("Please choose a .db or .csv file.")
    ctx.audit_target = f["filename"]
    iid = imports.start(f["filename"], ctx.staff.email, data=f["data"])
    return ctx.redirect(f"/admin/live/imports/{iid}", flash="Uploaded. The file is being read; this page refreshes itself.")


@route("GET", "/admin/live/imports/received", access="super", name="upload Live Data Simulator file (S3)")
def import_received(ctx):
    key = ctx.req.query.get("key", "")
    ctx.audit_target = key
    iid = imports.start("", ctx.staff.email, key=key)
    return ctx.redirect(f"/admin/live/imports/{iid}", flash="Uploaded. The file is being read; this page refreshes itself.")


@route("GET", "/admin/live/imports/(?P<iid>imp-[a-z0-9]{8})", access="super")
def import_page(ctx, iid):
    from ..livedata import catalog
    item = imports.get(iid)
    return ctx.render("live_import.html", title=f"Add data · {item['filename']}", item=item,
                      feeds=catalog.list_feeds(), datasets={d["dataset"]: d for d in catalog.list_datasets()})


@route("POST", "/admin/live/imports/(?P<iid>imp-[a-z0-9]{8})", access="super", name="load Live Data Simulator data")
def import_load(ctx, iid):
    ctx.audit_target = iid
    p = imports.load(iid, ctx.form(), ctx.staff.email)
    ctx.audit_detail = f"feed {p['feed']}: " + ", ".join(e["dataset"] for e in p["entries"])
    return ctx.redirect(f"/admin/live/imports/{iid}", flash="Loading has started. This page shows how far it has got.")


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


@route("POST", "/admin/live/delete", access="super", name="delete Live Data Simulator data")
def live_delete(ctx):
    from ..core import jobs
    from ..livedata import catalog
    f = ctx.form()
    feed, dataset = (f.get("feed") or "").strip(), (f.get("dataset") or "").strip()
    if feed:
        if not catalog.get_feed(feed):
            raise UserError(f"There is no feed called {feed}.")
        if (f.get("confirm_name") or "").strip() != feed:
            raise UserError("Type the feed's name to confirm.", f'Type "{feed}" in the box, then press Delete.')
        params, what = {"feed": feed}, f"feed {feed} and its datasets"
    elif dataset and catalog.get_dataset(dataset):
        params, what = {"dataset": dataset}, f"dataset {dataset}"
    else:
        raise UserError("Please choose what to delete.")
    ctx.audit_target = what
    jobs.start("live_delete", ctx.staff.email, params, label=f"Delete {what}")
    return ctx.redirect("/admin/live", flash=f"Deleting {what}. Its rows are removed in the background; "
                                             "refresh in a minute.")


@route("POST", "/admin/live/dataset", access="super", name="activate dataset")
def dataset_active(ctx):
    f = ctx.form()
    ctx.audit_target = f"{f.get('dataset')} {f.get('active')}"
    return _feed_action(ctx, lambda: ops.set_dataset_active(f.get("dataset", ""), f.get("active") == "1"))


# ================================================================== public student pages

def _staff_signed_in(ctx):
    """Staff never use the students' Register page: they make join codes; students register on their own device."""
    from ..core import auth
    if auth.load_session(ctx.req):
        return ctx.redirect("/", flash="The Register page is for students. Show them the join code; they open "
                                       "the Register address on their own device.", kind="warn")
    return None


@route("GET", "/register", access="public")
def public_register(ctx):
    away = _staff_signed_in(ctx)
    if away:
        return away
    q = ctx.req.query
    form = {"join_code": (q.get("code") or "")[:20], "full_name": "", "roll_number": ""}
    return ctx.render("public_register.html", title="Register", form=form, page_name="register")


@route("POST", "/register", access="public", audit_it=False)
def public_register_post(ctx):
    """The form posts here; the same registration as POST /v1/register (rate limits included)."""
    from ..livedata import timeutil
    from ..livedata.http import ApiError
    away = _staff_signed_in(ctx)
    if away:
        return away
    f = ctx.form()
    form = {k: (f.get(k) or "")[:80] for k in ("join_code", "full_name", "roll_number")}
    try:
        done = students.register(form, ctx.req.source_ip, ctx.req.base_url(), timeutil.now_us())
    except ApiError as e:
        return ctx.render("public_register.html", title="Register", form=form, page_name="register",
                          error={"message": e.message, "hint": e.hint}, status=e.status)
    # The key is on this page only (never stored in plain text, never cached).
    return ctx.render("public_register.html", title="Registered", form=form, page_name="register", done=done,
                      status=201)


@route("GET", "/docs", access="public")
def public_docs(ctx):
    return ctx.render("public_docs.html", title="Developer docs", page_name="docs", api=ctx.req.base_url(),
                      practice=os.environ.get("PRACTICE_DATASET", "practice_orders"))


@route("GET", "/live", access="public")
def public_home(ctx):
    """The old landing page: its steps are on the Register page and in the Developer docs now."""
    return ctx.redirect("/register")


@route("GET", "/status", access="public")
def public_status(ctx):
    """The old status page: students check GET /v1/status (or /v1/health); staff see the feeds page."""
    return ctx.redirect("/docs#health")
