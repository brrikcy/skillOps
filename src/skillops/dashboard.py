"""Progress dashboard figures. Every number is counted from the stored ticks (never estimated).
No schedule words: position and completion only.

batch_rows(batch_ids) -> one row per batch (level 1)
batch_detail(batch_id) -> everything for one batch (level 2)
MCQ and live-data columns are filled when those systems answer; otherwise "not available".
"""
import traceback

from .core import org, store
from .core.util import NotFound
from .runbook import progress, service


def manual_done(bid):
    """{project_id: record} for projects the supervisor closed by hand (the way to finish one without a runbook)."""
    return {i["sk"][9:]: i for i in store.by_pk("CORE", f"BATCH#{bid}", "PROJDONE#")}


def _project_summaries(batch, projects):
    out = []
    manual = manual_done(batch["id"])
    for j in projects:
        vid = progress.batch_version(batch, j, pin=False) if j.get("runbook_id") else None
        s = None
        if vid:
            try:
                s = progress.summary(batch["id"], vid)
            except NotFound:
                s = None
        out.append({"project": j, "vid": vid, "s": s, "manual": manual.get(j["id"])})
    return out


def is_finished(p):
    """A project is finished when every item is done, or the supervisor closed it by hand."""
    s = p["s"]
    return bool(s and s["total"] and s["done"] >= s["total"]) or bool(p.get("manual") and not s)


def _current(projs, batch=None):
    """The project the batch is in now: the one the supervisor chose for the batch; otherwise the one with the
    most recent tick; else the first not finished."""
    chosen = (batch or {}).get("current_project")
    if chosen:
        hit = next((p for p in projs if p["project"]["id"] == chosen), None)
        if hit:
            return hit
    ticked = [p for p in projs if p["s"] and p["s"]["last"]]
    if ticked:
        return max(ticked, key=lambda p: p["s"]["last"]["at"])
    for p in projs:
        if p["s"] and p["s"]["done"] < p["s"]["total"]:
            return p
    return projs[0] if projs else None


MCQ_BUDGET_SECONDS = 8      # the dashboard never waits longer than this for test systems in total


def _mcq_by_batch(batches):
    """{batch_id: summary or None}. One call per institution (5 s at most each, and 8 s in total);
    never raises. Institutions not reached in time show "not available"."""
    import time
    from .mcq import client
    started = time.time()
    out = {}
    by_inst = {}
    for b in batches:
        if b.get("mcq_batch"):
            by_inst.setdefault(b["inst_id"], []).append(b)
    for iid, bs in by_inst.items():
        if not client.is_set_up(iid) or time.time() - started > MCQ_BUDGET_SECONDS:
            continue
        try:
            names = sorted({b["mcq_batch"].strip().upper() for b in bs})
            res = client.call_cached(iid, "batchSummaries", {"batches": names}, seconds=120)
            for b in bs:
                out[b["id"]] = res.get(b["mcq_batch"].strip().upper())
        except Exception:  # noqa: BLE001  (the dashboard must load even when the test system does not)
            traceback.print_exc()
    return out


def _live_by_batch(batches):
    from .livedata import ops
    out = {}
    for b in batches:
        try:
            out[b["id"]] = ops.summary(b["id"])
        except Exception:  # noqa: BLE001
            traceback.print_exc()
    return out


def batch_rows(scope_tree, extras=True):
    """Rows for level 1, from org.scope(...) output. extras=False skips the test and simulator systems
    (the dashboard tabs that do not show them)."""
    batches = [(i, p, b) for i in scope_tree for p in i["programmes"] for b in p["batches"]]
    mcq = _mcq_by_batch([b for _, _, b in batches]) if extras else {}
    live = _live_by_batch([b for _, _, b in batches]) if extras else {}
    rows = []
    projects_cache = {}
    for inst, prog, b in batches:
        pid = prog["programme"]["id"]
        if pid not in projects_cache:
            projects_cache[pid] = org.list_projects(pid)
        projs = _project_summaries(b, projects_cache[pid])
        cur = _current(projs, b)
        finished = sum(1 for p in projs if is_finished(p))
        last = max((p["s"]["last"] for p in projs if p["s"] and p["s"]["last"]), key=lambda t: t["at"], default=None)
        rows.append({
            "institution": inst["institution"], "programme": prog["programme"], "batch": b,
            "trainers": org.batch_trainers(b["id"]), "current": cur, "projects": projs,
            "finished": finished, "project_total": len(projs), "last": last,
            "mcq": mcq.get(b["id"]), "mcq_known": b["id"] in mcq,
            "live": live.get(b["id"]), "live_known": b["id"] in live,
        })
    return rows


