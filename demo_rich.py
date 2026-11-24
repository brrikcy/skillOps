"""Temporary sample data, only to look at the screens full of things. Laptop only: python local_server.py --sample

On top of the normal --seed data it adds: three more runbooks (so every project on the dashboard has progress),
ticks for every batch spread over the last weeks by different trainers, notes, class days and plans for past and coming
days, students who joined with a code, and a busy audit log. All of it is in memory and is gone when the server stops.
"""
import os
import random
import time
from datetime import date, timedelta

rnd = random.Random(11)


def _staff(email, name, bids):
    from skillops.core.perms import Staff
    return Staff(email, "trainer", name, set(bids))


def _publish(name, path, say):
    from skillops.core import jobs
    from skillops.runbook import detect, service
    jobs.INLINE = True
    try:
        rid = service.create_runbook(name)
        with open(path, "rb") as f:
            vid = service.add_version(rid, os.path.basename(path), f.read(), "demo")
        v = service.get_version(vid)
        mapping, checkable = detect.suggest(v["patterns"])
        service.save_mapping(vid, mapping, checkable, ["Step", "Part", "Action", "Sub-step"])
        service.publish(vid, "demo")
    finally:
        jobs.INLINE = False
    say(f"  runbook '{name}' published")
    return rid


def _tick_at(bid, vid, item_id, staff, pid, ts):
    """A tick made at an earlier time (the normal tick is always 'now')."""
    from skillops.core import store, util
    from skillops.runbook import progress
    at = util.now_iso(ts)
    try:
        store.t("PROGRESS").put_item(
            Item={"pk": progress._pk(bid, vid), "sk": f"I#{item_id}", "batch_id": bid, "version_id": vid, "item_id": item_id,
                  "project_id": pid, "by": staff.email, "by_name": staff.name, "at": at},
            ConditionExpression="attribute_not_exists(pk)")
    except Exception:  # noqa: BLE001  (already ticked by the basic seed)
        return
    store.t("PROGRESS").put_item(Item={"pk": f"EVT#{bid}", "sk": f"{at}#{rnd.randrange(16**3):03x}", "at": at, "action": "tick",
                                       "item_id": item_id, "version_id": vid, "project_id": pid, "by": staff.email,
                                       "by_name": staff.name})


