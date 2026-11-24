"""Roles: the supervisor views the runbook but does not mark it; trainers mark only their own batches."""
import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops.core import jobs, org
from skillops.runbook import detect, progress, service


@pytest.fixture
def world(aws):
    jobs.INLINE = True
    ids = setup_org()
    rid = service.create_runbook("Sample")
    vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=2, parts=2, actions=3), "boss")
    mapping, checkable = detect.suggest(service.get_version(vid)["patterns"])
    service.save_mapping(vid, mapping, checkable, [])
    service.publish(vid, "boss")
    jobs.INLINE = False
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(airtel["id"], rid)
    airtel = org.get_project(ids["p1"], airtel["id"])
    progress.batch_version(org.get_batch(ids["A"]), airtel)
    pub = service.published(vid)
    first = pub["checkables"][0]
    boss = Browser()
    boss.sign_in_local("boss@robomy.test")
    return dict(ids=ids, vid=vid, pub=pub, first=first, boss=boss, base=f"/b/{ids['A']}/p/{airtel['id']}")


@pytest.mark.parametrize("action", ["tick", "untick", "note"])
def test_supervisor_cannot_mark_the_runbook(world, action):
    w = world
    r = w["boss"].post(f"{w['base']}/i/{w['first']['id']}/{action}", {"text": "x", "on": "1"})
    assert r["statusCode"] == 403, body(r)[:300]
    assert "view the runbook but not mark it" in body(r)
    s = progress.summary(w["ids"]["A"], w["vid"])
    assert s["done"] == 0


def test_supervisor_class_view_has_no_marking_buttons(world):
    w = world
    page = w["boss"].get(f"{w['base']}/teach/{w['first']['parent']}")
    html = body(page)
    assert page["statusCode"] == 200
    assert "view only" in html
    assert 'data-js="tick-inline"' not in html
    assert "Mark done" not in html


def test_supervisor_detail_view_has_no_mark_or_note(world):
    w = world
    for path in (f"/i/{w['first']['id']}", "/intro"):
        html = body(w["boss"].get(w["base"] + path))
        assert "view only" in html
        assert "Mark complete" not in html and "Save note" not in html


def test_supervisor_still_sees_ticks_made_by_trainers(world):
    w = world
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    assert t.post(f"{w['base']}/i/{w['first']['id']}/tick", {})["statusCode"] in (200, 303)
    html = body(w["boss"].get(f"{w['base']}/teach/{w['first']['parent']}"))
    assert "1</span>/<span>3</span> done" in html
    assert 't-check on' in html


def test_batch_page_says_view_for_the_supervisor_and_teach_for_trainers(world):
    w = world
    boss = body(w["boss"].get(f"/b/{w['ids']['A']}/today"))
    assert "No session planned for today" in boss and "Plan today's session" not in boss
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    html = body(t.get(f"/b/{w['ids']['A']}/today"))
    assert "Plan today's session" in html and "View runbook" in html


def test_trainer_marks_their_own_batch(world):
    w = world
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    html = body(t.get(f"{w['base']}/teach/{w['first']['parent']}"))
    assert 'data-js="tick-inline"' in html and "view only" not in html
