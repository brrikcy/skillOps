"""Home pages: the progress dashboard (supervisor's home; trainers see their own batches), the
trainer's batch cards, and one batch's overview page."""
from .. import dashboard
from ..core import config, org, perms, store
from ..core.http import Response, csv_download
from ..core.util import NotFound
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"


def _pct(done, total):
    return round(100 * done / total) if total else 0


@route("GET", "/")
def home(ctx):
    if ctx.staff.is_super:
        return ctx.redirect("/dashboard")
    tree = perms.visible_scope(ctx.staff)
    batches = [b for i in tree for p in i["programmes"] for b in p["batches"]]
    if len(batches) == 1:
        return ctx.redirect(f"/b/{batches[0]['id']}")
    rows = dashboard.batch_rows(tree)
    return ctx.render("home_trainer.html", title="My batches", rows=rows, count=len(batches))


@route("GET", "/dashboard")
def dashboard_page(ctx):
    tree = perms.visible_scope(ctx.staff)
    rows = dashboard.batch_rows(tree)
    insts = [i["institution"] for i in tree]
    progs = [p["programme"] for i in tree for p in i["programmes"]]
    qy = ctx.req.query
    # Default: the programme with the most recent tick ("most recently active"); "all" shows everything.
    if "programme" not in qy and "institution" not in qy:
        latest = max((r for r in rows if r["last"]), key=lambda r: r["last"]["at"], default=None)
        sel_prog = latest["programme"]["id"] if latest else "all"
        sel_inst = latest["institution"]["id"] if latest else "all"
    else:
        sel_prog, sel_inst = qy.get("programme", "all"), qy.get("institution", "all")
    shown = [r for r in rows if (sel_inst == "all" or r["institution"]["id"] == sel_inst) and
             (sel_prog == "all" or r["programme"]["id"] == sel_prog)]
    if qy.get("format") == "csv":
        return csv_download("skillops-dashboard.csv",
                            ["Institution", "Programme", "Batch", "Trainers", "Current project", "Position",
                             "Project items done", "Project items total", "Project completion %",
                             "Projects finished", "Projects in programme", "Last marked at (UTC)", "Last marked by",
                             "Live data registered", "Live data fetched", "MCQ attempts", "MCQ latest average %"],
                            [_csv_row(r) for r in shown])
    totals = {"batches": len(shown), "items_done": sum((r["current"]["s"] or {}).get("done", 0) for r in shown if r["current"]),
              "trainers": len({t["email"] for r in shown for t in r["trainers"]}),
              "live": sum((r["live"] or {}).get("registered", 0) for r in shown)}
    hl = dashboard.highlights(shown)
    for r in shown:
        r["revisit"] = sum(len(p["s"]["repeats"]) for p in r["projects"] if p["s"])
    return ctx.render("dashboard.html", title="Dashboard", rows=shown, insts=insts, progs=progs, sel_prog=sel_prog, hl=hl,
                      sel_inst=sel_inst, totals=totals, query=ctx.req.raw_query, pct=_pct,
                      empty_org=not org.list_institutions())


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


@route("GET", f"/dashboard/b/{BATCH}")
def dashboard_batch(ctx, bid):
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
    return ctx.render("dashboard_batch.html", title=f"{d['bc']['batch']['name']} · progress", d=d, pct=_pct)


@route("GET", f"/b/{BATCH}")
def batch_home(ctx, bid):
    perms.require_batch(ctx.staff, bid)
    d = dashboard.batch_detail(bid)
    ctx.set_batch_page(d["bc"])
    from .teach import _ist_today, recap_for
    today = recap_for(bid, _ist_today())
    repeats = [(p, n) for p in d["projects"] if p["s"] for n in p["s"]["repeats"]]
    others = []
    if not ctx.staff.is_super:
        tree = perms.visible_scope(ctx.staff)
        others = [b for i in tree for p in i["programmes"] for b in p["batches"] if b["id"] != bid]
    return ctx.render("batch_home.html", title=d["bc"]["batch"]["name"], d=d, today=today, repeats=repeats,
                      other_batches=others, tab="teach", pct=_pct)


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