def batch_detail(batch_id):
    bc = org.batch_context(batch_id)
    b = bc["batch"]
    projs = _project_summaries(b, org.list_projects(b["prog_id"]))
    cur = _current(projs, b)
    notes = []
    for p in projs:
        if not p["s"]:
            continue
        pub = p["s"]["pub"]
        for item_id, n in progress.notes(b["id"], p["vid"]).items():
            if n.get("text") and item_id in pub["index"]:
                notes.append({"project": p["project"], "node": pub["index"][item_id], "note": n,
                              "path": service.path_label(pub, pub["index"][item_id])})
    events = progress.events(b["id"], 40)
    titles = {}
    for e in events:
        vid = e.get("version_id")
        try:
            pub = service.published(vid)
            n = pub["index"].get(e["item_id"])
            titles[e["sk"]] = service.path_label(pub, n) + " · " + n["title"] if n else e["item_id"]
        except NotFound:
            titles[e["sk"]] = e["item_id"]
    parts = []
    if cur and cur["s"]:
        pub = cur["s"]["pub"]
        for n in pub["nodes"]:
            if n.get("page") and n["level"] < pub["checkable"]:
                c = cur["s"]["per"].get(n["id"], [0, 0])
                parts.append({"node": n, "done": c[0], "total": c[1]})
    return {"bc": bc, "projects": projs, "current": cur, "notes": sorted(notes, key=lambda x: x["note"]["at"], reverse=True),
            "events": events, "event_titles": titles, "parts": parts,
            "trainers": org.batch_trainers(b["id"])}


def _avg(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values)) if values else None


SHOWN_PROJECTS = 3          # at most this many projects on a programme's bars
UNTOUCHED_AHEAD = 2         # and at most this many that no batch has started


def project_pct(p):
    """A batch's progress in one project, 0-100. No runbook or nothing marked counts as 0; a project the
    supervisor closed by hand counts as 100."""
    s = p["s"]
    if s and s["total"]:
        return round(100 * s["done"] / s["total"])
    return 100 if p.get("manual") else 0


def project_window(grid):
    """Which projects to show for a programme, from grid[batch][project] = pct. Returns (first, last), inclusive.

    1. Show up to the furthest project any batch has started, plus at most UNTOUCHED_AHEAD projects that no
       batch has started (nothing started yet: the first UNTOUCHED_AHEAD projects).
    2. Never more than SHOWN_PROJECTS: keep the latest ones of that range...
    3. ...unless a batch is still working on an earlier project: then the window starts at the earliest
       project not finished by every batch, and the untouched projects ahead are dropped first.
    (0, -1) when the programme has no projects."""
    n = len(grid[0]) if grid else 0
    if not n:
        return 0, -1
    furthest = max((j for j in range(n) if any(row[j] > 0 for row in grid)), default=-1)
    last = min(n - 1, furthest + UNTOUCHED_AHEAD)
    first = max(0, last - SHOWN_PROJECTS + 1)
    slowest = next((j for j in range(n) if not all(row[j] >= 100 for row in grid)), n)
    if slowest < first:
        first, last = slowest, min(n - 1, slowest + SHOWN_PROJECTS - 1)
    return first, last


