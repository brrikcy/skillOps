"""Home pages: the progress dashboard (the supervisor's home), team progress, and one batch's page
(where a trainer lands)."""
from .. import dashboard
from ..core import config, org, perms, store
from ..core.http import Response, csv_download
from ..core import util
from ..core.util import NotFound
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"


def _pct(done, total):
    return round(100 * done / total) if total else 0


def _today_of(batch, plan_project, today):
    """Today for one batch: the planned session, whether it is a class day, and the next class day."""
    from .. import classdays, planner
    try:
        session = planner.get(batch["id"], today)
    except Exception:  # noqa: BLE001  (the page shows even if a plan cannot be read)
        session = {"entries": []}
    cfg = classdays.get(batch)
    is_set = classdays.is_set(cfg)
    nxt = classdays.next_class_days(cfg, today, 2) if is_set else []
    return {"session": session, "planned": len(session.get("entries") or []),
            "class_today": (today in nxt) if is_set else None,
            "next_day": next((d for d in nxt if d != today), None), "plan_project": plan_project}


@route("GET", "/")
def home(ctx):
    """The supervisor's home is the dashboard. A trainer with several batches lands on Overview: every batch of theirs at a glance
    (the same segmented bars as the supervisor's dashboard) with today's session and a button to teach or plan it.
    The batch bar at the top opens each batch."""
    if ctx.staff.is_super:
        return ctx.redirect("/dashboard")
    from ..web import trainer_batches
    mine = trainer_batches(ctx.staff)
    if len(mine) == 1:                      # one batch: no overview, the batch is the home
        return ctx.redirect(f"/b/{mine[0]['id']}")
    rows = dashboard.batch_rows(perms.visible_scope(ctx.staff), extras=False)
    if not rows:
        return ctx.render("home_trainer.html", title="No batches yet")
    from .teach import _ist_today
    today = _ist_today()
    cards = dashboard.programme_cards(rows)
    current = {r["batch"]["id"]: r["current"] for r in rows}
    for c in cards:
        for b in c["batches"]:
            cur = current.get(b["batch"]["id"]) or {}
            b["today"] = _today_of(b["batch"], (cur.get("project") or {}).get("id") if cur.get("s") else None, today)
    return ctx.render("home_trainer.html", title="Overview", cards=cards, today=today)


@route("GET", "/dashboard")
def dashboard_page(ctx):
    """Headline numbers, then one card per programme: each batch is one bar split into its projects (at most
    three shown, see dashboard.project_window), with the batch's overall progress at the end."""
    tree = perms.visible_scope(ctx.staff)
    qy = ctx.req.query
    if qy.get("format") == "csv":
        # the download also carries the simulator and test figures, so it asks those systems too
        rows = dashboard.batch_rows(tree)
        return csv_download("skillops-dashboard.csv",
                            ["Institution", "Programme", "Batch", "Trainers", "Current project", "Position",
                             "Project items done", "Project items total", "Project completion %",
                             "Projects finished", "Projects in programme", "Last marked at (UTC)", "Last marked by",
                             "Simulator registered", "Simulator fetched", "MCQ attempts", "MCQ latest average %"],
                            [_csv_row(r) for r in rows])
    if not ctx.staff.is_super:
        return ctx.redirect("/")                                # a trainer's dashboard is their home page
    rows = dashboard.batch_rows(tree, extras=False)
    cards = dashboard.programme_cards(rows)
    return ctx.render("dashboard.html", title="Dashboard", cards=cards, empty_org=not org.list_institutions())


@route("GET", "/settings", access="super")
def settings_page(ctx):
    """Settings (opened from the profile menu): every place where the admin changes something. The side menu
    only monitors; this is the one way in to set-up, people, content, tools and data."""
    from ..web import SETTINGS_DESC, SETTINGS_NAV
    groups = [(g, [(label, href, ico, SETTINGS_DESC.get(href, "")) for label, href, ico in items])
              for g, items in SETTINGS_NAV]
    return ctx.render("settings.html", title="Settings", groups=groups)


