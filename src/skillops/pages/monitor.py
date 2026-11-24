"""Monitoring pages: what the admin's side menu opens. Every page here only shows; nothing on them changes
anything. They carry no link to Settings: changes are made there, from the profile menu."""
from .. import dashboard
from ..core import org, perms
from ..web import route


def _pct(done, total):
    return round(100 * done / total) if total else 0


@route("GET", "/monitor/batches", access="super")
def batches(ctx):
    """Old address: the batches are on the Dashboard now."""
    return ctx.redirect("/dashboard")


@route("GET", "/monitor/courses", access="super")
def courses(ctx):
    """Courses with their projects in teaching order and the runbook each project uses."""
    from ..runbook import service
    rb_names = {r["id"]: r["name"] for r in service.list_runbooks()}
    insts = {i["id"]: i for i in org.list_institutions(include_archived=True)}
    items = org.list_courses()
    for c in items:
        c["projects"] = org.course_projects(c["id"])
        c["programmes"] = org.programmes_using_course(c["id"])
        for p in c["programmes"]:
            p["institution"] = insts.get(p["inst_id"], {})
    return ctx.render("monitor_courses.html", title="Courses", courses=items, rb_names=rb_names)


@route("GET", "/monitor/runbooks", access="super")
def runbooks(ctx):
    """The runbook library: what is published and where it is used. Reading a version opens the viewer."""
    from ..runbook import service
    from .runbooks import _projects_using
    items = service.list_runbooks()
    for r in items:
        vs = service.list_versions(r["id"])
        r["used_by"] = _projects_using(r["id"])
        r["latest_v"] = next((v for v in vs if v["id"] == r.get("latest")), None)
        r["published"] = [v for v in vs if v["status"] == "published"]
    return ctx.render("monitor_runbooks.html", title="Runbooks", runbooks=items)


@route("GET", "/monitor/live", access="super")
def live(ctx):
    """The Live Data Simulator's feeds: running or paused, the data clock and each dataset's rows."""
    from ..livedata import imports, ops
    try:
        feeds, error = ops.feeds(), None
    except Exception:  # noqa: BLE001
        feeds, error = [], "The Live Data Simulator is not answering. Everything else works."
    try:
        recent = imports.recent()
    except Exception:  # noqa: BLE001
        recent = []
    return ctx.render("monitor_live.html", title="Live Data Simulator", feeds=feeds, error=error, imports=recent)


@route("GET", "/monitor/mcq", access="super")
def mcq(ctx):
    """Which institutions have the MCQ test system connected, and each batch's MCQ name."""
    from ..mcq import client
    rows = []
    for inst in org.list_institutions():
        try:
            s = client.get_settings(inst["id"])
        except Exception:  # noqa: BLE001
            s = {}
        batches = [b for p in org.list_programmes(inst["id"]) for b in org.list_batches(p["id"])]
        rows.append({"inst": inst, "settings": s, "batches": batches})
    return ctx.render("monitor_mcq.html", title="MCQ tests", rows=rows)


# The audit log only shows, so it lives on the monitoring side (its old address keeps working).
from .admin_misc import audit_page  # noqa: E402
route("GET", "/monitor/audit", access="super", name="audit_page_monitor")(audit_page)
