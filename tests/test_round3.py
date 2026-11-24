"""Class days, teaching from the day's plan, the reader, deletes, join codes, big local uploads."""
import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops import classdays, planner
from skillops.core import jobs, org
from skillops.core.http import _split_multipart
from skillops.core.perms import Staff
from skillops.runbook import detect, progress, service


@pytest.fixture
def world(aws):
    jobs.INLINE = True
    ids = setup_org()
    rid = service.create_runbook("Sample")
    vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=2, parts=2, actions=2), "boss")
    mapping, checkable = detect.suggest(service.get_version(vid)["patterns"])
    service.save_mapping(vid, mapping, checkable, [])
    service.publish(vid, "boss")
    jobs.INLINE = False
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(airtel["id"], rid)
    pub = service.published(vid)
    boss = Browser()
    boss.sign_in_local("boss@robomy.test")
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    return dict(ids=ids, vid=vid, pub=pub, jid=airtel["id"], boss=boss, t=t, A=ids["A"],
                t1=Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]}))


# ------------------------------------------------------------------ class days

def test_class_days_follow_weekdays_off_days_and_extras(world):
    w = world
    r = w["boss"].post(f"/admin/batches/{w['A']}/classdays",
                       {"wd": ["0", "1", "3"], "start": "2026-11-01", "end": "2026-11-30",
                        "off": "2026-11-17", "extra": "2026-11-21"})
    assert r["statusCode"] == 303
    cfg = classdays.get(org.get_batch(w["A"]))
    assert cfg["weekdays"] == [0, 1, 3]
    assert classdays.is_class_day(cfg, "2026-11-16")          # Monday
    assert not classdays.is_class_day(cfg, "2026-11-18")      # Wednesday
    assert not classdays.is_class_day(cfg, "2026-11-17")      # a Tuesday, but a day off
    assert classdays.is_class_day(cfg, "2026-11-21")          # a Saturday added
    assert not classdays.is_class_day(cfg, "2026-12-01")      # after the last day
    assert classdays.next_class_days(cfg, "2026-11-16", 3) == ["2026-11-16", "2026-11-19", "2026-11-21"]
    assert w["boss"].post(f"/admin/batches/{w['A']}/classdays", {"wd": "0", "off": "25-12-2026"})["statusCode"] == 400
    assert w["t"].post(f"/admin/batches/{w['A']}/classdays", {"wd": "0"})["statusCode"] == 403


def test_calendar_marks_class_days_and_plans(world):
    w = world
    classdays.save(w["A"], {"start": "", "end": "", "off": "", "extra": ""}, ["0", "1", "3"])
    planner.save(w["A"], "2026-11-16", [{"kind": "item", "proj": w["jid"], "node": w["pub"]["checkables"][0]["id"]}],
                 "", 0, w["t1"])
    html = body(w["t"].get(f"/b/{w['A']}/calendar?m=2026-11"))
    assert "November 2026" in html
    assert f"/plan?date=2026-11-16" in html and "✓ 1" in html                 # planned class day
    assert f"/plan?date=2026-11-17" in html                                   # Tuesday: a class day
    assert f"/plan?date=2026-11-18" not in html                               # Wednesday: no class


# ------------------------------------------------------------------ teaching from the plan

def test_teach_opens_todays_plan_and_only_the_planned_parts(world, monkeypatch):
    w = world
    from skillops.pages import teach
    monkeypatch.setattr(teach, "_ist_today", lambda: "2026-11-16")
    r = w["t"].get(f"/b/{w['A']}/teach-today")
    assert r["headers"]["location"].startswith(f"/b/{w['A']}/p/{w['jid']}/plan?date=2026-11-16")   # no plan yet
    acts = w["pub"]["checkables"]
    sec = progress.sections(w["pub"])[1]
    planner.save(w["A"], "2026-11-16", [{"kind": "item", "proj": w["jid"], "node": acts[0]["id"]},
                                        {"kind": "text", "text": "Short break"},
                                        {"kind": "item", "proj": w["jid"], "node": sec["id"]}], "", 0, w["t1"])
    r = w["t"].get(f"/b/{w['A']}/teach-today")
    assert r["headers"]["location"] == f"/b/{w['A']}/session?date=2026-11-16"
    html = body(w["t"].get(r["headers"]["location"]))
    wanted = [acts[0]] + progress.section_items(w["pub"], sec)
    for n in wanted:
        assert f'id="a-{n["id"]}"' in html
    assert f'id="a-{acts[1]["id"]}"' not in html                              # not planned: not shown
    assert "Short break" in html and html.count('data-js="tick-inline"') == len(wanted)
    boss = body(w["boss"].get(f"/b/{w['A']}/session?date=2026-11-16"))
    assert "view only" in boss and 'data-js="tick-inline"' not in boss


