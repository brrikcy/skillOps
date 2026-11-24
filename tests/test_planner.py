"""Session planner: trainers lay out a date's session from runbook parts; the supervisor only reads."""
import json

import pytest

import docx_factory
from helpers import Browser, body, setup_org
from skillops import planner
from skillops.core import jobs, org
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
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    return dict(ids=ids, vid=vid, pub=pub, jid=airtel["id"], t=t, A=ids["A"],
                acts=pub["checkables"], secs=progress.sections(pub))


def _save(w, entries, rev=0, day="2026-11-12", who=None):
    who = who or w["t"]
    return who.request("POST", f"/b/{w['A']}/plan", raw=json.dumps(
        {"csrf": who.csrf, "date": day, "rev": rev, "note": "Bring the HDMI cable", "entries": entries}).encode(),
        content_type="application/json", headers={"accept": "application/json", "x-requested-with": "fetch"})


def test_split_screen_page_shows_the_runbook_and_the_planner(world):
    w = world
    r = w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan?date=2026-11-12")
    html = body(r)
    assert r["statusCode"] == 200
    assert "Session planner" in html and 'class="pv"' in html and "data-plan-list" in html
    assert html.count('draggable="true"') >= 3                 # the section and its actions can be dragged
    frag = w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan/part/{w['secs'][1]['id']}?date=2026-11-12")
    assert frag["statusCode"] == 200 and w["secs"][1]["title"] in body(frag) and "<html" not in body(frag)


def test_drag_and_drop_order_is_saved_as_given(world):
    w = world
    a, b = w["acts"][3], w["acts"][0]                          # any order: a later action first
    r = _save(w, [{"kind": "item", "proj": w["jid"], "node": a["id"]},
                  {"kind": "text", "text": "Break"},
                  {"kind": "item", "proj": w["jid"], "node": b["id"]},
                  {"kind": "item", "proj": w["jid"], "node": "n99999"},           # not in the runbook: dropped
                  {"kind": "item", "proj": "j-nothere", "node": b["id"]}])        # not a project: dropped
    assert r["statusCode"] == 200, body(r)
    plan = planner.get(w["A"], "2026-11-12")
    assert [e.get("node") or e.get("text") for e in plan["entries"]] == [a["id"], "Break", b["id"]]
    assert plan["entries"][0]["title"] == a["title"] and plan["note"] == "Bring the HDMI cable"
    assert plan["rev"] == 1
    # an old page (rev 0) cannot overwrite the newer plan
    assert _save(w, [], rev=0)["statusCode"] == 409
    assert [d["date"] for d in planner.list_dates(w["A"])] == ["2026-11-12"]


def test_plans_without_javascript(world):
    w = world
    base = f"/b/{w['A']}/plan"
    w["t"].post(base, {"date": "2026-11-13", "rev": "0", "action": "add", "proj": w["jid"], "node": w["acts"][0]["id"]})
    w["t"].post(base, {"date": "2026-11-13", "rev": "1", "action": "add", "proj": w["jid"], "node": w["acts"][1]["id"]})
    w["t"].post(base, {"date": "2026-11-13", "rev": "2", "action": "up", "index": "1"})
    plan = planner.get(w["A"], "2026-11-13")
    assert [e["node"] for e in plan["entries"]] == [w["acts"][1]["id"], w["acts"][0]["id"]]


def test_supervisor_sees_plans_but_cannot_change_them(world):
    w = world
    _save(w, [{"kind": "item", "proj": w["jid"], "node": w["acts"][0]["id"]}])
    boss = Browser()
    boss.sign_in_local("boss@robomy.test")
    html = body(boss.get(f"/b/{w['A']}/p/{w['jid']}/plan?date=2026-11-12"))
    assert "view only" in html and 'draggable="true"' not in html and w["acts"][0]["title"] in html
    r = _save(w, [], rev=1, who=boss)
    assert r["statusCode"] == 403 and planner.get(w["A"], "2026-11-12")["entries"]


