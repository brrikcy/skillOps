import pytest

from helpers import Browser, body, setup_org
from skillops.core import org, perms
from skillops.core.util import Conflict, Forbidden


def names(tree):
    return sorted(f"{i['institution']['name']}/{p['programme']['name']}/{b['name']}"
                  for i in tree for p in i["programmes"] for b in p["batches"])


def test_trainer_in_two_batches_of_two_programmes_sees_exactly_those(aws):
    ids = setup_org()
    org.add_trainer_to_institution("trainer1@robomy.test", ids["i2"])
    org.associate("trainer1@robomy.test", ids["b2"])          # trainer 1: Batch A (DBI) + Morning (other college)
    staff = perms.staff_from_session({"email": "trainer1@robomy.test", "role": "trainer"})
    assert names(perms.visible_scope(staff)) == [
        "DBI Skill Park/DBI OJT Sem 3 - Data Analyst/Batch A",
        "Second College/Data Analyst Bootcamp/Morning",
    ]
    t2 = perms.staff_from_session({"email": "trainer2@robomy.test", "role": "trainer"})
    assert names(perms.visible_scope(t2)) == ["DBI Skill Park/DBI OJT Sem 3 - Data Analyst/Batch B"]
    boss = perms.Staff("boss@robomy.test", "superadmin", "Boss")
    assert len(names(perms.visible_scope(boss))) == 4


def test_archived_batch_programme_or_institution_is_hidden_from_trainers(aws):
    ids = setup_org()
    org.add_trainer_to_institution("trainer1@robomy.test", ids["i2"])
    org.associate("trainer1@robomy.test", ids["b2"])
    t1 = lambda: perms.staff_from_session({"email": "trainer1@robomy.test", "role": "trainer"})  # noqa: E731
    org.set_archived("batch", ids["A"], True)
    assert names(perms.visible_scope(t1())) == ["Second College/Data Analyst Bootcamp/Morning"]
    with pytest.raises(Forbidden):
        perms.require_batch(t1(), ids["A"])
    org.set_archived("batch", ids["A"], False)
    org.set_archived("programme", ids["p2"], True)
    assert names(perms.visible_scope(t1())) == ["DBI Skill Park/DBI OJT Sem 3 - Data Analyst/Batch A"]
    org.set_archived("programme", ids["p2"], False)
    org.set_archived("institution", ids["i1"], True)
    assert names(perms.visible_scope(t1())) == ["Second College/Data Analyst Bootcamp/Morning"]
    # nothing was deleted: the supervisor still sees it all with archived included
    assert len(names(org.scope(None, include_archived=True))) == 4


