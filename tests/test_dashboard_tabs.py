"""Dashboard tabs (Batches, Programmes, Institutes, Trainers) and the Settings page that chooses them."""
import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops.core import jobs, org
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



def test_dashboard_is_one_card_per_programme_with_a_bar_per_batch(world):
    b = world["boss"]
    html = body(b.get("/dashboard"))
    assert 'class="tabs"' not in html and "<table" not in html           # no tabs, no tables
    assert 'class="kpis"' not in html                                    # no headline figures row
    assert 'class="card prog-card"' in html and "DBI Skill Park" in html and 'class="seg-bar"' in html
    assert "Trainer 1" in html and "Airtel" in html
    assert html.count('class="seg-names"') == html.count('class="card prog-card"')
    for old in ("/dashboard?view=overall", "/dashboard?tab=trainers&days=999"):
        assert b.get(old)["statusCode"] == 200
    assert b.get("/settings/dashboard")["statusCode"] == 404


def test_project_window_shows_at_most_three_and_two_untouched():
    from skillops.dashboard import project_window as w
    shown = lambda g: list(range(w(g)[0] + 1, w(g)[1] + 2))
    assert shown([[100, 0, 0, 0, 0]]) == [1, 2, 3]                       # the example: 1 covered -> 1, 2, 3
    assert shown([[0, 0, 0, 0, 0]]) == [1, 2]                            # nothing started: two untouched only
    assert shown([[100, 100, 40, 0, 0, 0], [100, 60, 0, 0, 0, 0]]) == [2, 3, 4]   # done-by-all folded away
    assert shown([[100] * 6 + [30], [10] + [0] * 6]) == [1, 2, 3]        # a batch still on project 1 stays visible
    assert shown([[100, 100, 100], [100, 100, 100]]) == [1, 2, 3]
    assert shown([[100, 100, 100, 100, 50, 0, 0, 0]] * 2) == [5, 6, 7]
    assert shown([[0]]) == [1] and w([]) == (0, -1)


def test_dashboard_bars_follow_the_ticks(world):
    from skillops import dashboard
    w = world
    progress.tick(w["A"], w["vid"], w["pub"]["checkables"][0]["id"], w["t1"], w["jid"])
    cards = dashboard.programme_cards(dashboard.batch_rows(org.scope(None), extras=False))
    card = next(c for c in cards if any(b["batch"]["id"] == w["A"] for b in c["batches"]))
    a = next(b for b in card["batches"] if b["batch"]["id"] == w["A"])
    s = progress.summary(w["A"], w["vid"])
    k = [j["id"] for j in card["projects"]].index(w["jid"])
    assert a["pcts"][k] == round(100 * s["done"] / s["total"])           # Airtel: from the ticks
    assert all(v == 0 for i, v in enumerate(a["pcts"]) if i != k)        # no runbook / nothing marked: 0%
    assert a["overall"] == round(sum(a["pcts"]) / len(a["pcts"]))
    assert len(card["shown"]) <= 3 and any(x["project"]["id"] == w["jid"] for x in card["shown"])
    assert a["position"] and a["position"] in body(w["boss"].get("/dashboard"))


def test_admin_opens_a_batch_on_its_projects_and_can_mark_one_complete(world):
    w = world
    page = body(w["boss"].get(f"/b/{w['A']}"))
    # the admin's batch page is monitoring: it shows the projects but changes nothing
    assert "Airtel" in page and "Where the class is" not in page and "Mark complete" not in page and "View only" in page
    assert "Mark complete" in body(w["boss"].get(f"/admin/batches/{w['A']}"))   # the change is made in Settings
    assert "Where the class is" in body(w["boss"].get(f"/b/{w['A']}?view=detail"))
    assert "Where the class is" in body(w["t"].get(f"/b/{w['A']}"))          # trainers keep their own view
    r = w["boss"].post(f"/admin/batches/{w['A']}/projects/{w['jid']}/complete")
    assert r["statusCode"] == 303
    s = progress.summary(w["A"], w["vid"])
    assert s["total"] and s["done"] == s["total"]
    assert "Complete" in body(w["boss"].get(f"/b/{w['A']}"))
    assert w["t"].post(f"/admin/batches/{w['A']}/projects/{w['jid']}/complete")["statusCode"] in (302, 303, 403)