def test_other_batches_plans_are_closed_to_a_trainer(world):
    w = world
    assert w["t"].get(f"/b/{w['ids']['B']}/p/{w['jid']}/plan")["statusCode"] == 403
    r = w["t"].post(f"/b/{w['ids']['B']}/plan", {"date": "2026-11-12", "rev": "0", "action": "text", "text": "x"})
    assert r["statusCode"] == 403


def test_todays_plan_shows_in_the_class_view_and_on_the_batch_page(world, monkeypatch):
    w = world
    from skillops.pages import teach
    monkeypatch.setattr(teach, "_ist_today", lambda: "2026-11-12")
    _save(w, [{"kind": "item", "proj": w["jid"], "node": w["acts"][1]["id"]}])
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/teach/{w['acts'][1]['parent']}"))
    assert "today's plan · #1" in html
    home = body(w["t"].get(f"/b/{w['A']}/today"))
    assert "1 part planned" in home and "/session?date=2026-11-12" in home


def test_planner_page_marks_sections_and_parts_with_alternating_colours_and_lists_the_parts(world):
    w = world
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan"))
    assert 'class="sheet sec-' in html and 'sheet-act sub-a' in html and 'sheet-act sub-b' in html
    nav = html.split('data-nav', 1)[1].split("</nav>", 1)[0]
    assert 'rv-sec' in nav and 'data-toggle-subs' in nav and 'rv-sub' in nav and 'data-at=' in nav


def test_the_page_script_does_not_lock_buttons_that_the_page_handles_itself():
    js = open("src/static/app.js", encoding="utf-8").read()
    assert "ev.defaultPrevented ||" in js          # the planner's + and Add buttons stay usable after the first use


def test_sections_list_shows_progress_and_marks_the_chosen_part(world):
    w = world
    progress.tick(w["A"], w["vid"], w["acts"][0]["id"], w["t1"] if "t1" in w else __import__("skillops.core.perms", fromlist=["Staff"]).Staff("trainer1@robomy.test", "trainer", "Trainer 1", {w["A"]}), w["jid"])
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan"))
    nav = html.split('data-nav', 1)[1].split("</nav>", 1)[0]
    assert 'class="rv-total"' in nav and "done so far" in nav and 'rv-bar' in nav and 'rv-mark' in nav
    assert 'rv-sub ' in nav and " done" in nav                      # a finished part is marked in the list
    js = open("src/static/planner.js", encoding="utf-8").read()
    assert "function selectAct" in js and ".sheet-act.sel" in js


def test_sections_list_has_a_resize_handle_and_a_plus_for_every_section(world):
    w = world
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan"))
    nav = html.split('data-nav', 1)[1].split("</nav>", 1)[0]
    assert 'data-resize' in nav and nav.count("data-nav-add") >= len(w["secs"])          # a + on every section
    assert 'class="rv-sub-row' in nav and nav.count('data-parent=') >= 1                          # and on every part
    assert nav.count('draggable="true"') >= len(w["secs"])                      # a section can also be dragged into the plan
    read = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/read"))
    assert "data-nav-add" not in read.split('data-nav', 1)[1].split("</nav>", 1)[0]     # no plan, no +
    css = open("src/static/style.css", encoding="utf-8").read()
    assert 'td[style*="background"] code' in css and "var(--nav-w, 300px)" in css


def test_sections_list_has_an_earlier_steps_row_and_follows_the_reader(world):
    w = world
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan"))
    nav = html.split('data-nav', 1)[1].split("</nav>", 1)[0]
    assert 'data-earlier' in nav and 'data-i="0"' in nav and 'data-done=' in nav
    js = open("src/static/planner.js", encoding="utf-8").read()
    assert "function focusGroup" in js and "only the open section stays expanded" in js