def batch_bar(projs, scope=None, current_id=None):
    """One batch's segmented bar, from its project summaries (batch_detail()["projects"] or a batch row's):
    {shown: [{n, project, pct}], before, after, pcts, overall, finished, project_total}.
    scope "all": every project; scope "current": only the current project (overall is then that project's %);
    otherwise the usual window of at most three."""
    pcts = [project_pct(p) for p in projs]
    if scope == "all":
        first, last = 0, len(pcts) - 1
    elif scope == "current":
        at = next((j for j, p in enumerate(projs) if p["project"]["id"] == current_id), None)
        first, last = (at, at) if at is not None else (0, -1)
    else:
        first, last = project_window([pcts] if pcts else [])
    shown = [{"n": j + 1, "project": projs[j]["project"], "pct": pcts[j]} for j in range(first, last + 1)]
    overall = round(sum(pcts) / len(pcts)) if pcts else 0
    if scope == "current":
        overall = shown[0]["pct"] if shown else 0
    return {"shown": shown, "before": first if shown and scope not in ("all", "current") else 0,
            "after": len(pcts) - 1 - last if shown and scope not in ("all", "current") else 0,
            "pcts": pcts, "overall": overall,
            "finished": sum(1 for v in pcts if v >= 100), "project_total": len(pcts)}


def programme_cards(rows):
    """The admin dashboard: one card per programme, one segmented bar per batch.

    Each batch's overall progress is the plain average of its projects (every project counts the same).
    Each card: institution, programme, the projects shown, how many are folded away before and after, and per
    batch: the shown segments, overall, where it is now. The projects shown: when every batch is in the same
    project, the one before it, it, and the one after; otherwise see project_window."""
    cards = []
    for r in rows:
        if not cards or cards[-1]["programme"]["id"] != r["programme"]["id"]:
            cards.append({"institution": r["institution"], "programme": r["programme"],
                          "projects": [p["project"] for p in r["projects"]], "batches": []})
        pcts = [project_pct(p) for p in r["projects"]]
        cur = r["current"] or {}
        cs = cur.get("s") or {}
        cards[-1]["batches"].append({
            "batch": r["batch"], "trainers": r["trainers"], "pcts": pcts, "last": r["last"],
            "overall": round(sum(pcts) / len(pcts)) if pcts else 0,
            "finished": sum(1 for v in pcts if v >= 100), "project_total": len(pcts),
            "cur_id": (cur.get("project") or {}).get("id"),
            "now": (cur.get("project") or {}).get("name", ""), "position": cs.get("position") or "",
            "cur_done": cs.get("done", 0), "cur_total": cs.get("total", 0)})
    for c in cards:
        first, last = project_window([b["pcts"] for b in c["batches"]])
        # Every batch of the programme is in the same project: show the one before, this one and the next.
        ids = {b["cur_id"] for b in c["batches"]}
        if len(ids) == 1 and None not in ids:
            at = next((j for j, pr in enumerate(c["projects"]) if pr["id"] in ids), None)
            if at is not None:
                first, last = max(0, at - 1), min(len(c["projects"]) - 1, at + 1)
        c["shown"] = [{"n": j + 1, "project": c["projects"][j]} for j in range(first, last + 1)]
        c["before"] = first
        c["after"] = len(c["projects"]) - 1 - last if c["shown"] else 0
        for b in c["batches"]:
            b["segments"] = [{"pct": b["pcts"][x["n"] - 1], **x} for x in c["shown"]]
        c["avg"] = round(sum(b["overall"] for b in c["batches"]) / len(c["batches"])) if c["batches"] else 0
    return cards


