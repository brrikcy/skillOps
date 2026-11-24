"""The class view, "continue where the class is", repeat flags, class recap, milestones and trainer activity."""
import json

import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops import dashboard
from skillops.core import jobs, org, util
from skillops.core.perms import Staff
from skillops.pages.teach import recap_for, recap_text
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
    code = {n["code"]: n for n in pub["checkables"]}
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    return dict(ids=ids, vid=vid, pub=pub, code=code, t1=t1, airtel=airtel, base=f"/b/{ids['A']}/p/{airtel['id']}")


def test_continue_goes_to_where_the_class_is_not_to_a_gap(world):
    w = world
    for c in ("1.1", "1.2", "1.4"):                      # 1.3 was skipped on purpose
        progress.tick(w["ids"]["A"], w["vid"], w["code"][c]["id"], w["t1"])
    s = progress.summary(w["ids"]["A"], w["vid"])
    assert s["first_open"]["code"] == "1.3"              # 1.3 is still open ...
    assert s["next_up"]["code"] == "1.5"                 # ... but the class continues at 1.5
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    r = t.get(w["base"] + "/teach")
    assert r["headers"]["location"] == f"{w['base']}/teach/{w['code']['1.5']['parent']}#a-{w['code']['1.5']['id']}"


def test_class_view_shows_a_whole_section_and_ticks_in_place(world):
    w = world
    section = w["code"]["1.1"]["parent"]
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"{w['base']}/teach/{section}")
    html = body(page)
    assert page["statusCode"] == 200 and "data-columns" in html
    for c in ("1.1", "1.2", "1.3"):                      # the part's three actions, all on one screen
        assert f'id="a-{w["code"][c]["id"]}"' in html
    assert w["code"]["1.4"]["id"] not in html            # the next part is a separate section
    assert "0</span>/<span>3</span> done" in html
    r = t.post(f"{w['base']}/i/{w['code']['1.2']['id']}/tick", {},
               headers={"accept": "application/json", "x-requested-with": "fetch"})
    assert json.loads(r["body"])["state"] == "done"
    assert "1</span>/<span>3</span> done" in body(t.get(f"{w['base']}/teach/{section}"))
    step = w["pub"]["index"][section]["parent"]          # opening a whole step goes to its first section
    assert t.get(f"{w['base']}/teach/{step}")["headers"]["location"].endswith(f"/teach/{section}")
    other = Browser()
    other.sign_in_local("trainer2@robomy.test")
    assert other.get(f"{w['base']}/teach/{section}")["statusCode"] == 403


def test_there_is_no_repeat_flag_or_gap_list(world):
    w = world
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = body(t.get(f"{w['base']}/teach/{w['code']['1.1']['parent']}"))
    assert "repeat-inline" not in page and "Needs a repeat" not in page
    assert t.post(f"{w['base']}/i/{w['code']['1.2']['id']}/repeat", {"on": "1"})["statusCode"] == 404
    s = progress.summary(w["ids"]["A"], w["vid"])
    assert "repeats" not in s and "skipped" not in s


def test_class_recap_lists_today_and_the_next_item(world):
    w = world
    for c in ("1.1", "1.2"):
        progress.tick(w["ids"]["A"], w["vid"], w["code"][c]["id"], w["t1"])
    from datetime import datetime
    today = datetime.fromtimestamp(util.now_ts(), util.IST).strftime("%Y-%m-%d")
    groups = recap_for(w["ids"]["A"], today)
    assert [n["code"] for n, _ in groups[0]["covered"]] == ["1.1", "1.2"]
    assert groups[0]["next"]["code"] == "1.3"
    text = recap_text(org.batch_context(w["ids"]["A"]), today, groups)
    assert "Airtel: today we covered" in text and "• 1.1 Action 1.1 title" in text
    assert "Next class starts with: 1.3 Action 1.3 title" in text and "by Robomy" not in text
    assert recap_for(w["ids"]["A"], "2020-01-01") == []
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"/b/{w['ids']['A']}/recap")
    assert page["statusCode"] == 200 and "Copy" in body(page) and "1.2 Action 1.2 title" in body(page)


def test_milestones_and_trainer_activity_come_from_ticks(world):
    w = world
    step1 = [n for n in w["pub"]["checkables"] if n["code"].startswith("1.")]
    for n in step1:
        progress.tick(w["ids"]["A"], w["vid"], n["id"], w["t1"])
    progress.untick(w["ids"]["A"], w["vid"], step1[0]["id"], w["t1"])
    progress.tick(w["ids"]["A"], w["vid"], step1[0]["id"], w["t1"])
    hl = dashboard.highlights(dashboard.batch_rows(org.scope(None)))
    assert [(m["batch"]["name"], m["kind"]) for m in hl["milestones"]] == [("Batch A", "step")]
    assert hl["milestones"][0]["title"].startswith("STEP 1")
    assert hl["activity"] == [{"name": "Trainer 1", "delivered": 6, "batches": ["Batch A"], "last": hl["activity"][0]["last"]}]


def test_tools_hub_groups_live_data_and_mcq(world):
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"/b/{world['ids']['A']}/tools")
    html = body(page)
    assert page["statusCode"] == 200 and "Live Data Simulator" in html and "MCQ tests" in html
    assert "Not connected for this institution yet." in html
    assert 'class="on"' in html and ">Tools<" in html.replace("</svg>", "")