def test_supervisor_chooses_the_current_project(world):
    w = world
    r = w["boss"].post(f"/admin/batches/{w['A']}/current-project", {"project": w["jid"]})
    assert r["statusCode"] == 303 and org.get_batch(w["A"])["current_project"] == w["jid"]
    html = body(w["t"].get(f"/b/{w['A']}"))
    assert "Next up · Airtel" in html and "Now: <strong>Airtel</strong>" in html
    assert 'action="/admin/batches/' not in html                                 # only the supervisor chooses
    assert w["t"].post(f"/admin/batches/{w['A']}/current-project", {"project": ""})["statusCode"] == 403


def test_reader_shows_one_section_with_every_section_one_click_away(world):
    w = world
    secs = progress.sections(w["pub"])
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/read?part={secs[1]['id']}"))
    assert 'class="rv-nav"' in html and all(f'data-goto="{s["id"]}"' in html for s in secs)
    assert "Previous" in html and "Next" in html
    assert 'data-plan-add' not in html and "Session planner</h1>" not in html          # reading only
    frag = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan/part/{secs[2]['id']}?mode=read"))
    assert "data-plan-add" not in frag


# ------------------------------------------------------------------ deletes

def test_delete_batch_takes_its_ticks_and_plans(world):
    w = world
    progress.tick(w["A"], w["vid"], w["pub"]["checkables"][0]["id"], w["t1"])
    planner.save(w["A"], "2026-11-16", [{"kind": "text", "text": "x"}], "", 0, w["t1"])
    assert w["boss"].post(f"/admin/batches/{w['A']}/delete", {"confirm_name": "wrong"})["statusCode"] == 400
    r = w["boss"].post(f"/admin/batches/{w['A']}/delete", {"confirm_name": "batch a"})
    assert r["statusCode"] == 303
    assert org.get_batch(w["A"], required=False) is None
    assert progress.ticks(w["A"], w["vid"]) == {} and planner.list_dates(w["A"]) == []
    assert w["A"] not in org.trainer_batch_ids("trainer1@robomy.test")


def test_delete_institution_programme_and_trainer(world):
    w = world
    ids = w["ids"]
    r = w["boss"].post(f"/admin/programmes/{ids['p2']}/delete", {"confirm_name": "Data Analyst Bootcamp"})
    assert r["statusCode"] == 303 and org.get_batch(ids["b2"], required=False) is None
    r = w["boss"].post(f"/admin/institutions/{ids['i2']}/delete", {"confirm_name": "Second College"})
    assert r["statusCode"] == 303 and org.get_institution(ids["i2"], required=False) is None
    r = w["boss"].post("/admin/trainers/trainer3%40robomy.test/delete", {"confirm_name": "Trainer 3"})
    assert r["statusCode"] == 303 and org.get_trainer("trainer3@robomy.test", required=False) is None
    r = w["boss"].post(f"/admin/projects/{w['jid']}/delete", {})
    assert r["statusCode"] == 303 and org.get_project(ids["p1"], w["jid"], required=False) is None
    assert w["t"].post(f"/admin/batches/{ids['B']}/delete", {"confirm_name": "Batch B"})["statusCode"] == 403


# ------------------------------------------------------------------ Live Data Simulator