def highlights(rows, milestone_days=14, activity_days=7):
    """Recognition, counted from the ticks (no ranking, no schedule words):
    milestones = steps and projects a batch finished recently; activity = items each trainer delivered."""
    from .core import util
    now = util.now_ts()
    m_cut = util.now_iso(now - milestone_days * 86400)
    a_cut = util.now_iso(now - activity_days * 86400)
    milestones, people = [], {}
    for r in rows:
        for p in r["projects"]:
            s = p["s"]
            if not s:
                continue
            for step_id, at in s["step_done_at"].items():
                if at and at >= m_cut:
                    milestones.append({"at": at, "batch": r["batch"], "project": p["project"], "kind": "step",
                                       "title": s["pub"]["index"][step_id]["title"]})
            if s["total"] and s["done"] >= s["total"] and s["last"] and s["last"]["at"] >= m_cut:
                milestones.append({"at": s["last"]["at"], "batch": r["batch"], "project": p["project"],
                                   "kind": "project", "title": p["project"]["name"]})
        for e in progress.events(r["batch"]["id"], 300):
            if e["at"] < a_cut:
                break
            who = people.setdefault(e["by"], {"name": e.get("by_name") or e["by"], "delivered": 0, "batches": set(),
                                              "last": ""})
            who["delivered"] += 1 if e["action"] == "tick" else -1
            who["batches"].add(r["batch"]["name"])
            who["last"] = max(who["last"], e["at"])
    milestones.sort(key=lambda m: m["at"], reverse=True)
    activity = sorted((dict(v, batches=sorted(v["batches"])) for v in people.values() if v["delivered"] > 0),
                      key=lambda v: v["name"].lower())
    return {"milestones": milestones[:12], "activity": activity}


def team(staff, days=7):
    """Trainers compare their batches with the other batches of the same programme (a programme belongs to one
    institution, so this is "same institute and programme"). Counted from the ticks; batches listed by name.

    {days, programmes: [{programme, institution, rows, people, me, others, max_recent, max_delivered}]}"""
    from .core import util
    cut = util.now_iso(util.now_ts() - days * 86400)
    mine = set(staff.batch_ids) if not staff.is_super else set()
    progs = []
    for inst in org.scope(None):
        for p in inst["programmes"]:
            if not staff.is_super and not any(b["id"] in mine for b in p["batches"]):
                continue
            projects = org.list_projects(p["programme"]["id"])
            rows, people = [], {}
            for b in sorted(p["batches"], key=lambda b: b["name"].lower()):
                projs = _project_summaries(b, projects)
                cur = _current(projs, b)
                done = sum(x["s"]["done"] for x in projs if x["s"])
                total = sum(x["s"]["total"] for x in projs if x["s"])
                last = max((x["s"]["last"] for x in projs if x["s"] and x["s"]["last"]), key=lambda t: t["at"],
                           default=None)
                trainers = org.batch_trainers(b["id"])
                for t in trainers:
                    people.setdefault(t["email"], {"email": t["email"], "name": t["name"], "delivered": 0,
                                                   "batches": set()})["batches"].add(b["name"])
                recent = 0
                for e in progress.events(b["id"], 400):
                    if e["at"] < cut:
                        break
                    step = 1 if e["action"] == "tick" else -1
                    recent += step
                    who = people.setdefault(e["by"], {"email": e["by"], "name": e.get("by_name") or e["by"],
                                                      "delivered": 0, "batches": set()})
                    who["delivered"] += step
                    who["batches"].add(b["name"])
                pcts = [project_pct(x) for x in projs]
                at = next((j for j, x in enumerate(projs) if cur and x["project"]["id"] == cur["project"]["id"]), None)
                cur_pct = pcts[at] if at is not None else 0
                cs = (cur or {}).get("s") or {}
                rows.append({"cur_pct": cur_pct, "cur_done": cs.get("done", 0), "cur_total": cs.get("total", 0),
                             "cur_seg": [{"n": at + 1, "project": projs[at]["project"], "pct": cur_pct}] if at is not None else [],
                             "all_seg": [{"n": j + 1, "project": projs[j]["project"], "pct": pcts[j]} for j in range(len(projs))],
                             "batch": b, "trainers": trainers, "current": cur, "done": done, "total": total,
                             "pcts": pcts, "pct": round(sum(pcts) / len(pcts)) if pcts else 0, "recent": max(recent, 0),
                             "last": last, "mine": b["id"] in mine,
                             "finished": sum(1 for v in pcts if v >= 100),
                             "project_total": len(projs)})
            first, last = project_window([r["pcts"] for r in rows])
            shown = [{"n": j + 1, "project": projects[j]} for j in range(first, last + 1)]
            for r in rows:
                r["segments"] = [dict(x, pct=r["pcts"][x["n"] - 1]) for x in shown]
            people = sorted((dict(v, batches=sorted(v["batches"]), delivered=max(v["delivered"], 0),
                                  me=v["email"] == staff.email) for v in people.values()),
                            key=lambda v: v["name"].lower())
            me = next((v for v in people if v["me"]), None)
            others = [v for v in people if not v["me"]]
            my_rows = [r for r in rows if r["mine"]]
            other_rows = [r for r in rows if not r["mine"]]

            def avg(xs):
                return round(sum(xs) / len(xs)) if xs else 0
            progs.append({
                "programme": p["programme"], "institution": inst["institution"], "rows": rows, "people": people,
                "shown": shown, "before": first if shown else 0, "after": len(projects) - 1 - last if shown else 0,
                "all_shown": [{"n": j + 1, "project": projects[j]} for j in range(len(projects))],
                "me": {"delivered": me["delivered"] if me else 0, "pct": avg([r["pct"] for r in my_rows]),
                       "cur_pct": avg([r["cur_pct"] for r in my_rows]), "batches": len(my_rows)},
                "others": {"delivered": avg([v["delivered"] for v in others]),
                           "pct": avg([r["pct"] for r in other_rows]),
                           "cur_pct": avg([r["cur_pct"] for r in other_rows]), "batches": len(other_rows),
                           "trainers": len(others)},
                "max_recent": max([r["recent"] for r in rows] + [1]),
                "max_delivered": max([v["delivered"] for v in people] + [1])})
    return {"programmes": progs, "days": days}


