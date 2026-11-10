"""Progress dashboard figures. Every number is counted from the stored ticks (never estimated).
No schedule words: position and completion only.

batch_rows(batch_ids) -> one row per batch (level 1)
batch_detail(batch_id) -> everything for one batch (level 2)
MCQ and live-data columns are filled when those systems answer; otherwise "not available".
"""
import traceback

from .core import org
from .core.util import NotFound
from .runbook import progress, service


def _project_summaries(batch, projects):
    out = []
    for j in projects:
        vid = progress.batch_version(batch, j, pin=False) if j.get("runbook_id") else None
        s = None
        if vid:
            try:
                s = progress.summary(batch["id"], vid)
            except NotFound:
                s = None
        out.append({"project": j, "vid": vid, "s": s})
    return out


def _current(projs):
    """The project the batch is in now: the one with the most recent tick; else the first not finished."""
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


def batch_rows(scope_tree):
    """Rows for level 1, from org.scope(...) output."""
    batches = [(i, p, b) for i in scope_tree for p in i["programmes"] for b in p["batches"]]
    mcq = _mcq_by_batch([b for _, _, b in batches])
    live = _live_by_batch([b for _, _, b in batches])
    rows = []
    projects_cache = {}
    for inst, prog, b in batches:
        pid = prog["programme"]["id"]
        if pid not in projects_cache:
            projects_cache[pid] = org.list_projects(pid)
        projs = _project_summaries(b, projects_cache[pid])
        cur = _current(projs)
        finished = sum(1 for p in projs if p["s"] and p["s"]["total"] and p["s"]["done"] >= p["s"]["total"])
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
    cur = _current(projs)
    notes, skipped = [], []
    for p in projs:
        if not p["s"]:
            continue
        pub = p["s"]["pub"]
        for item_id, n in progress.notes(b["id"], p["vid"]).items():
            if n.get("text") and item_id in pub["index"]:
                notes.append({"project": p["project"], "node": pub["index"][item_id], "note": n,
                              "path": service.path_label(pub, pub["index"][item_id])})
        if p is cur:
            skipped = [{"node": n, "path": service.path_label(pub, n)} for n in p["s"]["skipped"]]
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
            "events": events, "event_titles": titles, "skipped": skipped, "parts": parts,
            "trainers": org.batch_trainers(b["id"])}


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
