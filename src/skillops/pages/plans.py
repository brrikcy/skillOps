"""Class days and the calendar (the supervisor sets the days a batch meets; trainers see them and plan each one),
and the trainers' session plans."""
from .. import classdays
from ..core import org, perms
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"


# ================================================================== class days (supervisor)

@route("POST", f"/admin/batches/{BATCH}/classdays", access="super", name="set class days")
def classdays_save(ctx, bid):
    ctx.audit_target = ctx.audit_batch = bid
    cfg = classdays.save(bid, ctx.form(), ctx.req.form_list("wd"))
    ctx.audit_detail = classdays.describe(cfg)
    return ctx.redirect(f"/admin/batches/{bid}#class-days", flash=f"Class days saved: {classdays.describe(cfg)}.")


# ================================================================== the calendar (trainers and the supervisor)

def plan_project(bid, batch=None):
    """The project a day's plan opens on: the batch's current project (or the first with a runbook)."""
    from .. import dashboard
    b = batch or org.get_batch(bid)
    projs = [j for j in org.list_projects(b["prog_id"]) if j.get("runbook_id")]
    if not projs:
        return None
    chosen = b.get("current_project")
    if chosen and any(j["id"] == chosen for j in projs):
        return chosen
    cur = dashboard._current(dashboard._project_summaries(b, projs), b)
    return cur["project"]["id"] if cur else projs[0]["id"]


def calendar_view(bid, batch, month_key=None):
    """Everything the calendar needs: the month, which class days have a plan, the next class days."""
    import re as _re

    from .. import planner
    from .teach import _ist_today
    today = _ist_today()
    cfg = classdays.get(batch)
    key = month_key if month_key and _re.match(r"^20\d\d-(0[1-9]|1[0-2])$", month_key) else today[:7]
    planned = {d["date"]: d["count"] for d in planner.list_dates(bid, limit=400)}
    m = classdays.month(cfg, int(key[:4]), int(key[5:7]), planned, today)
    upcoming = [{"date": d, "planned": planned.get(d, 0)} for d in classdays.next_class_days(cfg, today, 5)]
    return {"cfg": cfg, "set": classdays.is_set(cfg), "text": classdays.describe(cfg), "month": m, "today": today,
            "upcoming": upcoming, "project": plan_project(bid, batch)}


@route("GET", f"/b/{BATCH}/calendar")
def calendar_page(ctx, bid):
    batch = perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    return ctx.render("calendar.html", title=f"Calendar · {bc['batch']['name']}", bc=bc,
                      cal=calendar_view(bid, batch, ctx.req.query.get("m")), tab="calendar")


# ================================================================== session planner (split screen)

PROJ = "(?P<jid>j-[a-z0-9]{4,12})"
NODE = "(?P<nid>n[0-9]{1,6})"


def _today():
    from .teach import _ist_today
    return _ist_today()


def _part(bid, jid, vid, pub, section, summ):
    """One section of the runbook for the left pane: its intro and every action, ready to drag."""
    from concurrent.futures import ThreadPoolExecutor

    from ..runbook import progress, service
    items = progress.section_items(pub, section)
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(lambda n: service.item_html(vid, n["id"]),
                               ([section] if not section.get("checkable") else []) + items))
    intro = bodies.pop(0) if not section.get("checkable") else ""
    sections = progress.sections(pub)
    ids = [s["id"] for s in sections]
    k = ids.index(section["id"])
    return {"section": section, "intro": intro, "acts": list(zip(items, bodies)), "number": k + 1,
            "count": len(sections), "path": service.path_label(pub, section),
            "prev_s": sections[k - 1] if k else None, "next_s": sections[k + 1] if k + 1 < len(sections) else None,
            "done_ids": summ["done_ids"], "jid": jid, "bid": bid}


def _planner_ctx(ctx, bid, jid):
    from ..runbook import progress
    batch = perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    proj = org.get_project(batch["prog_id"], jid)
    ctx.set_batch_page(bc, proj)
    return bc, proj, progress.batch_version(batch, proj, pin=False)


@route("GET", f"/b/{BATCH}/p/{PROJ}/plan")
def planner_page(ctx, bid, jid):
    return _reader(ctx, bid, jid, "plan")


@route("GET", f"/b/{BATCH}/p/{PROJ}/read")
def reader_page(ctx, bid, jid):
    """The runbook on its own, with every section one click away: its original PDF when there is one."""
    from .runbooks import viewer_for_batch
    shown = viewer_for_batch(ctx, bid, jid, at=ctx.req.query.get("part", ""))
    return shown or _reader(ctx, bid, jid, "read")