def test_closed_join_codes_disappear(world):
    from skillops.livedata import ops, students
    w = world
    c = ops.create_code(w["A"], 40, 30, False, "trainer1@robomy.test")["code"]
    assert [x["code"] for x in ops.batch_codes(w["A"])] == [c]
    w["t"].post(f"/b/{w['A']}/live/codes/close", {"code": c})
    assert ops.batch_codes(w["A"]) == [] and students.get_code(c) is None


def test_delete_a_dataset_and_a_feed(world):
    import os
    import sqlite3
    import tempfile
    from skillops.livedata import catalog, imports
    w = world
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER, at TEXT)")
    con.executemany("INSERT INTO t VALUES (?, ?)", [(i, f"2025-07-01 0{i % 9}:00:00") for i in range(30)])
    con.commit()
    con.close()
    data = open(path, "rb").read()
    os.remove(path)
    jobs.INLINE = True
    try:
        iid = imports.start("shop.db", "boss", data=data)
        imports.load(iid, {"feed_new": "shop", "data_start": "2025-07-01", "go_live": "2026-11-16 09:00",
                           "use0": "1", "name0": "shop_t", "rule0": "time", "time0": "at"}, "boss")
        assert catalog.get_dataset("shop_t")["row_count"] == 30
        assert w["boss"].post("/admin/live/delete", {"dataset": "shop_t"})["statusCode"] == 303
        assert catalog.get_dataset("shop_t") is None
        from skillops.livedata.store import t as live_t
        assert live_t("ROWS").scan()["Items"] == []
        assert w["boss"].post("/admin/live/delete", {"feed": "shop", "confirm_name": "nope"})["statusCode"] == 400
        assert w["boss"].post("/admin/live/delete", {"feed": "shop", "confirm_name": "shop"})["statusCode"] == 303
        assert catalog.get_feed("shop") is None
    finally:
        jobs.INLINE = False


def test_staff_are_sent_away_from_the_students_register_page(world):
    w = world
    r = w["boss"].get("/register")
    assert r["statusCode"] == 303 and r["headers"]["location"] == "/"
    assert Browser().get("/register")["statusCode"] == 200


def test_big_uploads_are_read_without_the_email_parser():
    blob = b"\x00\x01PK" * (2 * 1024 * 1024)                    # 8 MB of binary
    body_ = (b"--XyZ\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\nabc\r\n"
             b"--XyZ\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.db\"\r\n"
             b"Content-Type: application/octet-stream\r\n\r\n" + blob + b"\r\n--XyZ--\r\n")
    parts = {n: (fn, d) for n, fn, _, d in _split_multipart(body_, "multipart/form-data; boundary=XyZ")}
    assert parts["csrf"] == (None, b"abc") and parts["file"][0] == "big.db" and parts["file"][1] == blob


# ------------------------------------------------------------------ the trainer's batch bar; the supervisor's menu

def test_trainer_with_several_batches_opens_on_overview(world):
    w = world
    org.associate("trainer1@robomy.test", w["ids"]["B"])                   # now two batches
    w["t"].sign_in_local("trainer1@robomy.test")
    r = w["t"].get("/")
    home = body(r)
    assert r["statusCode"] == 200 and "Overview" in home
    assert home.count('class="seg-bar"') == 2 and 'class="today-strip' in home      # a bar and today's line per batch
    assert 'href="/" class="batchbar-home on"' in home and ">Overview</a>" in home
    assert w["t"].get("/dashboard")["headers"]["location"] == "/"          # a trainer's dashboard is their home
    html = body(w["t"].get(f"/b/{w['ids']['B']}"))
    assert 'class="batchbar"' in html and 'id="side-menu"' not in html and "data-nav-toggle" not in html
    bar = html.split('class="batchbar"', 1)[1].split("</nav>", 1)[0]
    assert f'href="/b/{w["A"]}"' in bar and f'href="/b/{w["ids"]["B"]}" class="on"' in bar
    assert 'href="/team"' in bar
    assert 'class="trail"' not in html                                      # a batch is a top page


