"""MCQ test pages. The tests live in each institution's Google Sheet (Apps Script); SkillOps shows
the batch's tests, opens and closes them, shows the live monitor (add time, end a test),
results and the marks register, and imports questions.
If the test system does not answer, these pages say so and every other page keeps working."""
import re

from ..core import org, perms, util
from ..core.http import csv_download
from ..core.util import UserError
from ..mcq import client
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"
INST = "(?P<iid>i-[a-z0-9]{4,12})"
TEST = "(?P<tid>[A-Za-z0-9_\\-]{1,30})"
UNAVAILABLE = "The test system is not answering. Everything else works."


def _batch(ctx, bid):
    perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    ctx.audit_batch = bid
    return bc


def _args(bc, **kw):
    mcq = (bc["batch"].get("mcq_batch") or "").strip()
    if not mcq:
        raise UserError("This batch has no MCQ batch name yet.",
                        "Your supervisor sets it on the batch's settings page (the Batch column in the Students sheet).")
    return dict(kw, batches=[mcq])


def _change_args(ctx, bc, **kw):
    """For a change to a test: a trainer is limited to their batch's own tests; the supervisor is not."""
    return dict(kw) if ctx.staff.is_super else _args(bc, **kw)


def _idem(ctx):
    return (ctx.form().get("idem") or "")[:60] or None


def _run(fn):
    """(result, problem) - never raises for an unavailable test system."""
    try:
        return fn(), None
    except client.McqUnavailable as e:
        return None, f"{UNAVAILABLE} ({e})"
    except client.McqError as e:
        return None, str(e)


# ================================================================== batch pages

@route("GET", f"/b/{BATCH}/mcq")
def batch_mcq(ctx, bid):
    bc = _batch(ctx, bid)
    iid = bc["batch"]["inst_id"]
    problem = None
    tests = links = summary = None
    if not client.is_set_up(iid):
        problem = "The MCQ test system is not connected for this institution yet." + \
            (" Connect it under MCQ tests." if ctx.staff.is_super else " Ask your supervisor.")
    elif not bc["batch"].get("mcq_batch"):
        problem = "This batch has no MCQ batch name yet. " + \
            ("Set it on the batch's settings page." if ctx.staff.is_super else "Ask your supervisor to set it.")
    else:
        tests, problem = _run(lambda: client.call(iid, "listTests", _args(bc)))
        if tests is not None:
            links, _ = _run(lambda: client.call(iid, "links"))
            if links and not ctx.staff.is_super:
                links = {"studentUrl": links.get("studentUrl", "")}      # the monitor link carries the trainer key
            summary, _ = _run(lambda: client.call(iid, "summary", _args(bc)))
    return ctx.render("mcq_batch.html", title=f"MCQ tests · {bc['batch']['name']}", bc=bc, tests=tests or [],
                      links=links or {}, summary=summary or {}, problem=problem, tab="mcq")


@route("POST", f"/b/{BATCH}/mcq/tests/{TEST}/active", name="open or close MCQ test")
def test_active(ctx, bid, tid):
    bc = _batch(ctx, bid)
    active = ctx.form().get("active") == "1"
    ctx.audit_target = f"{tid} {'open' if active else 'close'}"
    try:
        client.call(bc["batch"]["inst_id"], "setTestActive", _change_args(ctx, bc, testId=tid, active=active),
                    write=True, idem=_idem(ctx))
    except (client.McqUnavailable, client.McqError) as e:
        raise UserError(f"Not changed: {e}", "Try again in a minute.")
    return ctx.redirect(f"/b/{bid}/mcq", flash=f"Test {tid} is {'open' if active else 'closed'}.")


def _monitor_data(bc, tid):
    return _run(lambda: client.call_cached(bc["batch"]["inst_id"], "getMonitor", _args(bc, testId=tid), seconds=5))


@route("GET", f"/b/{BATCH}/mcq/monitor/{TEST}")
def monitor(ctx, bid, tid):
    bc = _batch(ctx, bid)
    data, problem = _monitor_data(bc, tid)
    return ctx.render("mcq_monitor.html", title=f"Monitor {tid}", bc=bc, tid=tid, data=data or {}, problem=problem,
                      tab="mcq")


