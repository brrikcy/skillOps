"""Back button, archived things shown in red, trainers grouped by institution, the Live Data Simulator name."""
from helpers import Browser, body, setup_org
from skillops import web
from skillops.core import org
from skillops.core.perms import Staff


def _boss():
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    return b


def test_back_goes_to_the_page_above(aws):
    ids = setup_org()
    boss = Staff("boss@robomy.test", "superadmin", "Boss", set())
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    t2 = Staff("trainer2@robomy.test", "trainer", "Trainer 2", {ids["A"], ids["B"]})
    proj = org.list_projects(ids["p1"])[0]["id"]
    assert web.parent_of("/dashboard", boss) is None                     # menu pages: no Back
    assert web.parent_of("/monitor/courses", boss) is None               # monitoring menu pages: no Back
    assert web.parent_of("/settings", boss) is None
    assert web.parent_of("/admin/trainers", boss) == "/settings"         # Settings > Trainers
    assert web.parent_of("/admin/trainers/x%40y.z", boss) == "/admin/trainers"
    assert web.parent_of(f"/admin/batches/{ids['A']}", boss) == "/admin/batches"
    assert web.parent_of(f"/dashboard/b/{ids['A']}", boss) == "/dashboard"
    assert web.parent_of(f"/b/{ids['A']}", boss) == "/dashboard"
    assert web.parent_of(f"/admin/batches/{ids['A']}/live", boss) == f"/admin/batches/{ids['A']}"
    assert web.parent_of(f"/b/{ids['A']}", t1) is None                  # a trainer's batch is a top page
    assert web.parent_of(f"/b/{ids['A']}", t2) is None
    assert web.parent_of(f"/b/{ids['A']}/p/{proj}/i/n12", t1) == f"/b/{ids['A']}"   # skips "continue here"
    assert web.parent_of(f"/b/{ids['A']}/live/student", t1) == f"/b/{ids['A']}/live"
    assert web.parent_of(f"/b/{ids['A']}/mcq/monitor/W01", t1) == f"/b/{ids['A']}/mcq"


def test_trail_links_every_level_above(aws):
    ids = setup_org()
    b = _boss()
    html = body(b.get(f"/admin/batches/{ids['A']}"))
    assert 'class="trail"' in html and 'href="/admin/batches">Batches</a>' in html
    assert 'href="/settings">Settings</a>' in body(b.get("/admin/institutions"))  # Settings > Institutions
    assert body(b.get("/monitor/courses")).count('class="trail"') == 0          # monitoring menu pages: no trail
    boss = Staff("boss@robomy.test", "superadmin", "Boss", set())
    proj = org.list_projects(ids["p1"])[0]
    trail = web.breadcrumbs(f"/b/{ids['A']}/p/{proj['id']}/i/n12", boss, "Some item")
    assert [label for label, _ in trail] == ["Dashboard", "Batch A", proj["name"], "Some item"]
    assert [href for _, href in trail][:2] == ["/dashboard", f"/b/{ids['A']}"] and trail[-1][1] is None


def test_archived_rows_are_red_when_shown(aws):
    ids = setup_org()
    org.set_archived("batch", ids["C"], True)
    b = _boss()
    hidden = body(b.get("/admin/batches"))
    assert "Batch C" not in hidden
    shown = body(b.get("/admin/batches?archived=1"))
    assert 'class="row-archived"' in shown and 'badge archived' in shown
    page = body(b.get(f"/admin/batches/{ids['C']}"))
    assert "archived-banner" in page


