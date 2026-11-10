"""Tests for the fixes made after the fresh-eyes review (2026-11-09)."""
import io
import threading
import zipfile

import boto3
import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops.core import export, jobs, org, store
from skillops.core.http import safe_next
from skillops.core.perms import Staff
from skillops.mcq import client, fake
from skillops.runbook import detect, progress, service


@pytest.fixture(autouse=True)
def inline_jobs():
    jobs.INLINE = True
    yield
    jobs.INLINE = False


def publish(data, rid=None, name="Sample"):
    rid = rid or service.create_runbook(name)
    vid = service.add_version(rid, "x.docx", data, "boss")
    v = service.get_version(vid)
    if not v["mapping"] or v["status"] != "mapping":
        raise AssertionError(v)
    mapping, checkable = detect.suggest(v["patterns"])
    service.save_mapping(vid, mapping, checkable, [])
    service.publish(vid, "boss")
    return rid, vid


# ------------------------------------------------------------------ MCQ: a trainer cannot touch other batches' tests

def test_trainer_cannot_change_another_batchs_or_shared_test(aws):
    fake.reset()
    ids = setup_org()
    client.save_settings(ids["i1"], "fake://demo", fake.SECRET)
    fake._st()["tests"].append({"id": "WB", "title": "B only", "active": False, "duration": 30, "questions": 5,
                                "batches": ["B"]})
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"/b/{ids['A']}/mcq")
    r = t.post(f"/b/{ids['A']}/mcq/tests/WB/active", {"active": "1"})
    assert r["statusCode"] == 400 and "not for your batch" in body(r)
    assert next(x for x in fake._st()["tests"] if x["id"] == "WB")["active"] is False
    csv = "Question,A,B,Answer\nQ1,x,y,A\n"
    r = t.post(f"/b/{ids['A']}/mcq/import", {"test_id": "W01", "text": csv, "mode": "add"})
    assert r["statusCode"] == 400 and "shared by all batches" in body(r)
    r = t.post(f"/b/{ids['A']}/mcq/import", {"test_id": "NEWA", "text": csv, "mode": "add"})
    assert r["statusCode"] == 303
    assert next(x for x in fake._st()["tests"] if x["id"] == "NEWA")["batches"] == ["A"]   # a new test is the batch's own
    assert "monitor&amp;key=" not in body(page) and "fakekey" not in body(page)          # monitor key: supervisor only


def test_the_same_mcq_form_sent_twice_is_done_once(aws):
    fake.reset()
    ids = setup_org()
    client.save_settings(ids["i1"], "fake://demo", fake.SECRET)
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    t.get(f"/b/{ids['A']}/mcq")
    before = next(s for s in fake._st()["sessions"] if s["id"] == "L0")["remainingSec"]
    form = {"session": "L0", "action": "add", "minutes": "5", "idem": "same-form-key"}
    t.post(f"/b/{ids['A']}/mcq/monitor/W02/session", form)
    t.post(f"/b/{ids['A']}/mcq/monitor/W02/session", form)
    assert next(s for s in fake._st()["sessions"] if s["id"] == "L0")["remainingSec"] == before + 300


# ------------------------------------------------------------------ cross-site forms and redirects

def test_a_form_from_another_site_is_never_kept(aws):
    setup_org()
    anon = Browser()
    r = anon.post("/admin/trainers", {"name": "Evil", "email": "evil@x.test"}, csrf=False,
                  headers={"origin": "https://evil.example"})
    assert r["statusCode"] == 403
    assert store.scan_all("CORE", FilterExpression=store.Attr("pk").begins_with("PENDING#")) == []
    r = anon.post("/admin/trainers", {"name": "Evil", "email": "evil@x.test"}, csrf=False, headers={"origin": ""})
    assert r["statusCode"] == 403                                   # no Origin/Referer: not kept either


def test_a_kept_form_works_only_in_the_browser_that_sent_it(aws):
    setup_org()
    import os
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    mine = Browser()
    r = mine.post("/admin/trainers", {"name": "Asha", "email": "asha@robomy.test", "idem": "k"}, csrf=False)
    pid = r["headers"]["location"].split("pending=")[1]
    other = Browser()
    other.sign_in_local("boss@robomy.test")
    page = other.get(f"/resume/{pid}")
    assert page["statusCode"] == 303 and "Save it now" not in body(page)
    mine.sign_in_local("boss@robomy.test")
    assert "Save it now" in body(mine.get(f"/resume/{pid}"))


@pytest.mark.parametrize("bad", ["/\t/evil.example/x", "//evil.example", "/\\evil.example", "https://evil.example",
                                 "/ /x", "/\r\n/evil.example", "javascript:alert(1)"])
def test_redirects_never_leave_the_site(bad):
    assert safe_next(bad) == "/"


def test_sign_in_with_a_tab_in_next_stays_on_the_site(aws):
    import os
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    r = Browser().post("/signin", {"local_user": "boss@robomy.test", "next": "/\t/evil.example/x"}, csrf=False)
    assert r["headers"]["location"] == "/"