@route("GET", f"/b/{BATCH}/mcq/monitor/{TEST}/panel")
def monitor_panel(ctx, bid, tid):
    bc = _batch(ctx, bid)
    data, problem = _monitor_data(bc, tid)
    return ctx.render("mcq_monitor_panel.html", title="", bc=bc, tid=tid, data=data or {}, problem=problem)


@route("POST", f"/b/{BATCH}/mcq/monitor/{TEST}/session", name="MCQ add time or end test")
def monitor_action(ctx, bid, tid):
    bc = _batch(ctx, bid)
    f = ctx.form()
    action = f.get("action")
    ctx.audit_target = f"{tid} {f.get('session')} {action}"
    args = _args(bc, sessionId=f.get("session", ""))
    try:
        if action == "add":
            args["minutes"] = max(1, min(60, int(f.get("minutes") or 5)))
            client.call(bc["batch"]["inst_id"], "addTime", args, write=True, idem=_idem(ctx))
            msg = f"{args['minutes']} minute(s) added."
        elif action == "end":
            client.call(bc["batch"]["inst_id"], "endTest", args, write=True, idem=_idem(ctx))
            msg = "The test was ended for this student."
        else:
            raise UserError("Unknown action.")
    except (client.McqUnavailable, client.McqError) as e:
        raise UserError(f"Not done: {e}", "Refresh the monitor and try again.")
    return ctx.redirect(f"/b/{bid}/mcq/monitor/{tid}", flash=msg)


@route("GET", f"/b/{BATCH}/mcq/results/{TEST}")
def results(ctx, bid, tid):
    bc = _batch(ctx, bid)
    data, problem = _run(lambda: client.call(bc["batch"]["inst_id"], "results", _args(bc, testId=tid)))
    rows = (data or {}).get("rows", [])
    if ctx.req.query.get("format") == "csv":
        if problem:
            raise UserError(problem)
        return csv_download(f"{tid}-{bc['batch']['name']}-results.csv",
                            ["Submitted", "Name", "Email", "Batch", "Score", "Max", "Percentage", "Correct", "Wrong",
                             "Unanswered", "Minutes", "Warnings", "Type"],
                            [[util.now_iso(r["submittedAt"] / 1000) if r.get("submittedAt") else "", r["name"], r["email"],
                              r.get("batch", ""), r["score"], r["max"], r["percentage"], r.get("correct", ""),
                              r.get("wrong", ""), r.get("unanswered", ""), r.get("minutes", ""), r.get("warnings", ""),
                              r.get("type", "")] for r in rows])
    return ctx.render("mcq_results.html", title=f"Results {tid}", bc=bc, tid=tid, rows=rows, problem=problem, tab="mcq")


@route("GET", f"/b/{BATCH}/mcq/register")
def register(ctx, bid):
    bc = _batch(ctx, bid)
    data, problem = _run(lambda: client.call(bc["batch"]["inst_id"], "marksRegister", _args(bc)))
    data = data or {"header": [], "rows": []}
    if ctx.req.query.get("format") == "csv":
        if problem:
            raise UserError(problem)
        return csv_download(f"marks-register-{bc['batch']['name']}.csv", data["header"], data["rows"])
    return ctx.render("mcq_register.html", title="Marks register", bc=bc, data=data, problem=problem, tab="mcq")


@route("POST", f"/b/{BATCH}/mcq/import", name="import MCQ questions")
def import_questions(ctx, bid):
    bc = _batch(ctx, bid)
    f = ctx.form()
    tid = (f.get("test_id") or "").strip().upper()
    if not re.match(r"^[A-Z0-9_\-]{1,30}$", tid):
        raise UserError("The test ID may use letters, numbers, - and _ (for example W03).")
    up = ctx.req.files().get("file")
    csv_text = up["data"].decode("utf-8-sig", "replace") if up else f.get("text", "")
    if not csv_text.strip():
        raise UserError("Please choose the CSV file of questions.")
    ctx.audit_target = f"{tid} {f.get('mode')}"
    try:
        r = client.call(bc["batch"]["inst_id"], "importQuestions",
                        _change_args(ctx, bc, testId=tid, csv=csv_text,
                                     mode="replace" if f.get("mode") == "replace" else "add"),
                        write=True, idem=_idem(ctx))
    except (client.McqUnavailable, client.McqError) as e:
        raise UserError(f"Not imported: {e}")
    if not r.get("ok", True):
        errs = "; ".join(r.get("errors", [])[:5])
        raise UserError(f"The file has {r.get('errorCount', 0)} problem(s): {errs}", "Correct the file and import again.")
    return ctx.redirect(f"/b/{bid}/mcq",
                        flash=f"Imported {r.get('added')} question(s) into {tid}. It now has {r.get('total')}. "
                              "New tests start closed: set Allowed Batches and open it when ready.")