def _csv_row(r):
    cur = r["current"]
    s = (cur or {}).get("s") or {}
    last = r["last"] or {}
    return [r["institution"]["name"], r["programme"]["name"], r["batch"]["name"],
            ", ".join(t["name"] for t in r["trainers"]), cur["project"]["name"] if cur else "", s.get("position", ""),
            s.get("done", ""), s.get("total", ""), _pct(s.get("done", 0), s.get("total", 0)) if s else "",
            r["finished"], r["project_total"], last.get("at", ""), last.get("by_name", ""),
            (r["live"] or {}).get("registered", "not available") if r["live_known"] else "not available",
            (r["live"] or {}).get("fetched", "not available") if r["live_known"] else "not available",
            sum(t["written"] for t in (r["mcq"] or {}).get("tests", [])) if r["mcq_known"] else "not available",
            ((r["mcq"] or {}).get("latest") or {}).get("average", "") if r["mcq_known"] else "not available"]


@route("GET", "/team")
def team_page(ctx):
    """Every batch in the trainer's programmes, side by side, with the trainer's own marked."""
    scope = "all" if ctx.req.query.get("scope") == "all" else "current"
    return ctx.render("team.html", title="Team progress", t=dashboard.team(ctx.staff), scope=scope, pct=_pct)


@route("GET", f"/dashboard/b/{BATCH}")
def dashboard_batch(ctx, bid):
    """Old address of a batch's progress: the Progress tab is now the batch page itself."""
    perms.require_batch(ctx.staff, bid)
    return ctx.redirect(f"/b/{bid}" + ("?format=csv" if ctx.req.query.get("format") == "csv" else ""))


@route("GET", f"/b/{BATCH}")
def batch_progress(ctx, bid):
    """A batch opens on Progress: the whole picture on one screen, details folded away."""
    perms.require_batch(ctx.staff, bid)
    d = dashboard.batch_detail(bid)
    ctx.set_batch_page(d["bc"])
    if ctx.req.query.get("format") == "csv":
        rows = []
        for p in d["projects"]:
            s = p["s"]
            rows.append([p["project"]["name"], s["done"] if s else "", s["total"] if s else "",
                         _pct(s["done"], s["total"]) if s else "", s["position"] if s else "no runbook"])
        return csv_download(f"skillops-{d['bc']['batch']['name']}.csv",
                            ["Project", "Items done", "Items total", "Completion %", "Position"], rows)
    if ctx.staff.is_super and ctx.req.query.get("view") != "detail":
        # The supervisor opens a batch on its projects and how far each has gone (the detailed view is one click away).
        return ctx.render("batch_projects.html", title=d["bc"]["batch"]["name"], d=d, tab="progress", pct=_pct)
    # Two views of the same batch: the current project, or all its projects.
    scope = "all" if ctx.req.query.get("scope") == "all" else "current"
    cur = d["current"]
    curid, curvid = (cur["project"]["id"], cur["vid"]) if cur else (None, None)
    v = dashboard.progress_view(d, only_vid=curvid if scope == "current" else None)
    if scope == "current":
        f = {"events": [e for e in d["events"] if e.get("version_id") == curvid],
             "notes": [n for n in d["notes"] if n["project"]["id"] == curid]}
    else:
        f = {"events": d["events"], "notes": d["notes"]}
    keep = "view=detail&" if ctx.req.query.get("view") == "detail" else ""
    rb_names = {}
    if scope == "all":
        from ..runbook import service
        rb_names = {r["id"]: r["name"] for r in service.list_runbooks()}
    return ctx.render("batch_progress.html", title=d["bc"]["batch"]["name"], d=d, v=v, f=f, scope=scope, keep=keep, rb_names=rb_names,
                      bb=dashboard.batch_bar(d["projects"], scope, curid), tab="progress", pct=_pct)


@route("POST", f"/admin/batches/{BATCH}/projects/(?P<jid>j-[a-z0-9]{{4,12}})/complete", access="super",
       name="mark project complete")