def test_the_open_step_and_the_sections_above_you_are_pinned_in_the_list():
    js = open("src/static/planner.js", encoding="utf-8").read()
    css = open("src/static/style.css", encoding="utf-8").read()
    assert "function pin()" in js and "rv-stuck" in js and ".rv-group > summary.rv-stuck { position: sticky" in css


def test_the_planner_never_saves_by_itself_and_asks_before_losing_changes(world):
    w = world
    html = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan"))
    assert "data-plan-save" in html and "data-plan-discard" in html          # an explicit Save plan button
    js = open("src/static/planner.js", encoding="utf-8").read()
    assert "scheduleSave" not in js and "setTimeout(save" not in js          # no timer saves the plan
    assert "beforeunload" in js and "__soPlanner" in js and "Save and continue" in js
    app = open("src/static/app.js", encoding="utf-8").read()
    assert "window.soAsk" in app and "Save and close" in app and "Discard changes" in app and "Keep editing" in app


def test_a_part_whose_whole_section_is_in_the_plan_is_left_out_and_reported(world):
    w = world
    sec = w["secs"][1]
    kids = [a for a in w["acts"] if a.get("parent") == sec["id"]]
    item = lambda n: {"kind": "item", "proj": w["jid"], "node": n["id"]}
    r = _save(w, [item(kids[0]), item(sec), item(kids[1])], day="2026-12-01")
    assert r["statusCode"] == 200, body(r)
    j = json.loads(r["body"])
    assert [e["node"] for e in j["entries"]] == [sec["id"]]                    # the section only
    assert j["dropped"] == {"missing": 0, "overlap": 2}
    r = _save(w, [item(w["acts"][0]), {"kind": "item", "proj": w["jid"], "node": "n99998"}], rev=0, day="2026-12-02")
    assert json.loads(r["body"])["dropped"]["missing"] == 1                    # a part the runbook does not have


def test_stored_parts_the_runbook_no_longer_has_are_marked_not_hidden(world):
    w = world
    from skillops.core import store
    store.t("CORE").put_item(Item={"pk": f"PLAN#{w['A']}", "sk": "2026-12-03", "rev": 1, "note": "",
                                   "entries": [{"kind": "item", "proj": w["jid"], "node": w["acts"][0]["id"], "title": "x"},
                                               {"kind": "item", "proj": w["jid"], "node": "n99997", "title": "Old part"}]})
    plan = planner.get(w["A"], "2026-12-03")
    marked = planner.annotate(w["A"], plan["entries"])
    assert [e["missing"] for e in marked] == [False, True]
    page = body(w["t"].get(f"/b/{w['A']}/p/{w['jid']}/plan?date=2026-12-03"))
    assert '"missing": true' in page                                            # the page knows
    session = body(w["t"].get(f"/b/{w['A']}/session?date=2026-12-03"))
    assert "not in this batch" in session and "left out here" in session        # and the class view says so
    cur = w["t"].get(f"/b/{w['A']}/plan/current?date=2026-12-03")
    assert cur["statusCode"] == 200 and json.loads(cur["body"])["rev"] == 1


def test_the_page_script_has_the_new_recovery_paths():
    js = open("src/static/planner.js", encoding="utf-8").read()
    app = open("src/static/app.js", encoding="utf-8").read()
    assert "function settleConflict" in js and "Merge: add what they have to mine" in js and "function addEntry" in js
    assert "function canAdd" in js and "function applyFilter" in js and "aria-pressed" in js
    assert "soNavigate" in app and 'ev.key === "Escape" && !ev.defaultPrevented' in app


