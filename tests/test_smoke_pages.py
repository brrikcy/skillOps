"""Every page opens, for the supervisor and for a trainer, with full sample data
(a published runbook, ticks, a live-data feed and join code, the fake MCQ system)."""
import re

import pytest

import docx_factory
from helpers import Browser, body
from skillops import web
from skillops.core import jobs, org
from skillops.mcq import fake
from skillops.runbook import service


@pytest.fixture
def world(aws, tmp_path):
    import os
    import demo
    os.environ.setdefault("SIMLAB_SECRET", "test-secret")
    fake.reset()
    path = tmp_path / "sample.docx"
    path.write_bytes(docx_factory.runbook())
    ids = demo.seed_all(say=lambda m: None, runbook=str(path))
    return ids


def all_get_paths(ids):
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    rid = service.list_runbooks()[0]["id"]
    vid = service.list_versions(rid)[0]["id"]
    item = service.published(vid)["checkables"][0]["id"]
    trainer = "trainer1%40local.test"
    return {
        "super": ["/dashboard", "/dashboard?programme=all&institution=all", "/dashboard?format=csv",
                  f"/dashboard/b/{ids['A']}", "/admin/institutions", f"/admin/institutions/{ids['i1']}",
                  "/admin/programmes", f"/admin/programmes/{ids['p1']}", "/admin/batches", f"/admin/batches/{ids['A']}",
                  "/admin/trainers", f"/admin/trainers/{trainer}", "/admin/import", "/admin/audit", "/admin/audit?format=csv",
                  "/admin/export", "/health", "/admin/runbooks", f"/admin/runbooks/{rid}",
                  f"/admin/runbooks/{rid}/v/{vid}/map", f"/admin/runbooks/{rid}/v/{vid}/preview",
                  f"/admin/runbooks/{rid}/v/{vid}/i/{item}", "/admin/live", "/admin/mcq"],
        "trainer": ["/", f"/b/{ids['A']}", f"/b/{ids['A']}/tools", f"/b/{ids['A']}/recap",
                    f"/b/{ids['A']}/recap?date=2026-01-01", f"/b/{ids['A']}/p/{airtel['id']}/teach/{service.published(vid)['checkables'][0]['parent']}", f"/b/{ids['A']}/p/{airtel['id']}/i/{item}",
                    f"/b/{ids['A']}/p/{airtel['id']}/i/{item}?present=1", f"/b/{ids['A']}/p/{airtel['id']}/search?q=Action",
                    f"/b/{ids['A']}/p/{airtel['id']}/intro", f"/b/{ids['A']}/live", f"/b/{ids['A']}/mcq",
                    f"/b/{ids['A']}/mcq/monitor/W02", f"/b/{ids['A']}/mcq/results/W01", f"/b/{ids['A']}/mcq/register",
                    f"/dashboard/b/{ids['A']}", "/dashboard"],
        "public": ["/live", "/register", "/docs", "/status", "/public-config.js", "/v1/health", "/v1/status",
                   "/static/style.css", "/static/skillops-logo.svg", "/static/icons.svg"],
    }


def test_every_page_opens(world):
    paths = all_get_paths(world)
    boss = Browser()
    import os
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    boss.sign_in_local("boss@robomy.test")
    for p in paths["super"]:
        r = boss.get(p)
        assert r["statusCode"] == 200, (p, r["statusCode"], body(r)[:300])
    t = Browser()
    t.sign_in_local("trainer1@local.test")
    for p in paths["trainer"]:
        r = t.get(p)
        assert r["statusCode"] == 200, (p, r["statusCode"], body(r)[:300])
    anon = Browser()
    for p in paths["public"]:
        r = anon.get(p)
        assert r["statusCode"] == 200, (p, r["statusCode"])


def test_pages_show_both_logos_and_no_by_robomy_text(world):
    import os
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    for p in ("/dashboard", "/admin/runbooks", "/signin"):
        html = body(b.get(p)) if p != "/signin" else body(Browser().get(p))
        assert 'alt="Robomy"' in html and 'alt="SkillOps"' in html and "skillops-logo.svg" in html
        assert "by Robomy" not in html
        assert not re.search(r"<script>[^<]", html)                  # no inline scripts anywhere


def test_no_schedule_words_on_progress_pages(world):
    import os
    os.environ["LOCAL_SUPERADMINS"] = "boss@robomy.test"
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    for p in ("/dashboard?programme=all&institution=all", f"/dashboard/b/{world['A']}", f"/b/{world['A']}"):
        text = re.sub(r"<[^>]+>", " ", body(b.get(p))).lower()
        for word in ("behind", "on track", "late", "days left", "due date", "overdue"):
            assert word not in text, (p, word)


def test_route_count(world):
    assert len(web.ROUTES) > 90
    assert jobs.RUNNERS.keys() >= {"export", "runbook_read", "runbook_publish"}