def test_unassigned_batch_gives_the_plain_message(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    r = b.get(f"/b/{ids['B']}")
    assert r["statusCode"] == 403
    assert "Batch B is not assigned to you." in body(r) and "Ask your supervisor." in body(r)


def test_trainer_opens_into_their_batch(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer2@robomy.test")
    r = b.get("/")
    assert r["statusCode"] == 303 and r["headers"]["location"] == f"/b/{ids['B']}"     # one batch: no overview


def test_trainer_with_no_batch_is_told_so(aws):
    setup_org()
    org.create_trainer("t9@robomy.test", "Trainer Nine")
    b = Browser()
    b.sign_in_local("t9@robomy.test")
    r = b.get("/")
    assert r["statusCode"] == 200 and "No batches yet" in body(r)


def test_names_are_unique_inside_their_parent(aws):
    ids = setup_org()
    with pytest.raises(Conflict):
        org.create_batch(ids["p1"], "batch a")
    with pytest.raises(Conflict):
        org.create_institution("dbi skill park", "DBI2")
    org.create_batch(ids["p2"], "Batch A")      # the same name in another programme is fine


def test_projects_keep_their_course_order_and_can_move(aws):
    ids = setup_org()
    p = ids["p1"]
    assert [j["name"] for j in org.list_projects(p)] == ["IPL", "Airtel", "HDFC"]
    hdfc = org.list_projects(p)[2]["id"]
    org.move_project(hdfc, "up")                                    # moved in the course: every programme follows
    assert [j["name"] for j in org.list_projects(p)] == ["IPL", "HDFC", "Airtel"]
    org.move_project(org.list_projects(p)[0]["id"], "up")          # already first: no change
    assert [j["name"] for j in org.list_projects(p)] == ["IPL", "HDFC", "Airtel"]


def test_programme_takes_its_courses_projects_and_can_change_them(aws):
    ids = setup_org()
    c2 = org.create_course("Business Case Studies")
    uber = org.add_project(c2, "Uber")
    org.add_project(c2, "Apollo")
    with pytest.raises(Conflict):                                   # one name per project across courses
        org.add_project(c2, "airtel")
    p = ids["p1"]
    names = lambda: [j["name"] for j in org.list_projects(p)]       # noqa: E731
    org.set_programme_courses(p, [ids["c1"], c2])
    assert names() == ["IPL", "Airtel", "HDFC", "Uber", "Apollo"]
    org.move_programme_course(p, c2, "up")
    assert names() == ["Uber", "Apollo", "IPL", "Airtel", "HDFC"]
    airtel = next(j for j in org.list_projects(p) if j["name"] == "Airtel")
    org.remove_programme_project(p, airtel["id"])                    # taken out of a selected course
    assert "Airtel" not in names() and org.get_project(p, airtel["id"])["removed"]   # still readable
    assert org.add_programme_project(p, airtel["id"]) == "back"
    org.set_programme_courses(p, [ids["c1"]])                        # the other course goes, with its projects
    assert names() == ["IPL", "Airtel", "HDFC"]
    assert org.add_programme_project(p, uber) == "added"             # one project from outside its courses
    assert names() == ["IPL", "Airtel", "HDFC", "Uber"]
    assert next(j for j in org.list_projects(p) if j["name"] == "Uber")["source"] == "added"
    org.set_programme_courses(p, [ids["c1"], c2])                    # selecting its course absorbs the extra
    assert org.get_programme(p)["extra_projects"] == [] and names().count("Uber") == 1


def test_a_course_change_reaches_every_programme(aws):
    ids = setup_org()
    org.add_project(ids["c1"], "Zomato")
    assert [j["name"] for j in org.list_projects(ids["p1"])][-1] == "Zomato"
    assert [j["name"] for j in org.list_projects(ids["p2"])] == ["Airtel"]        # p2 does not take the course


def test_trainers_belong_to_institutions_before_their_batches(aws):
    from skillops.core.util import UserError
    ids = setup_org()
    with pytest.raises(UserError, match="not a trainer at Second College"):
        org.associate("trainer1@robomy.test", ids["b2"])
    org.add_trainer_to_institution("trainer1@robomy.test", ids["i2"])
    org.associate("trainer1@robomy.test", ids["b2"])
    assert org.trainer_institution_ids("trainer1@robomy.test") == {ids["i1"], ids["i2"]}
    gone = org.remove_trainer_from_institution("trainer1@robomy.test", ids["i2"])
    assert [b["id"] for b in gone] == [ids["b2"]] and org.trainer_batch_ids("trainer1@robomy.test") == {ids["A"]}
    assert [t["name"] for t in org.institution_trainers(ids["i1"])] == ["Trainer 1", "Trainer 2", "Trainer 3"]
    assert org.institution_trainers(ids["i2"]) == []


def test_unlinking_keeps_history_and_takes_effect_at_once(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    assert b.get(f"/b/{ids['A']}")["statusCode"] == 200
    org.dissociate("trainer1@robomy.test", ids["A"])
    assert b.get(f"/b/{ids['A']}")["statusCode"] == 403
    item = org.store.get("CORE", "TRAINER#trainer1@robomy.test", f"BATCH#{ids['A']}")
    assert item and item["active"] is False and item["until"]


def test_turned_off_trainer_is_refused_at_once(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    org.set_trainer_enabled("trainer1@robomy.test", False)
    r = b.get(f"/b/{ids['A']}")
    assert r["statusCode"] == 403 and "turned off" in body(r)
    assert b.get("/")["statusCode"] == 303      # session ended: back to sign-in


def test_supervisor_creates_everything_from_forms(boss):
    r = boss.post("/admin/institutions", {"name": "New Place", "code": "NP", "idem": "k1"})
    assert r["statusCode"] == 303
    inst = [i for i in org.list_institutions() if i["name"] == "New Place"][0]
    r = boss.post(f"/admin/institutions/{inst['id']}/programmes", {"name": "Prog X", "idem": "k2"})
    prog = org.list_programmes(inst["id"])[0]
    boss.post("/admin/courses", {"name": "Analytics", "idem": "k3"})
    course = org.list_courses()[0]
    boss.post(f"/admin/courses/{course['id']}/projects", {"name": "Uber", "idem": "k3b"})
    boss.post(f"/admin/courses/{course['id']}/projects", {"name": "Apollo", "idem": "k3c"})
    boss.post(f"/admin/programmes/{prog['id']}/courses", {"course": course["id"]})
    boss.post(f"/admin/programmes/{prog['id']}/batches", {"name": "Batch Z", "mcq_batch": "Z", "idem": "k4"})
    batch = org.list_batches(prog["id"])[0]
    boss.post("/admin/trainers", {"name": "Asha", "email": "asha@robomy.test", "idem": "k5"})
    r = boss.post(f"/admin/batches/{batch['id']}/trainers", {"email": "asha@robomy.test"})
    assert r["statusCode"] == 400 and "not a trainer at New Place" in body(r)          # not at the institution yet
    boss.post(f"/admin/institutions/{inst['id']}/trainers", {"email": "asha@robomy.test", "idem": "k6"})
    boss.post(f"/admin/institutions/{inst['id']}/trainers",                              # a new trainer, made here
              {"name": "Ravi", "email": "ravi@robomy.test", "idem": "k7"})
    assert [t["name"] for t in org.institution_trainers(inst["id"])] == ["Asha", "Ravi"]
    boss.post(f"/admin/batches/{batch['id']}/trainers", {"email": "asha@robomy.test"})
    assert org.trainer_batch_ids("asha@robomy.test") == {batch["id"]}
    assert [j["name"] for j in org.list_projects(prog["id"])] == ["Uber", "Apollo"]
    apollo = org.list_projects(prog["id"])[1]["id"]
    boss.post(f"/admin/programmes/{prog['id']}/projects/{apollo}/remove", {})
    assert [j["name"] for j in org.list_projects(prog["id"])] == ["Uber"]
    page = body(boss.get(f"/admin/programmes/{prog['id']}"))
    assert "Taken out of this programme" in page and "Put back" in page
    assert batch["mcq_batch"] == "Z"
