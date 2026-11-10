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
SAMPLE_RUNBOOK = os.environ.get("SKILLOPS_SAMPLE_RUNBOOK", "")


def seed_org(say=print):
    from skillops.core import org
    i1 = org.create_institution("DBI Skill Park", "DBI")
    p1 = org.create_programme(i1, "DBI OJT Sem 3 - Data Analyst")
    for name in PROJECTS:
        org.add_project(p1, name)
    batches = {}
    for n, letter in enumerate("ABC", start=1):
        bid = org.create_batch(p1, f"Batch {letter}", letter)
        batches[letter] = bid
        email = f"trainer{n}@local.test"
        org.create_trainer(email, f"Trainer {n}")
        org.associate(email, bid)
    i2 = org.create_institution("Sample Training Centre", "STC")
    p2 = org.create_programme(i2, "Data Analyst Foundation")
    org.add_project(p2, "Airtel")
    b2 = org.create_batch(p2, "Weekend Batch", "WEEKEND")
    org.associate("trainer1@local.test", b2)        # Trainer 1 teaches at both institutions
    say("Created DBI Skill Park (7 projects, batches A, B, C, one trainer each) and Sample Training Centre (1 batch).")
    return {"i1": i1, "p1": p1, "i2": i2, "p2": p2, "b2": b2, **batches}


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
    v = service.get_version(vid)
    counts = v["counts"]
    say(f"Runbook 'Airtel' v1 published: {counts.get('1')} steps, {counts.get('2')} parts, {counts.get('3')} actions, "
        f"{counts.get('4')} sub-steps.")
    for pid in (ids["p1"], ids["p2"]):
        proj = next(j for j in org.list_projects(pid) if j["name"] == "Airtel")
        org.set_project_runbook(pid, proj["id"], rid)
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
    return ids


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