# ------------------------------------------------------------------ runbook versions

def test_s3_return_page_twice_makes_one_version(aws):
    rid = service.create_runbook("Twice")
    data = docx_factory.runbook()
    v1 = service.add_version_once(rid, "uploads/incoming/abcdefgh12.docx", "a.docx", data, "boss")
    v2 = service.add_version_once(rid, "uploads/incoming/abcdefgh12.docx", "a.docx", data, "boss")
    assert v1 == v2 and len(service.list_versions(rid)) == 1


def test_version_numbers_are_never_shared(aws):
    rid = service.create_runbook("Busy")
    jobs.INLINE = False
    jobs.RUNNERS_SAVED = dict(jobs.RUNNERS)
    jobs.RUNNERS["runbook_read"] = lambda job: {}        # only the numbering matters here
    try:
        threads = [threading.Thread(target=service.add_version, args=(rid, "x.docx", docx_factory.runbook(), "b"))
                   for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        jobs.RUNNERS.update(jobs.RUNNERS_SAVED)
    assert sorted(v["number"] for v in service.list_versions(rid)) == [1, 2, 3, 4]


def test_republishing_an_old_version_keeps_the_newest_current(aws):
    rid, v1 = publish(docx_factory.runbook())
    _, v2 = publish(docx_factory.runbook(drop={"1.2"}), rid)
    assert service.get_runbook(rid)["latest"] == v2
    service.publish(v1, "boss")
    assert service.get_runbook(rid)["latest"] == v2


def test_a_version_in_use_cannot_be_remapped(aws):
    ids = setup_org()
    rid, vid = publish(docx_factory.runbook())
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(ids["p1"], airtel["id"], rid)
    progress.batch_version(org.get_batch(ids["A"]), org.get_project(ids["p1"], airtel["id"]))
    v = service.get_version(vid)
    with pytest.raises(Exception) as e:
        service.save_mapping(vid, v["mapping"], 2, [])
    assert "cannot change now" in str(e.value)
    with pytest.raises(Exception):
        service.publish(vid, "boss")


def test_a_file_that_cannot_be_read_ends_as_failed(aws):
    rid = service.create_runbook("Broken")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", "<not really xml")
    vid = service.add_version(rid, "broken.docx", buf.getvalue(), "boss")
    assert service.get_version(vid)["status"] == "failed"


def test_tree_cache_follows_a_new_publish(aws):
    rid, vid = publish(docx_factory.runbook())
    first = service.published(vid)
    service.set_version(vid, {"published_at": "2099-01-01T00:00:00.000Z"})     # published again elsewhere
    assert service._cache[vid][1] is first
    store.s3().put_object(Bucket=service.config.runbook_bucket(), Key=f"runbooks/{vid}/tree.json",
                          Body=store.s3().get_object(Bucket=service.config.runbook_bucket(),
                                                     Key=f"runbooks/{vid}/tree.json")["Body"].read())
    assert service.published(vid) is not first


# ------------------------------------------------------------------ restore

def test_export_carries_runbook_files_and_restore_puts_them_back(aws):
    rid, vid = publish(docx_factory.runbook())
    blob, manifest = export.build_zip()
    assert manifest["runbook_files"] == 1
    names = zipfile.ZipFile(io.BytesIO(blob)).namelist()
    assert f"files/uploads/{vid}.docx" in names and names.count("manifest.json") == 1
    store.s3().delete_object(Bucket=service.config.runbook_bucket(), Key=f"uploads/{vid}.docx")
    export.restore_zip(blob, say=lambda m: None)
    assert store.s3().get_object(Bucket=service.config.runbook_bucket(), Key=f"uploads/{vid}.docx")


def test_invitation_remakes_a_missing_sign_in_account(aws, monkeypatch):
    c = boto3.client("cognito-idp", region_name="ap-south-1")
    pool = c.create_user_pool(PoolName="staff", UsernameAttributes=["email"])["UserPool"]["Id"]
    c.create_group(UserPoolId=pool, GroupName="trainer")
    setup_org()                                            # made while no pool is set: no accounts exist
    monkeypatch.setenv("USER_POOL_ID", pool)
    msg = org.resend_invitation("trainer1@robomy.test")
    assert "made again" in msg
    user = c.admin_get_user(UserPoolId=pool, Username="trainer1@robomy.test")
    assert user["Username"]
    assert org.ensure_account(org.get_trainer("trainer1@robomy.test")) is False   # already there now


def test_student_address_behind_cloudfront(aws):
    from skillops.livedata.http import Request
    ev = {"rawPath": "/v1/health", "headers": {"x-skillops-viewer": "49.36.1.2"},
          "requestContext": {"http": {"method": "GET", "sourceIp": "130.176.0.1"}}}
    assert Request(ev).source_ip == "49.36.1.2"


def test_staff_object_for_progress_tests():
    assert Staff("a@b.c", "trainer", "A", {"x"}).batch_ids == {"x"}