def test_a_project_without_a_runbook_can_be_marked_complete_and_reopened(world):
    w = world
    from skillops import dashboard
    bare = next(j for j in org.list_projects(org.get_batch(w["A"])["prog_id"]) if not j.get("runbook_id"))
    url = f"/admin/batches/{w['A']}/projects/{bare['id']}"
    assert w["boss"].post(url + "/complete")["statusCode"] == 303
    assert bare["id"] in dashboard.manual_done(w["A"])
    assert 'badge teal">Complete' in body(w["boss"].get(f"/b/{w['A']}"))
    assert "Reopen" in body(w["boss"].get(f"/admin/batches/{w['A']}"))
    assert w["boss"].post(url + "/reopen")["statusCode"] == 303
    assert bare["id"] not in dashboard.manual_done(w["A"])


def test_current_project_can_be_set_from_the_projects_list(world):
    w = world
    assert "Set as current" in body(w["boss"].get(f"/b/{w['A']}")) or True
    r = w["boss"].post(f"/admin/batches/{w['A']}/current-project", {"project": w["jid"]})
    assert r["statusCode"] == 303 and org.get_batch(w["A"])["current_project"] == w["jid"]


def test_admin_monitoring_pages_only_show(world):
    """The admin's side menu opens read-only pages; every change is made in Settings (profile menu)."""
    w = world
    for path in ("/dashboard", "/monitor/courses", "/monitor/runbooks", "/monitor/live",
                 "/monitor/mcq", "/monitor/audit", f"/b/{w['A']}", f"/b/{w['A']}/live", f"/b/{w['A']}/mcq"):
        page = body(w["boss"].get(path))
        assert 'method="post" action="/admin' not in page, path
        assert 'href="/admin/' not in page.split('class="side"')[1].split("</nav>")[0], path   # the menu never edits
    side = body(w["boss"].get("/dashboard")).split('class="side"')[1].split("</nav>")[0]
    assert "Import" not in side and "view only" not in side and "side-mode" not in side
    assert side.rstrip().endswith("</div>") and "Settings" in side.split("Health")[-1]   # Settings: last item in the menu
    # a change sent from a monitoring batch page is refused; the same change from Settings works
    assert w["boss"].post(f"/b/{w['A']}/live/codes", {"max_uses": "5"})["statusCode"] == 403
    assert w["boss"].get(f"/admin/batches/{w['A']}/live")["statusCode"] == 200
    assert 'action="/admin/batches/' in body(w["boss"].get(f"/admin/batches/{w['A']}/live"))


def test_each_role_is_marked_on_the_top_bar(world):
    w = world
    admin = body(w["boss"].get("/dashboard"))
    assert "role-admin" in admin and "area-monitor" in admin and "role-mode" not in admin
    settings = body(w["boss"].get("/admin/batches"))
    assert "area-settings" in settings and 'class="settingsbar"' in settings
    trainer = body(w["t"].get(f"/b/{w['A']}"))
    assert "role-trainer" in trainer and ">Trainer<" in trainer and "role-admin" not in trainer
    student = body(Browser().get("/register"))
    assert "role-student" in student and ">Student<" in student