@route("POST", f"/b/{BATCH}/mcq/reattempt", name="allow MCQ re-attempt")
def reattempt(ctx, bid):
    bc = _batch(ctx, bid)
    f = ctx.form()
    ctx.audit_target = f"{f.get('test_id')} {f.get('email')}"
    try:
        r = client.call(bc["batch"]["inst_id"], "allowReattempt",
                        _args(bc, testId=f.get("test_id", ""), email=f.get("email", "")), write=True, idem=_idem(ctx))
    except (client.McqUnavailable, client.McqError) as e:
        raise UserError(f"Not done: {e}")
    msg = ("Done. The student can start the test again; the earlier result is kept and marked." if r.get("allowed")
           else "No attempt was found for that email in that test.")
    return ctx.redirect(f"/b/{bid}/mcq", flash=msg, kind="ok" if r.get("allowed") else "warn")


# ================================================================== supervisor: connections

@route("GET", "/admin/mcq", access="super")
def connections(ctx):
    rows = []
    for inst in org.list_institutions():
        s = client.get_settings(inst["id"])
        batches = [b for p in org.list_programmes(inst["id"]) for b in org.list_batches(p["id"])]
        rows.append({"inst": inst, "settings": s, "batches": batches})
    return ctx.render("mcq_admin.html", title="MCQ tests", rows=rows)


@route("POST", f"/admin/mcq/{INST}", access="super", name="save MCQ connection")
def save_connection(ctx, iid):
    org.get_institution(iid)
    f = ctx.form()
    ctx.audit_target = iid
    client.save_settings(iid, f.get("url", ""), f.get("secret", ""))
    return ctx.redirect("/admin/mcq", flash="Saved. Now press Test connection.")


@route("POST", f"/admin/mcq/{INST}/test", access="super", name="test MCQ connection")
def test_connection(ctx, iid):
    org.get_institution(iid)
    ctx.audit_target = iid
    try:
        r = client.test_connection(iid)
    except (client.McqUnavailable, client.McqError) as e:
        return ctx.redirect("/admin/mcq", flash=f"Not connected: {e}", kind="bad")
    return ctx.redirect("/admin/mcq", flash=f"Connected to \"{r.get('sheet')}\" ({r.get('institute')}): {r.get('tests')} test(s).")


@route("POST", f"/admin/mcq/{INST}/regrade", access="super", name="re-mark MCQ test")
def regrade(ctx, iid):
    tid = ctx.form().get("test_id", "").strip()
    ctx.audit_target = f"{iid} {tid}"
    try:
        r = client.call(iid, "regradeTest", {"testId": tid}, write=True, idem=_idem(ctx))
    except (client.McqUnavailable, client.McqError) as e:
        raise UserError(f"Not done: {e}")
    return ctx.redirect("/admin/mcq", flash=r.get("message", "Done."), kind="ok" if r.get("done") else "warn")


# ================================================================== health

def health_checks():
    out = []
    for inst in org.list_institutions():
        if not client.is_set_up(inst["id"]):
            continue
        import time
        t0 = time.time()
        try:
            r = client.call(inst["id"], "ping", retries=0, fresh=True)
            out.append({"name": f"MCQ test system: {inst['name']}", "ok": True, "detail": f"Answers ({r.get('tests')} tests).",
                        "ms": int((time.time() - t0) * 1000)})
        except (client.McqUnavailable, client.McqError) as e:
            out.append({"name": f"MCQ test system: {inst['name']}", "ok": False, "detail": f"Not working: {e}",
                        "ms": int((time.time() - t0) * 1000)})
    return out


from ..core import health as _health  # noqa: E402
_health.CHECKS.append(("mcq", health_checks))
