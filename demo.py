"""Try SkillOps on your laptop in one command (fake AWS; nothing to install beyond requirements-dev).

    python demo.py
    python demo.py --runbook "C:\\path\\to\\Airtel_Classroom_Delivery_Runbook_Complete.docx"

It creates:
  1. Two institutions. DBI Skill Park: the programme "DBI OJT Sem 3 - Data Analyst" (IPL -> Airtel -> HDFC ->
     Uber -> Apollo -> IRCTC -> Coursera) with Batch A, B and C and three trainers, one per batch.
     Sample Training Centre: one programme using the same runbook, with one batch.
  2. The sample runbook uploaded, read, mapped with the saved template "Robomy runbook v3", published,
     and chosen for the Airtel project of both programmes.
  3. A few ticks for Batch A, then prints the batch's position.
  4. A small live-data practice feed with a join code, and the fake MCQ test system for DBI.
local_server.py --seed uses the same steps, so the web pages have something to show.
"""
import argparse
import os
import random
import sqlite3
import sys
import tempfile
from datetime import timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

PROJECTS = ["IPL", "Airtel", "HDFC", "Uber", "Apollo", "IRCTC", "Coursera"]
# the sample runbook (.docx; the same runbook saved as .pdf beside it is attached too): set SKILLOPS_SAMPLE_RUNBOOK to its path
SAMPLE_RUNBOOK = os.environ.get("SKILLOPS_SAMPLE_RUNBOOK", "")


COURSES = {"Data Analytics": PROJECTS[:4], "Business Case Studies": PROJECTS[4:]}


def seed_org(say=print):
    from skillops.core import org
    courses = {}
    for cname, projects in COURSES.items():             # Robomy's catalogue
        cid = courses[cname] = org.create_course(cname)
        for name in projects:
            org.add_project(cid, name)
    i1 = org.create_institution("DBI Skill Park", "DBI")
    p1 = org.create_programme(i1, "DBI OJT Sem 3 - Data Analyst")
    org.set_programme_courses(p1, list(courses.values()))          # both courses: all 7 projects
    batches = {}
    for n, letter in enumerate("ABC", start=1):
        bid = org.create_batch(p1, f"Batch {letter}", letter)
        batches[letter] = bid
        email = f"trainer{n}@local.test"
        org.create_trainer(email, f"Trainer {n}", i1)
        org.associate(email, bid)
    i2 = org.create_institution("Sample Training Centre", "STC")
    p2 = org.create_programme(i2, "Data Analyst Foundation")
    airtel = next(j for j in org.course_projects(courses["Data Analytics"]) if j["name"] == "Airtel")
    org.add_programme_project(p2, airtel["id"])                    # one project from a course it does not take
    b2 = org.create_batch(p2, "Weekend Batch", "WEEKEND")
    org.add_trainer_to_institution("trainer1@local.test", i2)     # Trainer 1 works with both institutions
    org.associate("trainer1@local.test", b2)
    org.create_trainer("trainer4@local.test", "Trainer 4", i2)
    org.associate("trainer4@local.test", b2)
    say("Created Robomy's courses (Data Analytics: 4 projects, Business Case Studies: 3), DBI Skill Park "
        "(batches A, B, C, one trainer each) and Sample Training Centre (1 batch).")
    return {"i1": i1, "p1": p1, "i2": i2, "p2": p2, "b2": b2, "courses": courses, **batches}


def seed_runbook(ids, path=SAMPLE_RUNBOOK, say=print):
    """Upload the sample runbook, map it with a saved template, publish it, use it for Airtel."""
    from skillops.core import jobs, org
    from skillops.runbook import detect, progress, service
    if not os.path.exists(path):
        say(f"(Sample runbook not found at {path}; skipping the runbook part.)")
        return None
    jobs.INLINE = True
    try:
        rid = service.create_runbook("Airtel")
        with open(path, "rb") as f:
            vid = service.add_version(rid, os.path.basename(path), f.read(), "demo")
        v = service.get_version(vid)
        mapping, checkable = detect.suggest(v["patterns"])
        service.save_mapping(vid, mapping, checkable, ["Step", "Part", "Action", "Sub-step"],
                             template_name="Robomy runbook v3")
        service.publish(vid, "demo")
    finally:
        jobs.INLINE = False
    pdf_path = os.path.splitext(path)[0] + ".pdf"                  # the runbook saved as PDF, if it is beside the Word file
    if os.path.exists(pdf_path):
        try:
            with open(pdf_path, "rb") as f:
                service.attach_pdf(vid, os.path.basename(pdf_path), f.read(), "demo")      # matched in the background
            say("Runbook PDF attached (session PDFs are cut from it; it is matched to the pages in the background).")
        except Exception as e:  # noqa: BLE001
            say(f"(The runbook PDF was not attached: {e})")
    v = service.get_version(vid)
    counts = v["counts"]
    say(f"Runbook 'Airtel' v1 published: {counts.get('1')} steps, {counts.get('2')} parts, {counts.get('3')} actions, "
        f"{counts.get('4')} sub-steps.")
    proj = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(proj["id"], rid)                      # one project, used by both programmes
    # A few ticks for Batch A: everything up to 0.3, plus 1A.1 and 1A.2.
    from skillops.core.perms import Staff
    trainer = Staff("trainer1@local.test", "trainer", "Trainer 1", {ids["A"]})
    batch = org.get_batch(ids["A"])
    proj = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    vid_a = progress.batch_version(batch, proj)
    pub = service.published(vid_a)
    wanted = {"K.1", "K.2", "K.3", "K.4", "0.1", "0.2", "0.3", "1A.1", "1A.2", "1A.4"}
    chosen = [n for n in pub["checkables"] if n.get("code") in wanted]
    if not chosen:                      # another runbook: tick the first few items, leaving one gap
        chosen = [n for k, n in enumerate(pub["checkables"][:6]) if k != 3]
    for n in chosen:
        progress.tick(ids["A"], vid_a, n["id"], trainer, proj["id"])
    progress.save_note(ids["A"], vid_a, chosen[min(2, len(chosen) - 1)]["id"],
                       "Repeat the Git set-up next time: three laptops had no Git.", trainer)
    s = progress.summary(ids["A"], vid_a)
    say(f"Batch A ticked {s['done']} of {s['total']} actions. Position: {s['position']}")
    return {"rid": rid, "vid": vid, "summary": s}