def test_a_trainer_downloads_a_saved_plan_as_a_pdf(world):
    import base64

    w = world
    sec = w["secs"][1]
    entries = [{"kind": "item", "proj": w["jid"], "node": sec["id"]},
               {"kind": "text", "text": "Short break"},
               {"kind": "item", "proj": w["jid"], "node": w["acts"][0]["id"]}]
    assert _save(w, entries, day="2026-12-05")["statusCode"] == 200
    r = w["t"].get(f"/b/{w['A']}/plan/pdf?date=2026-12-05")
    assert r["statusCode"] == 200
    assert r["headers"]["content-type"] == "application/pdf"
    assert "session-plan-" in r["headers"]["content-disposition"] and "2026-12-05" in r["headers"]["content-disposition"]
    data = base64.b64decode(r["body"])
    assert data.startswith(b"%PDF") and b"Short break" in data and b"Session plan" in data
    # a date with no plan still gives a file (it says nothing is planned)
    empty = w["t"].get(f"/b/{w['A']}/plan/pdf?date=2026-12-06")
    assert empty["statusCode"] == 200 and b"Nothing is planned" in base64.b64decode(empty["body"])


def test_the_pdf_carries_the_content_of_every_planned_part(world):
    import base64

    from skillops.core import pdfhtml
    from skillops.runbook import service as svc
    w = world
    sec, act = w["secs"][1], w["acts"][0]
    assert _save(w, [{"kind": "item", "proj": w["jid"], "node": sec["id"]},
                     {"kind": "item", "proj": w["jid"], "node": act["id"]}], day="2026-12-07")["statusCode"] == 200
    data = base64.b64decode(w["t"].get(f"/b/{w['A']}/plan/pdf?date=2026-12-07")["body"])
    words = []
    for nid in [a["id"] for a in w["acts"] if a.get("parent") == sec["id"]] + [act["id"]]:
        for b in pdfhtml.blocks(svc.item_html(w["vid"], nid)):
            if b["t"] == "p":
                words += [r[0].strip() for r in b["runs"] if len(r[0].strip()) > 6]
    assert words, "the test runbook has some text"
    assert all(wd.encode("cp1252", "replace").split(b"(")[0][:12] in data for wd in words[:6])


def _runbook_pdf_for(w, leave_out=()):
    """The runbook 'saved as PDF': a heading and some lines for every published page (bookmarked)."""
    from skillops.core import pdf
    doc = pdf.Document("Runbook", footer="Runbook")
    for n in w["pub"]["nodes"]:
        if not n.get("page") or n["id"] in leave_out:
            continue
        doc.need(90)
        doc.bookmark(n["title"], gap=4)
        doc.text(n["title"], size=13, bold=True)
        for k in range(6):
            doc.text(f"ORIGINAL {n['id']} line {k} of the runbook's own page.", size=9)
        doc.space(10)
    return doc.render()


def test_a_session_pdf_is_cut_from_the_runbooks_own_pdf_and_falls_back_for_parts_it_lacks(world):
    import base64
    import io

    from pypdf import PdfReader
    from skillops.runbook import service as svc
    w = world
    sec, act = w["secs"][1], w["acts"][0]
    entries = [{"kind": "item", "proj": w["jid"], "node": sec["id"]}, {"kind": "text", "text": "Short break"},
               {"kind": "item", "proj": w["jid"], "node": act["id"]}]
    assert _save(w, entries, day="2026-12-08")["statusCode"] == 200
    url = f"/b/{w['A']}/plan/pdf?date=2026-12-08"

    plain = base64.b64decode(w["t"].get(url)["body"])                         # no runbook PDF yet: laid out from the pages
    assert b"ORIGINAL" not in plain

    jobs.INLINE = True                                                         # the matching job runs on the spot
    try:
        pages = svc.attach_pdf(w["vid"], "runbook.pdf", _runbook_pdf_for(w), "boss")
    finally:
        jobs.INLINE = False
    v = svc.get_version(w["vid"])
    assert pages >= 1 and v["pdf_found"] == v["pdf_total"] > 0 and "found" in v["pdf_message"]
    svc._pdf_cache.clear()
    cut = base64.b64decode(w["t"].get(url)["body"])
    r = PdfReader(io.BytesIO(cut))
    text = "\n".join(p.extract_text() for p in r.pages)
    assert "Short break" in r.pages[0].extract_text()                           # the cover lists your own lines
    assert f"ORIGINAL {sec['id']} line 0" in text and f"ORIGINAL {act['id']} line 5" in text
    assert "Page 1 of" in r.pages[0].extract_text()

    jobs.INLINE = True
    try:
        svc.attach_pdf(w["vid"], "runbook.pdf", _runbook_pdf_for(w, leave_out={act["id"]}), "boss")
    finally:
        jobs.INLINE = False
    svc._pdf_cache.clear()
    mixed = PdfReader(io.BytesIO(base64.b64decode(w["t"].get(url)["body"])))
    mtext = "\n".join(p.extract_text() for p in mixed.pages)
    assert f"ORIGINAL {sec['id']} line 0" in mtext and f"ORIGINAL {act['id']}" not in mtext
    assert act["title"] in mtext                                               # the missing part is laid out instead