def test_trainers_list_shows_their_institutions(aws):
    ids = setup_org()
    org.create_trainer("t9@robomy.test", "Trainer Nine", ids["i2"])
    org.associate("t9@robomy.test", ids["b2"])
    org.add_trainer_to_institution("trainer1@robomy.test", ids["i2"])
    org.associate("trainer1@robomy.test", ids["b2"])          # trainer 1 also teaches at the second college
    org.create_trainer("t0@robomy.test", "Trainer Zero")      # in no institution yet
    people = {t["name"]: t for t in org.trainer_directory()}
    assert [i["name"] for i in people["Trainer 1"]["institutions"]] == ["DBI Skill Park", "Second College"]
    assert [b["name"] for b in people["Trainer 1"]["batches"]] == ["Batch A", "Morning"]
    assert people["Trainer Zero"]["institutions"] == []
    b = _boss()
    page = body(b.get(f"/admin/trainers?inst={ids['i2']}"))
    assert "Trainer Nine" in page and "Trainer 1" in page and "Trainer 2" not in page
    assert "Trainer Zero" in body(b.get("/admin/trainers?inst=none"))
    inst_page = body(b.get(f"/admin/institutions/{ids['i2']}"))
    assert "Trainer Nine" in inst_page and "Or add a new trainer" in inst_page


def test_trainer_page_adds_and_removes_institutions(aws):
    ids = setup_org()
    b = _boss()
    r = b.post("/admin/trainers/trainer2%40robomy.test/institutions", {"inst_id": ids["i2"]})
    assert r["statusCode"] == 303 and org.trainer_institution_ids("trainer2@robomy.test") == {ids["i1"], ids["i2"]}
    page = body(b.get("/admin/trainers/trainer2%40robomy.test"))
    assert "Morning" in page                                   # the second college's batch can now be linked
    b.post("/admin/trainers/trainer2%40robomy.test/institutions/remove", {"inst_id": ids["i1"]})
    assert org.trainer_batch_ids("trainer2@robomy.test") == set()     # unlinked from Batch B at DBI


def test_the_tool_is_called_live_data_simulator(aws):
    setup_org()
    page = body(_boss().get("/admin/live"))
    assert "Live Data Simulator" in page and "SkillOps Live Data" not in page


def test_team_progress_compares_without_opening_other_batches(aws):
    import docx_factory
    from skillops.core import jobs
    from skillops.runbook import detect, progress, service
    ids = setup_org()
    jobs.INLINE = True
    rid = service.create_runbook("Sample")
    vid = service.add_version(rid, "x.docx", docx_factory.runbook(steps=1, parts=1, actions=3), "boss")
    mapping, checkable = detect.suggest(service.get_version(vid)["patterns"])
    service.save_mapping(vid, mapping, checkable, [])
    service.publish(vid, "boss")
    jobs.INLINE = False
    airtel = next(j for j in org.list_projects(ids["p1"]) if j["name"] == "Airtel")
    org.set_project_runbook(airtel["id"], rid)
    acts = service.published(vid)["checkables"]
    t1 = Staff("trainer1@robomy.test", "trainer", "Trainer 1", {ids["A"]})
    t2 = Staff("trainer2@robomy.test", "trainer", "Trainer 2", {ids["B"]})
    for n in acts[:2]:
        progress.tick(ids["A"], vid, n["id"], t1)
    progress.tick(ids["B"], vid, acts[0]["id"], t2)
    from skillops import dashboard
    t = dashboard.team(t1)
    assert [g["programme"]["name"] for g in t["programmes"]] == ["DBI OJT Sem 3 - Data Analyst"]   # not the other college
    g = t["programmes"][0]
    rows = {r["batch"]["name"]: r for r in g["rows"]}
    assert set(rows) == {"Batch A", "Batch B", "Batch C"}
    assert rows["Batch A"]["mine"] and not rows["Batch B"]["mine"]
    assert rows["Batch A"]["recent"] == 2 and rows["Batch B"]["recent"] == 1
    assert g["me"]["delivered"] == 2 and g["others"]["delivered"] == 0   # (1 + 0) / 2 others rounds to 0
    assert {v["name"] for v in g["people"]} == {"Trainer 1", "Trainer 2", "Trainer 3"}
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    html = body(b.get("/team"))
    assert "Team progress" in html and ">You<" in html
    assert f'href="/b/{ids["A"]}"' in html and f'href="/b/{ids["B"]}"' not in html
    assert b.get(f"/b/{ids['B']}")["statusCode"] == 403