def seed_mcq(ids, say=print):
    from skillops.mcq import client, fake
    client.save_settings(ids["i1"], "fake://demo", fake.SECRET)
    client.test_connection(ids["i1"])
    say("The fake MCQ test system is connected for DBI Skill Park (local mode only).")


def seed_live(ids, say=print, n_rows=300, every=10):
    """A tiny practice feed: rows already released, more every few seconds; a join code for Batch A."""
    from skillops.livedata import feedops, loader, students, timeutil
    tz, data_start = "Asia/Kolkata", "2025-07-01"
    now = timeutil.now_us()
    start_dt = timeutil.us_to_dt(timeutil.parse_local(data_start, tz)).astimezone(timeutil.ZoneInfo(tz)).replace(tzinfo=None)
    path = os.path.join(tempfile.mkdtemp(prefix="skillops-demo-"), "practice.db")
    rnd = random.Random(7)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (order_id INTEGER, order_time TEXT, customer TEXT, city TEXT, amount REAL)")
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", [
        (5001 + i, (start_dt + timedelta(seconds=every * i)).strftime("%Y-%m-%d %H:%M:%S"), f"C{rnd.randint(100, 999)}",
         rnd.choice(["Mumbai", "Pune", "Delhi", "Bengaluru", ""]), round(rnd.uniform(80, 2500), 2)) for i in range(n_rows)])
    conn.commit()
    conn.close()
    feed = feedops.ensure_feed("practice", data_start, tz, now - 10 * 60 * 1_000_000, 1, now, say=lambda m: None)
    src = loader.open_source(path)
    loader.upload_dataset(src, {"table": "orders", "dataset": "practice_orders", "time_column": "order_time",
                                "key_column": "order_id"}, feed, activate=True, say=lambda m: None, workers=2)
    src.close()
    code = students.create_code(ids["A"], 60, 24 * 60, False, "demo", now)["code"]
    say(f"Live data: practice feed loaded; join code for Batch A is {code}.")
    return code


def seed_all(say=print, runbook=SAMPLE_RUNBOOK):
    ids = seed_org(say)
    seed_runbook(ids, runbook, say)
    seed_mcq(ids, say)
    seed_live(ids, say)
    seed_schedule(ids, say)
    return ids


def seed_schedule(ids, say=print):
    """Batch A meets on Monday, Tuesday and Thursday; today's session is planned (when Airtel has a runbook)."""
    from datetime import date, timedelta

    from skillops import classdays, planner
    from skillops.core import org
    from skillops.core.perms import Staff
    from skillops.runbook import progress, service
    today = date.today()
    classdays.save(ids["A"], {"start": (today - timedelta(days=30)).isoformat(),
                              "end": (today + timedelta(days=120)).isoformat(), "off": "", "extra": today.isoformat()},
                   ["0", "1", "3"])
    b = org.get_batch(ids["A"])
    proj = next((j for j in org.list_projects(b["prog_id"]) if j.get("runbook_id")), None)
    vid = progress.batch_version(b, proj, pin=False) if proj else None
    if not vid:
        say("Class days set for Batch A (Mon, Tue, Thu).")
        return
    summ = progress.summary(ids["A"], vid)
    nxt = summ["next_up"]
    cks = summ["pub"]["checkables"]
    k = next((i for i, n in enumerate(cks) if nxt and n["id"] == nxt["id"]), 0)
    entries = [{"kind": "item", "proj": proj["id"], "node": n["id"]} for n in cks[k:k + 4]]
    entries.insert(min(2, len(entries)), {"kind": "text", "text": "Short break"})
    t1 = Staff("trainer1@local.test", "trainer", "Trainer 1", {ids["A"]})
    planner.save(ids["A"], today.isoformat(), entries, "Bring the HDMI cable", 0, t1)
    say("Class days set for Batch A (Mon, Tue, Thu), and today's session is planned.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runbook", default=SAMPLE_RUNBOOK, help="path of a runbook .docx")
    args = ap.parse_args()
    from local_server import local_env
    local_env()
    from moto import mock_aws
    with mock_aws():
        from skillops import livedata  # noqa: F401  (registers the live-data tables)
        from skillops.core import org, store
        store.create_tables()
        store.create_buckets()
        ids = seed_org()
        seed_runbook(ids, args.runbook)
        print()
        for t in org.list_trainers():
            tree = org.scope(org.trainer_batch_ids(t["email"]))
            seen = [f"{i['institution']['name']} / {p['programme']['name']} / {b['name']}"
                    for i in tree for p in i["programmes"] for b in p["batches"]]
            print(f"{t['name']} sees: " + "; ".join(seen))


if __name__ == "__main__":
    main()