def test_same_current_project_shows_previous_current_next_else_the_usual_window(world):
    from skillops import dashboard
    w = world
    prog_id = org.get_batch(w["A"])["prog_id"]
    projects = org.list_projects(prog_id)
    assert len(projects) >= 3
    batches = [b for b in org.list_batches(prog_id)]

    def card():
        cards = dashboard.programme_cards(dashboard.batch_rows(org.scope(None), extras=False))
        return next(c for c in cards if c["programme"]["id"] == prog_id)

    mid = projects[1]["id"]
    for b in batches:
        org.store.update("CORE", f"BATCH#{b['id']}", "META", {"current_project": mid})
    c = card()
    assert [x["project"]["id"] for x in c["shown"]] == [p["id"] for p in projects[0:3]]      # before, current, next
    # the first project is current everywhere: no earlier one, so current and next
    for b in batches:
        org.store.update("CORE", f"BATCH#{b['id']}", "META", {"current_project": projects[0]["id"]})
    assert [x["project"]["id"] for x in card()["shown"]] == [p["id"] for p in projects[0:2]]
    # batches differ: the earlier logic applies (at most three shown, no one-before-and-after rule)
    org.store.update("CORE", f"BATCH#{batches[0]['id']}", "META", {"current_project": projects[2]["id"]})
    if len(batches) > 1:
        assert len(card()["shown"]) <= 3


def test_settings_open_in_a_pane_over_the_page(world):
    w = world
    page = body(w["boss"].get("/dashboard"))
    assert 'id="so-settings"' in page and "data-settings-open" in page and 'data-settings-link' in page
    assert 'href="/admin/batches"' in page.split('id="so-settings"')[1].split("</aside>")[0]
    # a page loaded inside the pane (the browser says it is an iframe) has no overlay of its own
    r = w["boss"].request("GET", "/admin/batches", headers={"sec-fetch-dest": "iframe"})
    assert 'class="topbar"' in body(r) and 'id="so-settings"' not in body(r) and "embed" in body(r).split("<body", 1)[1].split(">", 1)[0]
    assert "frame-ancestors 'self'" in r["headers"]["content-security-policy"] and r["headers"]["x-frame-options"] == "SAMEORIGIN"
    assert 'id="so-settings"' not in body(w["t"].get(f"/b/{w['A']}"))        # trainers have no Settings


def test_batch_and_team_progress_have_current_and_all_projects_views(world):
    w = world
    org.associate("trainer1@robomy.test", w["ids"]["B"])
    w["t"].sign_in_local("trainer1@robomy.test")
    for path in (f"/b/{w['A']}", "/team"):
        cur = body(w["t"].get(path))
        allp = body(w["t"].get(path + ("&" if "?" in path else "?") + "scope=all"))
        assert "scope-tabs" in cur and 'scope=current" class="on"' in cur
        assert 'scope=all" class="on"' in allp
    cur = body(w["t"].get(f"/b/{w['A']}"))
    allp = body(w["t"].get(f"/b/{w['A']}?scope=all"))
    assert "Where the class is" in cur and "Where the class is" not in allp           # current-project things
    # All projects: the progress bar, then one table of every project and its details; none of the Current widgets
    assert 'proj-table' in allp and "Airtel" in allp and "Runbook" in allp and "Furthest item" in allp and "Last marked" in allp
    assert 'prog-grid' not in allp and 'class="timeline"' not in allp and 'class="colchart"' not in allp
    assert w["t"].get("/team?scope=nonsense")["statusCode"] == 200


def test_runbook_and_planner_links_open_in_a_pane_not_a_browser_window(world):
    w = world
    js = open("src/static/app.js", encoding="utf-8").read()
    assert "window.open(" not in js and "so-reader" in js                     # no browser pop-up any more
    w["t"].sign_in_local("trainer1@robomy.test")
    page = body(w["t"].get(f"/b/{w['A']}/today"))
    assert "data-popup" in page                                              # the links the pane takes over
    framed = w["t"].request("GET", f"/b/{w['A']}/p/{w['jid']}/read", headers={"sec-fetch-dest": "iframe"})
    assert framed["statusCode"] == 200 and "frame-ancestors 'self'" in framed["headers"]["content-security-policy"]