def test_the_runbook_opens_as_its_original_pdf_when_there_is_one_and_the_planner_keeps_its_own_pages(world):
    import base64

    from skillops.runbook import service as svc
    w = world
    t, A, jid, vid = w["t"], w["A"], w["jid"], w["vid"]
    act = w["acts"][0]
    reader = f"/b/{A}/p/{jid}/read"
    old = body(t.get(reader))
    assert "data-rbv" not in old and "data-pages" in old                    # no PDF yet: the runbook's pages, as before

    jobs.INLINE = True
    try:
        svc.attach_pdf(vid, "runbook.pdf", _runbook_pdf_for(w), "boss")
    finally:
        jobs.INLINE = False
    svc._pdf_map_cache.clear()
    for path in (reader, f"/b/{A}/p/{jid}", f"/b/{A}/p/{jid}/intro", f"/b/{A}/p/{jid}/i/{act['id']}",
                 f"/b/{A}/p/{jid}/teach", f"/b/{A}/p/{jid}/teach/{act['parent']}"):
        r = t.get(path)
        page = body(r)
        assert r["statusCode"] == 200 and "data-rbv" in page and f"/b/{A}/p/{jid}/runbook.pdf" in page, path
        assert "data-pages" not in page and "t-cols" not in page             # not the columns view
    page = body(t.get(f"/b/{A}/p/{jid}/i/{act['id']}"))
    assert f'data-act="{act["id"]}"' in page and "data-check" in page          # trainers can tick from the sections list
    assert "#page=" in page and "view=FitH" in page

    pdf_r = t.get(f"/b/{A}/p/{jid}/runbook.pdf")
    assert pdf_r["statusCode"] == 200 and pdf_r["headers"]["content-type"] == "application/pdf"
    assert base64.b64decode(pdf_r["body"]).startswith(b"%PDF") and "inline" in pdf_r["headers"]["content-disposition"]
    assert "attachment" in t.get(f"/b/{A}/p/{jid}/runbook.pdf?download=1")["headers"]["content-disposition"]

    planner_page = body(t.get(f"/b/{A}/p/{jid}/plan?date=2026-12-09"))        # planning keeps the runbook's own pages
    assert "data-pages" in planner_page and "data-rbv" not in planner_page

    boss = Browser()
    boss.sign_in_local("boss@robomy.test")
    rid = svc.get_version(vid)["rid"]
    for path in (f"/admin/runbooks/{rid}/v/{vid}/view", f"/admin/runbooks/{rid}/v/{vid}/i/{act['id']}",
                 f"/monitor/runbooks/{rid}/v/{vid}/view"):
        r = boss.get(path)
        page = body(r)
        assert r["statusCode"] == 200 and "data-rbv" in page and f"/runbooks/{rid}/v/{vid}/runbook.pdf" in page, path
        assert "data-check" not in page                                         # the supervisor only reads
    assert boss.get(f"/admin/runbooks/{rid}/v/{vid}/runbook.pdf")["statusCode"] == 200
    assert t.get(f"/admin/runbooks/{rid}/v/{vid}/runbook.pdf")["statusCode"] in (302, 303, 403)