def _reader(ctx, bid, jid, mode):
    from .. import planner
    from ..runbook import progress, service
    bc, proj, vid = _planner_ctx(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    day = planner.check_date(ctx.req.query.get("date") or _today())
    summ = progress.summary(bid, vid)
    pub = summ["pub"]
    sections = progress.sections(pub)
    want = ctx.req.query.get("part", "")
    section = next((s for s in sections if s["id"] == want), None)
    if not section:
        nxt = summ["next_up"]
        section = (pub["index"][nxt["parent"]] if nxt and nxt.get("parent") else nxt) or (sections[0] if sections else None)
    if not section:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    groups = []
    for s in sections:
        top = s
        while top.get("parent"):
            top = pub["index"][top["parent"]]
        if not groups or groups[-1]["top"]["id"] != top["id"]:
            groups.append({"top": top, "sections": []})
        groups[-1]["sections"].append(s)
    subs = {s["id"]: [{"id": n["id"], "title": n["title"], "label": n.get("label") or "", "fill": n.get("fill") or "",
                       "done": n["id"] in summ["done_ids"]} for n in progress.section_items(pub, s)] for s in sections}
    secno = {s["id"]: k + 1 for k, s in enumerate(sections)}
    secpath = {s["id"]: service.path_label(pub, s) for s in sections}
    projects = [j for j in org.list_projects(bc["batch"]["prog_id"]) if j.get("runbook_id")]
    plan = planner.get(bid, day)
    plan["entries"] = planner.annotate(bid, plan["entries"])
    title = "Session planner" if mode == "plan" else f"{proj['name']} runbook"
    return ctx.render("plan.html", title=f"{title} · {bc['batch']['name']}", bc=bc, proj=proj, mode=mode,
                      projects=projects, groups=groups, subs=subs, secno=secno, secpath=secpath, part=_part(bid, jid, vid, pub, section, summ), plan=plan,
                      dates=planner.list_dates(bid), day=day, today=_today(), summ=summ,
                      view_only=ctx.staff.is_super or mode == "read")


@route("GET", f"/b/{BATCH}/p/{PROJ}/plan/part/{NODE}")
def planner_part(ctx, bid, jid, nid):
    """The left pane's next section, fetched by the page without reloading."""
    from ..core.util import NotFound
    from ..runbook import progress
    bc, proj, vid = _planner_ctx(ctx, bid, jid)
    if not vid:
        raise NotFound("This project has no runbook yet.")
    summ = progress.summary(bid, vid)
    section = next((s for s in progress.sections(summ["pub"]) if s["id"] == nid), None)
    if not section:
        raise NotFound("This section is not in the runbook version this batch uses.")
    from .. import planner
    day = planner.check_date(ctx.req.query.get("date") or _today())
    return ctx.render("_plan_part.html", title=section["title"], part=_part(bid, jid, vid, summ["pub"], section, summ),
                      view_only=ctx.staff.is_super or ctx.req.query.get("mode") == "read", day=day,
                      plan=planner.get(bid, day), mode=ctx.req.query.get("mode") or "plan")


@route("GET", f"/b/{BATCH}/plan/current")
def planner_current(ctx, bid):
    """The plan as it is saved now (for the page to settle a clash with someone else's save)."""
    from .. import planner
    from ..core.http import json_response
    perms.require_batch(ctx.staff, bid)
    day = planner.check_date(ctx.req.query.get("date") or _today())
    plan = planner.get(bid, day)
    plan["entries"] = planner.annotate(bid, plan["entries"])
    return json_response({"ok": True, **plan})


@route("GET", f"/b/{BATCH}/plan/pdf")
def planner_pdf(ctx, bid):
    """A date's saved plan as a PDF, to print or share."""
    from .. import planner
    from ..core.http import file_download
    perms.require_batch(ctx.staff, bid)
    day = planner.check_date(ctx.req.query.get("date") or _today())
    name, data = planner.to_pdf(bid, day, org.get_batch(bid)["name"])
    return file_download(name, data, "application/pdf")


@route("POST", f"/b/{BATCH}/plan", name="save session plan")
def planner_save(ctx, bid):
    from .. import planner
    from ..core.http import json_response
    perms.require_trainer(ctx.staff)
    perms.require_batch(ctx.staff, bid)
    f = ctx.form()
    day = planner.check_date(f.get("date"))
    ctx.audit_batch, ctx.audit_target = bid, day
    if "entries" in f:                                  # the page's drag-and-drop list, saved whole
        result = planner.save(bid, day, f.get("entries"), f.get("note", ""), f.get("rev") or 0, ctx.staff)
        ctx.audit_detail = f"{len(result['entries'])} entries"
        return json_response({"ok": True, **result})
    result = planner.change(bid, day, f, ctx.staff)     # one button, without JavaScript
    ctx.audit_detail = f.get("action", "")
    back = f.get("back") or ""
    return ctx.redirect(back if back.startswith(f"/b/{bid}/") else f"/b/{bid}/today", flash="Plan saved.")