def complete_project(ctx, bid, jid):
    """The supervisor closes a project for a batch: every item not yet done is marked done, in the supervisor's
    name (items already marked keep their trainer). Un-marking single items stays possible on the item pages."""
    from ..runbook import progress
    b = org.get_batch(bid)
    proj = org.get_project(b["prog_id"], jid)
    vid = progress.batch_version(b, proj)
    if not vid:
        # No runbook, so no items to tick: the supervisor's mark is kept as a record on the batch.
        store.t("CORE").put_item(Item={"pk": f"BATCH#{bid}", "sk": f"PROJDONE#{jid}", "type": "projdone", "batch_id": bid,
                                       "project_id": jid, "by": ctx.staff.email, "by_name": ctx.staff.name,
                                       "at": util.now_iso()})
        ctx.audit_batch, ctx.audit_target, ctx.audit_detail = bid, proj["name"], "closed by hand (no runbook)"
        return ctx.redirect(f"/admin/batches/{bid}", flash=f"{proj['name']} marked complete.")
    s = progress.summary(bid, vid)
    made = 0
    for node in s["pub"]["checkables"]:
        if node["id"] not in s["done_ids"]:
            made += 1 if progress.tick(bid, vid, node["id"], ctx.staff, jid)[1] else 0
    ctx.audit_batch, ctx.audit_target = bid, proj["name"]
    ctx.audit_detail = f"{made} items marked done"
    return ctx.redirect(f"/admin/batches/{bid}", flash=f"{proj['name']} marked complete ({made} item{'' if made == 1 else 's'} marked done)."
                        if made else f"{proj['name']} was already complete.")


@route("GET", f"/b/{BATCH}/today")
def batch_today(ctx, bid):
    """Today's session for the batch, the coming class days and what was covered today."""
    perms.require_batch(ctx.staff, bid)
    d = dashboard.batch_detail(bid)
    ctx.set_batch_page(d["bc"])
    from .. import classdays, planner
    from .plans import calendar_view
    from .teach import _ist_today, recap_for
    today_str = _ist_today()
    try:
        cal = calendar_view(bid, d["bc"]["batch"], ctx.req.query.get("m"))
    except Exception:  # noqa: BLE001  (the page must show even if the calendar cannot be read)
        cal = None
    try:
        session = planner.get(bid, today_str)
    except Exception:  # noqa: BLE001
        session = None
    cfg = classdays.get(d["bc"]["batch"])
    class_today = classdays.is_class_day(cfg, today_str) if classdays.is_set(cfg) else None
    return ctx.render("batch_home.html", title=f"{d['bc']['batch']['name']} · today", d=d,
                      today=recap_for(bid, today_str), today_str=today_str, class_today=class_today,
                      tab="today", pct=_pct, cal=cal, session=session)


@route("POST", f"/admin/batches/{BATCH}/current-project", access="super", name="choose current project")
def choose_current_project(ctx, bid):
    from ..core import org as _org
    from ..core import store as _store
    b = _org.get_batch(bid)
    jid = ctx.form().get("project", "")
    if jid:
        _org.get_project(b["prog_id"], jid)
    ctx.audit_batch = bid
    ctx.audit_target = jid or "automatic"
    _store.update("CORE", f"BATCH#{bid}", "META", {"current_project": jid})
    return ctx.redirect(f"/admin/batches/{bid}", flash="Current project saved." if jid else
                        "The current project now follows the latest tick.")


@route("POST", f"/admin/batches/{BATCH}/projects/(?P<jid>j-[a-z0-9]{{4,12}})/reopen", access="super",
       name="reopen project")
def reopen_project(ctx, bid, jid):
    """Takes back a hand-made "complete" on a project with no runbook. (Projects with a runbook are reopened by
    un-marking items.)"""
    b = org.get_batch(bid)
    proj = org.get_project(b["prog_id"], jid)
    store.t("CORE").delete_item(Key={"pk": f"BATCH#{bid}", "sk": f"PROJDONE#{jid}"})
    ctx.audit_batch, ctx.audit_target = bid, proj["name"]
    return ctx.redirect(f"/admin/batches/{bid}", flash=f"{proj['name']} is open again.")


@route("GET", "/media/(?P<key>[A-Za-z0-9_\\-/\\.]{1,200})", access="public")
def media(ctx, key):
    """In AWS, CloudFront serves /media/* straight from S3. This route is the laptop's stand-in."""
    if ".." in key:
        raise NotFound("Not found.")
    try:
        obj = store.s3().get_object(Bucket=config.runbook_bucket(), Key="media/" + key)
    except Exception:  # noqa: BLE001
        raise NotFound("This file does not exist.")
    return Response(200, obj["Body"].read(), {"content-type": obj.get("ContentType", "application/octet-stream"),
                                              "cache-control": "public, max-age=86400"}, binary=True)



@route("GET", "/help")
def help_page(ctx):
    """Short, current help for signed-in staff (trainers see theirs; the supervisor sees both)."""
    return ctx.render("help.html", title="Help", is_super=bool(ctx.staff.is_super))
