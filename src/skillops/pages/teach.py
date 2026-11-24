"""Class view: a whole section of the runbook on one screen, in newspaper columns, for teaching.

A section is a part of the runbook (for the Airtel runbook: a Part, with its actions). Its text flows
down one column, then into the next one to the right, filling the window; the trainer moves sideways
(arrow keys, Page Down, the mouse wheel) instead of scrolling a long page. Every action has its own
"Done" button, saved in place without reloading.

Also here: the class recap (what was covered on a day, ready to copy) and the batch's Tools hub.
"""
from concurrent.futures import ThreadPoolExecutor

from ..core import org, perms, util
from ..core.http import json_response
from ..core.util import NotFound, UserError
from ..runbook import progress, service
from ..web import route

BATCH = "(?P<bid>b-[a-z0-9]{4,12})"
PROJ = "(?P<jid>j-[a-z0-9]{4,12})"
NODE = "(?P<nid>n[0-9]{1,6})"


def _ctx(ctx, bid, jid):
    batch = perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    proj = org.get_project(batch["prog_id"], jid)
    ctx.set_batch_page(bc, proj)
    return bc, proj, progress.batch_version(batch, proj)


@route("GET", f"/b/{BATCH}/p/{PROJ}/teach")
def teach_start(ctx, bid, jid):
    """Open the class view where the class is: the section of the next item after the furthest tick."""
    bc, proj, vid = _ctx(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    from .runbooks import viewer_for_batch
    shown = viewer_for_batch(ctx, bid, jid)
    if shown:
        return shown
    summ = progress.summary(bid, vid)
    target = summ["next_up"] or (summ["pub"]["checkables"][0] if summ["pub"]["checkables"] else None)
    if not target:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    section = summ["pub"]["index"][target["parent"]] if target.get("parent") else target
    return ctx.redirect(f"/b/{bid}/p/{jid}/teach/{section['id']}#a-{target['id']}")


@route("GET", f"/b/{BATCH}/p/{PROJ}/teach/{NODE}")
def teach_section(ctx, bid, jid, nid):
    bc, proj, vid = _ctx(ctx, bid, jid)
    if not vid:
        return ctx.render("rb_none.html", title=proj["name"], bc=bc, proj=proj)
    from .runbooks import viewer_for_batch
    shown = viewer_for_batch(ctx, bid, jid, at=nid)
    if shown:
        return shown
    summ = progress.summary(bid, vid)
    pub = summ["pub"]
    sections = progress.sections(pub)
    ids = [s["id"] for s in sections]
    if nid not in ids:
        node = pub["index"].get(nid)
        if node and node.get("checkable") and node.get("parent") in ids:
            return ctx.redirect(f"/b/{bid}/p/{jid}/teach/{node['parent']}#a-{nid}")
        if node:   # a bigger part (a step): open its first section
            first = next((s for s in sections if progress._under(pub, s, nid)), None)
            if first:
                return ctx.redirect(f"/b/{bid}/p/{jid}/teach/{first['id']}")
        raise NotFound("This section is not in the runbook version this batch uses.",
                       "Open the project again to continue where the batch is.")
    k = ids.index(nid)
    section = sections[k]
    items = progress.section_items(pub, section)
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(lambda n: service.item_html(vid, n["id"]),
                               ([section] if not section.get("checkable") else []) + items))
    intro = bodies.pop(0) if not section.get("checkable") else ""
    path = service.path_label(pub, section)
    done_here = sum(1 for n in items if n["id"] in summ["done_ids"])
    # the sections menu, grouped by their top-level part
    groups = []
    for s in sections:
        top = s
        while top.get("parent"):
            top = pub["index"][top["parent"]]
        if not groups or groups[-1]["top"]["id"] != top["id"]:
            groups.append({"top": top, "sections": []})
        c = summ["per"].get(s["id"], [0, 0])
        groups[-1]["sections"].append({"node": s, "done": c[0], "total": c[1]})
    from .. import planner
    try:
        on_plan = planner.planned_on(bid, _ist_today(), jid)
    except Exception:  # noqa: BLE001  (the class view must open even if the plan cannot be read)
        on_plan = {}
    planned = {n["id"]: on_plan.get(n["id"]) or on_plan.get(section["id"]) for n in items
               if n["id"] in on_plan or section["id"] in on_plan}
    return ctx.render("teach.html", title=f"{section['title']} · {proj['name']}", bc=bc, proj=proj, pub=pub,
                      planned=planned,
                      summ=summ, section=section, intro=intro, items=list(zip(items, bodies)), path=path,
                      prev_s=sections[k - 1] if k else None, next_s=sections[k + 1] if k + 1 < len(sections) else None,
                      done_here=done_here, groups=groups, base=f"/b/{bid}/p/{jid}", number=k + 1,
                      count=len(sections), view_only=ctx.staff.is_super)


# ================================================================== class recap

def _ist_today():
    from datetime import datetime
    return datetime.fromtimestamp(util.now_ts(), util.IST).strftime("%Y-%m-%d")


def recap_for(bid, day):
    """[{project, items: [(node, tick)], next}] for the projects the batch worked on that day."""
    b = org.get_batch(bid)
    out = []
    for proj in org.list_projects(b["prog_id"]):
        vid = progress.batch_version(b, proj, pin=False) if proj.get("runbook_id") else None
        if not vid:
            continue
        try:
            pub = service.published(vid)
        except NotFound:
            continue
        ticks = progress.ticked_on(bid, vid, day)
        if not ticks:
            continue
        items = [(pub["index"][t["item_id"]], t) for t in ticks if t["item_id"] in pub["index"]]
        items.sort(key=lambda x: pub["order"][x[0]["id"]])
        summ = progress.summary(bid, vid)
        out.append({"project": proj, "covered": items, "next": summ["next_up"], "pub": pub,
                    "trainers": sorted({t.get("by_name") or t["by"] for _, t in items})})
    return out