def activity(bid, days=28, only_vid=None):
    """Items marked done per day (India time) over the last `days` days, oldest first:
    [{"date", "count"}]. Un-marks are not subtracted: this is what was delivered on each day."""
    from datetime import datetime, timedelta

    from .core import util
    today = datetime.fromtimestamp(util.now_ts(), util.IST).date()
    dates = [(today - timedelta(days=k)).isoformat() for k in range(days - 1, -1, -1)]
    counts = dict.fromkeys(dates, 0)
    cut = util.now_iso(util.now_ts() - (days + 1) * 86400)
    for e in progress.events(bid, 3000):
        if e["at"] < cut:
            break
        if e.get("action") != "tick" or (only_vid and e.get("version_id") != only_vid):
            continue
        day = util.parse_iso(e["at"]).astimezone(util.IST).date().isoformat()
        if day in counts:
            counts[day] += 1
    return [{"date": d, "count": counts[d]} for d in dates]


def progress_view(d, days=28, only_vid=None):
    """Everything the batch's Progress page shows, from batch_detail(): headline figures, the steps of the
    current project (each with its parts), and the daily activity with clean axis ticks."""
    projs, cur = d["projects"], d["current"]
    done = sum(p["s"]["done"] for p in projs if p["s"])
    total = sum(p["s"]["total"] for p in projs if p["s"])
    steps = []
    for x in d["parts"]:
        if x["node"]["level"] == 1 or not steps:
            steps.append(dict(x, parts=[]))
        else:
            steps[-1]["parts"].append(x)
    act = activity(d["bc"]["batch"]["id"], days, only_vid)
    peak = max((a["count"] for a in act), default=0)
    step = next(s for s in (1, 2, 5, 10, 20, 25, 50, 100, 200, 500, 1000, 10**9) if s * 2 >= max(peak, 1))
    top = step * 2 if step * 2 >= peak else step * 3
    return {"done": done, "total": total, "steps": steps, "activity": act, "axis_top": top,
            "axis_ticks": [top, top // 2], "week": sum(a["count"] for a in act[-7:]),
            "month": sum(a["count"] for a in act),
            "choosable": [p["project"] for p in projs if p["s"]],
            "active_days": sum(1 for a in act if a["count"])}
