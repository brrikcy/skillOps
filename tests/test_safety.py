"""Repeat-safe writes, audit log, export and restore, CSV import, health."""
import io
import threading
import zipfile

from helpers import body, setup_org
from skillops.core import audit, export, importer, org, store


def test_double_submit_creates_one_institution(boss):
    form = {"name": "Twice Clicked", "code": "TC", "idem": "key-123"}
    r1 = boss.post("/admin/institutions", form)
    r2 = boss.post("/admin/institutions", form)
    assert r1["statusCode"] == r2["statusCode"] == 303
    assert r1["headers"]["location"] == r2["headers"]["location"]
    assert [i["name"] for i in org.list_institutions()] == ["Twice Clicked"]


def test_simultaneous_submits_create_one_batch(boss):
    ids = setup_org()
    results = []

    def send():
        results.append(boss.post(f"/admin/programmes/{ids['p1']}/batches", {"name": "Batch D", "idem": "same"}))
    threads = [threading.Thread(target=send) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len([b for b in org.list_batches(ids["p1"]) if b["name"] == "Batch D"]) == 1
    assert all(r["statusCode"] in (303, 409) for r in results)


def test_failed_work_can_be_retried_with_the_same_key(boss):
    ids = setup_org()
    r = boss.post(f"/admin/programmes/{ids['p1']}/batches", {"name": "", "idem": "k-retry"})
    assert r["statusCode"] == 400 and "Please fill in the batch name." in body(r)
    r = boss.post(f"/admin/programmes/{ids['p1']}/batches", {"name": "Batch E", "idem": "k-retry"})
    assert r["statusCode"] == 303


def test_audit_records_writes_refusals_and_filters(boss):
    ids = setup_org()
    boss.post(f"/admin/batches/{ids['A']}/update", {"name": "Batch A1", "mcq_batch": "A"})
    boss.post(f"/admin/batches/{ids['A']}/update", {"name": "", "mcq_batch": "A"})
    items = audit.read(action="update batch")
    assert [i["result"] for i in items] == ["not done", "ok"]
    assert items[1]["batch"] == ids["A"] and items[1]["who"] == "boss@robomy.test"
    assert audit.read(batch=ids["A"], result="ok")[0]["action"] == "update batch"
    r = boss.get("/admin/audit?action=update+batch&format=csv")
    text = r["body"]
    import base64
    csv_text = base64.b64decode(text).decode("utf-8-sig")
    assert csv_text.splitlines()[0].startswith("time (UTC),who,role,action")
    assert len(csv_text.splitlines()) == 3


def test_export_then_restore_gives_back_the_same_data(aws):
    ids = setup_org()
    org.set_archived("batch", ids["C"], True)
    audit.write("boss@robomy.test", "superadmin", "test", ids["A"], "x")
    blob, manifest = export.build_zip()
    before = {name: sorted(store.dumps(i) for i in store.raw_scan(name) if export._keep(name, i))
              for name in export.EXPORT_TABLES}
    z = zipfile.ZipFile(io.BytesIO(blob))
    assert {"manifest.json", "README.txt", "csv/batches.csv", "csv/trainers.csv", "csv/audit.csv",
            "tables/CORE.jsonl"} <= set(z.namelist())
    assert "Batch A" in z.read("csv/batches.csv").decode("utf-8-sig")
    # wipe the tables (a fresh stack) and restore
    c = store.client()
    for name in export.EXPORT_TABLES:
        c.delete_table(TableName=store.config.table(name))
    store.reset()
    defs = store.all_table_definitions()
    for name in export.EXPORT_TABLES:
        c.create_table(TableName=store.config.table(name), BillingMode="PAY_PER_REQUEST", **defs[name])
    counts = export.restore_zip(blob, say=lambda m: None)
    after = {name: sorted(store.dumps(i) for i in store.raw_scan(name)) for name in export.EXPORT_TABLES}
    assert after == before
    assert counts["CORE"][1] == 0
    # restoring again overwrites nothing
    counts = export.restore_zip(blob, say=lambda m: None)
    assert counts["CORE"][0] == 0 and counts["CORE"][1] == len(before["CORE"])


def test_export_job_from_the_page(boss):
    setup_org()
    boss.post("/admin/export", {})
    import time
    for _ in range(50):
        jobs = org.store.by_gsi1("JOBS#export")
        if jobs and jobs[0]["status"] == "done":
            break
        time.sleep(0.1)
    assert jobs[0]["status"] == "done", jobs
    r = boss.get(f"/admin/export/{jobs[0]['id']}/download")
    assert r["statusCode"] == 200 and r["headers"]["content-type"] == "application/zip"


def test_csv_import_plans_applies_and_is_safe_to_repeat(aws):
    text = ("institution,institution_code,programme,course,project,batch,mcq_batch,trainer_name,trainer_email\n"
            ",,,Data Analytics,IPL,,,,\n"
            ",,,Data Analytics,Airtel,,,,\n"
            ",,,Case Studies,Uber,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,Data Analytics,,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,Uber,,,,\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,,Batch A,A,Trainer One,t1@robomy.test\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,,Batch B,B,Trainer Two,t2@robomy.test\n"
            "DBI Skill Park,DBI,DBI OJT Sem 3 - Data Analyst,,,Batch B,B,Trainer One,t1@robomy.test\n")
    plan = importer.plan(text)
    assert plan["errors"] == [], plan["errors"]
    # 2 courses, 3 projects, 1 institution, 1 programme, 1 course selected, 1 project added, 2 batches,
    # 2 trainers, 2 added to the institution, 3 links
    assert len(plan["actions"]) == 18, plan["actions"]
    done = importer.apply(text)
    assert done == plan["actions"]
    prog = org.list_programmes()[0]
    assert [j["name"] for j in org.list_projects(prog["id"])] == ["IPL", "Airtel", "Uber"]
    assert len(org.trainer_batch_ids("t1@robomy.test")) == 2
    assert org.trainer_institution_ids("t2@robomy.test") == {prog["inst_id"]}
    assert importer.plan(text)["actions"] == []
    assert importer.apply(text) == []


def test_csv_import_reports_bad_lines(aws):
    text = ("institution,programme,batch,trainer_email\n"
            "New Place,Prog,Batch A,not-an-email\n")
    plan = importer.plan(text)
    assert any("institution_code" in e for e in plan["errors"])


def test_health_page_reports_each_part(boss):
    r = boss.get("/health")
    assert r["statusCode"] == 200
    assert "Every part is working." in body(r)
    for part in ("Database table: core", "Database table: progress", "Database table: audit",
                 "Runbook file storage (S3)", "Staff sign-in (Cognito)"):
        assert part in body(r)