def recap_text(bc, day, groups):
    from datetime import datetime
    d = datetime.strptime(day, "%Y-%m-%d")
    lines = [f"{bc['batch']['name']} · {bc['programme'].get('name', '')}", f"Class of {d.day} {d.strftime('%b %Y')}", ""]
    for g in groups:
        lines.append(f"{g['project']['name']}: today we covered")
        for n, _ in g["covered"]:
            lines.append(f"• {n['title']}")
        if g["next"]:
            lines.append(f"Next class starts with: {g['next']['title']}")
        lines.append("")
    lines.append("Sent from SkillOps")
    return "\n".join(lines)


@route("GET", f"/b/{BATCH}/recap")
def recap(ctx, bid):
    perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    day = (ctx.req.query.get("date") or _ist_today())[:10]
    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        raise UserError("Please choose a date.")
    groups = recap_for(bid, day)
    return ctx.render("recap.html", title=f"Class recap · {bc['batch']['name']}", bc=bc, day=day, groups=groups,
                      text=recap_text(bc, day, groups) if groups else "", tab="teach")


# ================================================================== tools hub

@route("GET", f"/b/{BATCH}/tools")
def tools(ctx, bid):
    perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    from ..livedata import ops
    from ..mcq import client
    try:
        live = ops.summary(bid)
    except Exception:  # noqa: BLE001
        live = None
    mcq, mcq_problem = None, None
    iid, name = bc["batch"]["inst_id"], (bc["batch"].get("mcq_batch") or "").strip()
    if not client.is_set_up(iid):
        mcq_problem = "Not connected for this institution yet."
    elif not name:
        mcq_problem = "This batch has no MCQ batch name yet."
    else:
        try:
            mcq = client.call_cached(iid, "summary", {"batches": [name]}, seconds=60)
        except (client.McqUnavailable, client.McqError) as e:
            mcq_problem = f"The test system is not answering ({e})."
    return ctx.render("tools.html", title=f"Tools · {bc['batch']['name']}", bc=bc, live=live, mcq=mcq,
                      mcq_problem=mcq_problem, base_url=ctx.req.base_url(), tab="tools")


# ================================================================== teaching from the day's plan

@route("GET", f"/b/{BATCH}/teach-today")
def teach_today(ctx, bid):
    """"Teach": today's planned session. Without a plan for today, the planner opens on today."""
    from .. import planner
    from .plans import plan_project
    batch = perms.require_batch(ctx.staff, bid)
    today = _ist_today()
    if planner.get(bid, today)["entries"]:
        return ctx.redirect(f"/b/{bid}/session?date={today}")
    jid = plan_project(bid, batch)
    if not jid:
        return ctx.redirect(f"/b/{bid}", flash="This batch has no runbook yet.", kind="warn")
    if ctx.staff.is_super:
        return ctx.redirect(f"/b/{bid}/calendar", flash="There is no session plan for today.", kind="warn")
    return ctx.redirect(f"/b/{bid}/p/{jid}/plan?date={today}",
                        flash="There is no plan for today yet. Lay out today's session here, then press Teach.",
                        kind="warn")


@route("GET", f"/b/{BATCH}/session")
def teach_session(ctx, bid):
    """The day's plan in the class view: only the planned parts, in the planned order, each with Mark done."""
    from .. import planner
    batch = perms.require_batch(ctx.staff, bid)
    bc = org.batch_context(bid)
    ctx.set_batch_page(bc)
    day = planner.check_date(ctx.req.query.get("date") or _ist_today())
    plan = planner.get(bid, day)
    cache, blocks, missing = {}, [], 0
    for k, e in enumerate(plan["entries"], start=1):
        if e.get("kind") == "text":
            blocks.append({"kind": "text", "text": e["text"], "n": k})
            continue
        pid = e.get("proj")
        if pid not in cache:
            try:
                proj = org.get_project(batch["prog_id"], pid)
                vid = progress.batch_version(batch, proj)
                cache[pid] = (proj, vid, progress.summary(bid, vid)) if vid else None
            except Exception:  # noqa: BLE001  (a project removed since the plan was made is skipped)
                cache[pid] = None
        if not cache[pid]:
            missing += 1
            continue
        proj, vid, summ = cache[pid]
        node = summ["pub"]["index"].get(e["node"])
        if not node:
            missing += 1
            continue
        nodes = [node] if node.get("checkable") else progress.section_items(summ["pub"], node)
        blocks.append({"kind": "head", "title": node["title"], "path": service.path_label(summ["pub"], node),
                       "project": proj["name"], "n": k, "section": not node.get("checkable")})
        for n in nodes:
            blocks.append({"kind": "item", "node": n, "base": f"/b/{bid}/p/{pid}", "vid": vid,
                           "done": n["id"] in summ["done_ids"],
                           "tick": summ["ticks"].get(n["id"])})
    items = [b for b in blocks if b["kind"] == "item"]
    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(lambda b: service.item_html(b["vid"], b["node"]["id"]), items))
    for b, body in zip(items, bodies):
        b["html"] = body
    from .plans import plan_project
    return ctx.render("teach_session.html", title=f"Session · {bc['batch']['name']}", bc=bc, day=day, plan=plan,
                      blocks=blocks, missing=missing, done_here=sum(1 for b in items if b["done"]), total=len(items),
                      view_only=ctx.staff.is_super, jid=plan_project(bid, batch), today=_ist_today())
