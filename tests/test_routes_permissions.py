"""Every route is checked: a trainer cannot use supervisor pages or another batch's pages."""
import re

import pytest

from helpers import Browser, body, setup_org
from skillops import web
from skillops.core import org


def fill(pattern, ids):
    """Turn a route pattern into a real path, using ids that exist."""
    proj = org.list_projects(ids["p1"])[0]["id"]

    def sub(m):
        name, rx = m.group(1), m.group(2)
        for prefix, value in (("job-", "job-abcdef12"), ("imp-", "imp-abcdef12"), ("i-", ids["i1"]), ("p-", ids["p1"]), ("c-", ids["c1"]),
                              ("b-", ids["B"]), ("j-", proj), ("r-", "r-abcdef"), ("v-", "v-abcdef"), ("n[", "n12")):
            if rx.startswith(prefix):
                return value
        return "trainer2%40robomy.test" if name == "email" else "abc"
    path = re.sub(r"\(\?P<(\w+)>([^)]*)\)", sub, pattern).replace(r"\-", "-").replace(r"\.", ".")
    assert re.match("^" + pattern + "$", path), (pattern, path)
    return path


@pytest.fixture
def trainer1(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("trainer1@robomy.test")
    return b, ids


def test_every_supervisor_route_refuses_a_trainer(trainer1):
    b, ids = trainer1
    super_routes = [r for r in web.ROUTES if r.access == "super"]
    assert len(super_routes) > 50
    for rt in super_routes:
        path = fill(rt.pattern, ids)
        r = b.get(path) if rt.method == "GET" else b.post(path, {"name": "x"})
        assert r["statusCode"] == 403, (rt.method, rt.pattern, path, r["statusCode"])
        assert "Only the supervisor can do this." in body(r)


def test_every_batch_route_refuses_another_batch(trainer1):
    b, ids = trainer1
    batch_routes = [r for r in web.ROUTES if r.access == "staff" and "(?P<bid>" in r.pattern]
    assert batch_routes
    for rt in batch_routes:
        path = fill(rt.pattern, ids)          # bid = Batch B; trainer 1 has Batch A only
        r = b.get(path) if rt.method == "GET" else b.post(path, {})
        assert r["statusCode"] == 403, (rt.pattern, r["statusCode"])
        assert "Batch B is not assigned to you." in body(r)


def test_signed_out_get_goes_to_sign_in_with_the_same_page(aws):
    ids = setup_org()
    r = Browser().get(f"/b/{ids['A']}?x=1")
    assert r["statusCode"] == 303
    assert r["headers"]["location"] == f"/signin?next=%2Fb%2F{ids['A']}%3Fx%3D1"


def test_post_from_another_site_is_refused(trainer1):
    b, ids = trainer1
    r = b.post("/signout", {}, headers={"origin": "https://evil.example"})
    assert r["statusCode"] == 403 and "did not come from a SkillOps page" in body(r)


def test_post_without_the_right_csrf_token_is_refused(trainer1):
    b, ids = trainer1
    r = b.post("/signout", {"csrf": "wrong"})
    assert r["statusCode"] == 403 and "out of date" in body(r)


def test_unknown_page_is_a_plain_404_with_request_id(aws):
    r = Browser().get("/no/such/page")
    assert r["statusCode"] == 404 and "Request ID" in body(r) and "Traceback" not in body(r)
