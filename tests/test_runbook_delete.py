"""Deleting uploaded runbooks and versions: only when nobody uses them, with their files."""
import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops.core import config, jobs, org, store
from skillops.core.perms import Staff
from skillops.core.util import UserError
from skillops.runbook import detect, progress, service


def _published(rid):
    jobs.INLINE = True
    vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=1, parts=1, actions=2), "boss")
    mapping, checkable = detect.suggest(service.get_version(vid)["patterns"])
    service.save_mapping(vid, mapping, checkable, [])
    service.publish(vid, "boss")
    jobs.INLINE = False
    return vid


def _keys(vid):
    resp = store.s3().list_objects_v2(Bucket=config.runbook_bucket())
    return [o["Key"] for o in resp.get("Contents", []) if vid in o["Key"]]


@pytest.fixture
def boss(aws):
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    return b


def test_unused_version_is_deleted_with_its_files(boss):
    setup_org()
    rid = service.create_runbook("Sample")
    v1 = _published(rid)
    v2 = _published(rid)
    assert service.get_runbook(rid)["latest"] == v2 and _keys(v2)
    r = boss.post(f"/admin/runbooks/{rid}/v/{v2}/delete", {})
    assert r["statusCode"] == 303
    assert service.get_version(v2, required=False) is None
    assert _keys(v2) == []
    assert service.get_runbook(rid)["latest"] == v1            # "current" falls back to v1


def test_version_a_batch_uses_or_marked_is_kept(boss):
    ids = setup_org()
    rid = service.create_runbook("Sample")
    vid = _published(rid)
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(airtel["id"], rid)
    progress.batch_version(org.get_batch(ids["A"]), org.get_project(ids["p1"], airtel["id"]))
    with pytest.raises(UserError, match="batches are on it: Batch A"):
        service.delete_version(vid)
    r = boss.post(f"/admin/runbooks/{rid}/v/{vid}/delete", {})
    assert r["statusCode"] == 400 and service.get_version(vid)
    # ticks are history: after the batch moves on, the old version still stays
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    progress.tick(ids["A"], vid, service.published(vid)["checkables"][0]["id"], t1)
    v2 = _published(rid)
    progress.move_batch(org.get_batch(ids["A"]), org.get_project(ids["p1"], airtel["id"]), v2, t1)
    assert "ticks or notes" in " ".join(service.version_blockers(vid))


def test_runbook_delete_needs_its_name_and_no_project(boss):
    ids = setup_org()
    rid = service.create_runbook("Sample")
    vid = _published(rid)
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(airtel["id"], rid)
    page = body(boss.get(f"/admin/runbooks/{rid}"))
    assert "cannot be deleted while it is in use" in page
    assert boss.post(f"/admin/runbooks/{rid}/delete", {"confirm_name": "Sample"})["statusCode"] == 400
    org.set_project_runbook(airtel["id"], "")
    assert boss.post(f"/admin/runbooks/{rid}/delete", {"confirm_name": "wrong"})["statusCode"] == 400
    r = boss.post(f"/admin/runbooks/{rid}/delete", {"confirm_name": "sample"})
    assert r["statusCode"] == 303
    assert service.get_runbook(rid, required=False) is None and service.get_version(vid, required=False) is None
    assert _keys(vid) == []


def test_trainer_cannot_delete(aws):
    setup_org()
    rid = service.create_runbook("Sample")
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    assert t.post(f"/admin/runbooks/{rid}/delete", {"confirm_name": "Sample"})["statusCode"] == 403


def test_one_click_accept_keeps_the_structure_and_remembers_it(boss):
    setup_org()
    rid = service.create_runbook("Sample")
    jobs.INLINE = True
    vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=1, parts=1, actions=2), "boss")
    jobs.INLINE = False
    before = service.get_version(vid)
    page = body(boss.get(f"/admin/runbooks/{rid}/v/{vid}/map"))
    assert "Confirm the structure" in page and 'class="wizard"' in page
    r = boss.post(f"/admin/runbooks/{rid}/v/{vid}/map/accept", {})
    assert r["statusCode"] == 303 and r["headers"]["location"].endswith("/preview")
    after = service.get_version(vid)
    assert after["mapping"] == before["mapping"] and after["checkable"] == before["checkable"]
    assert [t["name"] for t in service.list_templates()] == ["Sample"]
    # the next upload is laid out by that template on its own
    jobs.INLINE = True
    v2 = service.add_version(rid, "y.docx", docx_factory.runbook(steps=2, parts=1, actions=2), "boss")
    jobs.INLINE = False
    assert service.get_version(v2)["mapping"] == after["mapping"]