def test_same_batch_names_are_told_apart_in_the_bar(world):
    w = world
    org.add_trainer_to_institution("trainer1@robomy.test", w["ids"]["i2"])
    org.associate("trainer1@robomy.test", w["ids"]["b2"])
    b2 = org.get_batch(w["ids"]["b2"])
    org.store.update("CORE", f"BATCH#{b2['id']}", "META", {"name": "Batch A"})
    w["t"].sign_in_local("trainer1@robomy.test")
    bar = body(w["t"].get(f"/b/{w['A']}")).split('class="batchbar"', 1)[1].split("</nav>", 1)[0]
    assert "Batch A · DBI OJT Sem 3 - Data Analyst" in bar and "Batch A · Data Analyst Bootcamp" in bar


def test_supervisor_keeps_the_side_menu_and_its_button(world):
    w = world
    html = body(w["boss"].get("/dashboard"))
    assert "data-nav-toggle" in html and 'id="side-menu"' in html and 'class="batchbar"' not in html
    w["boss"].cookies["so_nav"] = "closed"
    assert "nav-closed" in body(w["boss"].get("/dashboard")).split("<body", 1)[1].split(">", 1)[0]


# ------------------------------------------------------------------ Progress: the batch's first tab and landing page

def test_batch_opens_on_progress_with_charts_and_folded_lists(world):
    w = world
    acts = w["pub"]["checkables"]
    for n in acts[:3]:
        progress.tick(w["A"], w["vid"], n["id"], w["t1"])
    assert w["t"].get("/")["headers"]["location"] == f"/b/{w['A']}"      # one batch: it is their home
    html = body(w["t"].get(f"/b/{w['A']}"))
    tabs = html.split('<nav class="tabs">', 1)[1].split("</nav>", 1)[0]
    assert tabs.index("Progress") < tabs.index("Today") < tabs.index("Calendar") < tabs.index("Tools")
    assert f'href="/b/{w["A"]}" class="on"' in tabs and f'href="/b/{w["A"]}/today"' in tabs
    assert 'batch-hero' in html and 'class="seg-bar"' in html and 'hero-figs' not in html
    assert 'class="colchart"' in html and html.count('class="cc-col"') == 28
    assert "Items marked done per day" in html and ": 3 marked done" in html          # today's column
    assert '<details name="prog-more">' not in html and "Gaps" not in html and "Needs a repeat" not in html   # nothing folded, no gaps or repeats
    assert 'class="timeline"' in html and html.index("Where the class is") < html.index("Trainer notes</h2>")   # notes under it
    assert html.index("Recent activity</h2>") < html.index('class="colchart"')                                   # recent activity above the graph
    assert html.count('pane-body') == 1
    assert 'name="prog-steps"' in html and "data-fit" in html


def test_activity_counts_ticks_per_day_and_not_unticks(world):
    w = world
    from skillops import dashboard
    from skillops.core import util
    acts = w["pub"]["checkables"]
    progress.tick(w["A"], w["vid"], acts[0]["id"], w["t1"])
    util.set_fake_now(util.now_ts() - 3 * 86400)
    progress.tick(w["A"], w["vid"], acts[1]["id"], w["t1"])
    progress.tick(w["A"], w["vid"], acts[2]["id"], w["t1"])
    util.set_fake_now(None)
    progress.untick(w["A"], w["vid"], acts[2]["id"], w["t1"])
    a = dashboard.activity(w["A"])
    assert len(a) == 28 and a[-1]["count"] == 1 and a[-4]["count"] == 2 and sum(x["count"] for x in a) == 3


def test_old_progress_address_and_csv_still_work(world):
    w = world
    r = w["boss"].get(f"/dashboard/b/{w['A']}")
    assert r["statusCode"] == 303 and r["headers"]["location"] == f"/b/{w['A']}"
    r = w["boss"].get(f"/dashboard/b/{w['A']}?format=csv")
    assert r["headers"]["location"] == f"/b/{w['A']}?format=csv"
    import base64
    csv = w["t"].get(f"/b/{w['A']}?format=csv")
    assert csv["statusCode"] == 200 and "Project,Items done" in base64.b64decode(body(csv)).decode("utf-8-sig")