def _progress(bid, project, staffs, n, days):
    """Tick the first n actions of a project's runbook, spread over the last `days` days."""
    from skillops.core import org
    from skillops.runbook import progress, service
    b = org.get_batch(bid)
    vid = progress.batch_version(b, project)
    if not vid:
        return None
    cks = service.published(vid)["checkables"][:n]
    now = time.time()
    for i, node in enumerate(cks):
        ago = days - int(i * days / max(len(cks), 1))
        ts = now - ago * 86400 - 3 * 3600 + (i % 9) * 780
        if ts > now - 120:
            ts = now - 120 * (len(cks) - i)
        _tick_at(bid, vid, node["id"], staffs[(i // 4) % len(staffs)], project["id"], ts)
    return vid


def _plans(bid, project, vid, weekdays, staff, past, coming, say=None):
    """Class days and a plan for some past and some coming class days (parts around where the batch is)."""
    from skillops import classdays, planner
    from skillops.runbook import progress, service
    today = date.today()
    classdays.save(bid, {"start": (today - timedelta(days=45)).isoformat(), "end": (today + timedelta(days=120)).isoformat(),
                         "off": "", "extra": ""}, weekdays)
    cks = service.published(vid)["checkables"]
    summ = progress.summary(bid, vid)
    pos = next((k for k, n in enumerate(cks) if summ["next_up"] and n["id"] == summ["next_up"]["id"]), 0)
    days = [d for d in (today + timedelta(days=k) for k in range(-28, 22)) if str(d.weekday()) in weekdays and d != today]
    past_days = [d for d in days if d < today][-past:]
    next_days = [d for d in days if d > today][:coming]
    notes = ["Bring the HDMI cable", "Quick quiz at the end", "Revise last class first", "Pair work after the break", ""]
    for k, d in enumerate(past_days + next_days):
        start = max(0, pos - (len(past_days) - k) * 3) if d < today else pos + (k - len(past_days)) * 3
        entries = [{"kind": "item", "proj": project["id"], "node": n["id"]} for n in cks[start:start + 3]]
        if len(entries) > 1:
            entries.insert(2, {"kind": "text", "text": rnd.choice(["Short break", "Quiz on joins", "Recap and questions"])})
        try:
            planner.save(bid, d.isoformat(), entries, notes[k % len(notes)], 0, staff)
        except Exception:  # noqa: BLE001
            pass


def _students(ids, say):
    from skillops.livedata import students, timeutil
    names = ["Aarav Sharma", "Ananya Iyer", "Vihaan Gupta", "Diya Patel", "Arjun Reddy", "Meera Nair", "Rohan Das", "Isha Verma",
             "Kabir Singh", "Saanvi Rao", "Aditya Joshi", "Navya Menon", "Dev Kapoor", "Riya Bose"]
    now = timeutil.now_us()
    try:
        code = students.create_code(ids["A"], 60, 24 * 60, False, "demo", now)["code"]
        for k, name in enumerate(names):
            students.register({"join_code": code, "full_name": name, "roll_number": f"DBI-A-{100 + k}"},
                              f"10.0.0.{k + 1}", "http://127.0.0.1:8795", now)
        say(f"  {len(names)} students joined Batch A for live data")
    except Exception as e:  # noqa: BLE001
        say(f"  (students were not added: {e})")


def _audit():
    from skillops.core import store, util
    now = time.time()
    rows = [("supervisor@local.test", "superadmin", "create institution", "", "DBI Skill Park", "ok", ""),
            ("supervisor@local.test", "superadmin", "upload runbook version", "", "Airtel", "ok", "v-sample"),
            ("supervisor@local.test", "superadmin", "publish runbook version", "", "Airtel v1", "ok", ""),
            ("supervisor@local.test", "superadmin", "set class days", "", "Batch A", "ok", "Mon, Tue, Thu"),
            ("trainer1@local.test", "trainer", "sign in", "", "", "ok", ""),
            ("trainer1@local.test", "trainer", "mark item complete", "", "Airtel 1A.4", "ok", ""),
            ("trainer2@local.test", "trainer", "mark item complete", "", "Airtel 1A.1", "ok", ""),
            ("trainer1@local.test", "trainer", "save session plan", "", "2026-11-24", "ok", "5 entries"),
            ("trainer3@local.test", "trainer", "sign in", "", "", "ok", ""),
            ("trainer2@local.test", "trainer", "save item note", "", "Airtel K.3", "ok", ""),
            ("supervisor@local.test", "superadmin", "attach runbook pdf", "", "Airtel v1", "ok", "355 pages"),
            ("trainer4@local.test", "trainer", "mark item complete", "", "Airtel K.1", "ok", ""),
            ("someone@example.com", "", "sign in", "", "", "denied", "wrong password"),
            ("trainer1@local.test", "trainer", "un-mark item", "", "Airtel 1A.5", "ok", ""),
            ("supervisor@local.test", "superadmin", "save mcq settings", "", "DBI Skill Park", "ok", "")]
    for k, (who, role, action, batch, target, result, detail) in enumerate(rows):
        at = util.now_iso(now - (len(rows) - k) * 5400 - rnd.randrange(600))
        store.t("AUDIT").put_item(Item={"pk": f"AUDIT#{at[:7]}", "sk": f"{at}#{k:03x}", "at": at, "who": who, "role": role,
                                        "action": action, "batch": batch, "target": target, "result": result,
                                        "detail": detail, "request_id": ""})


def seed_rich(ids, say=print):
    from skillops.core import org
    from skillops.runbook import progress, service
    import demo
    say("Sample data (temporary):")
    path = demo.SAMPLE_RUNBOOK
    p1_projects = {j["name"]: j for j in org.list_projects(ids["p1"])}
    if not os.path.exists(path):
        say("  (the sample runbook file is missing: only the basic sample data is shown)")
        return
    for name in ("IPL", "HDFC", "Uber"):
        rid = _publish(name, path, say)
        org.set_project_runbook(p1_projects[name]["id"], rid)
    p1_projects = {j["name"]: j for j in org.list_projects(ids["p1"])}          # now with their runbooks
    t1 = _staff("trainer1@local.test", "Trainer 1", [ids["A"], ids["b2"]])
    t2 = _staff("trainer2@local.test", "Trainer 2", [ids["B"]])
    t3 = _staff("trainer3@local.test", "Trainer 3", [ids["C"]])
    t4 = _staff("trainer4@local.test", "Trainer 4", [ids["b2"]])
    air, ipl, hdfc, uber = (p1_projects[n] for n in ("Airtel", "IPL", "HDFC", "Uber"))
    air_w = next(j for j in org.list_projects(org.get_batch(ids["b2"])["prog_id"]) if j["name"] == "Airtel")
    plan = [(ids["A"], [t1], [(ipl, 60, 26), (air, 46, 16), (hdfc, 14, 6), (uber, 4, 2)]),
            (ids["B"], [t2], [(ipl, 34, 22), (air, 22, 14), (hdfc, 5, 4)]),
            (ids["C"], [t3], [(ipl, 18, 20), (air, 12, 10)]),
            (ids["b2"], [t1, t4], [(air_w, 17, 12)])]
    vids = {}
    for bid, staffs, rows in plan:
        for proj, n, days in rows:
            vids[(bid, proj["id"])] = _progress(bid, proj, staffs, n, days)
    say("  ticks spread over the last weeks for every batch")
    for bid, proj, node_i, text, who in ((ids["A"], air, 6, "Repeat the Git set-up next time: three laptops had no Git.", t1),
                                          (ids["A"], air, 18, "Students liked the shop tables; slow down on GROUP BY.", t1),
                                          (ids["A"], ipl, 9, "Two students missed this class: share the recap.", t1),
                                          (ids["B"], air, 7, "Class ran short: finish the SQL toolkit warm-up.", t2),
                                          (ids["C"], ipl, 4, "Projector failed for 20 minutes.", t3)):
        vid = vids.get((bid, proj["id"]))
        if vid:
            cks = service.published(vid)["checkables"]
            progress.save_note(bid, vid, cks[min(node_i, len(cks) - 1)]["id"], text, who)
    _plans(ids["A"], air, vids[(ids["A"], air["id"])], ["0", "1", "3"], t1, 4, 4)
    _plans(ids["B"], air, vids[(ids["B"], air["id"])], ["1", "4"], t2, 3, 3)
    _plans(ids["C"], air, vids[(ids["C"], air["id"])], ["2", "5"], t3, 2, 3)
    _plans(ids["b2"], air_w, vids[(ids["b2"], air_w["id"])], ["5", "6"], t4, 2, 3)
    say("  class days and plans for every batch")
    _students(ids, say)
    _audit()
    say("  audit log filled")
