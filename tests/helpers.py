"""Shared test helpers (fixtures are in conftest.py). Tests run against moto, a fake AWS in memory. No AWS account needed."""
import base64
import os
import re
import sys
from urllib.parse import urlencode

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-south-1", "AWS_REGION": "ap-south-1",
    "PLATFORM_NAME": "SkillOps", "SKILLOPS_LOCAL": "1", "LOCAL_SUPERADMINS": "boss@robomy.test",
})
for k in ("USER_POOL_ID", "USER_POOL_CLIENT_ID", "JOBS_FUNCTION"):
    os.environ.pop(k, None)

from moto import mock_aws  # noqa: E402

from skillops import web  # noqa: E402
from skillops.core import org, store, util  # noqa: E402

HOST = "skillops.test"


@pytest.fixture
def aws():
    with mock_aws():
        store.reset()
        store.create_tables()
        store.create_buckets()
        yield
        util.set_fake_now(None)
        store.reset()


def make_event(method, path, query="", body=b"", headers=None, cookies=None, content_type=None):
    h = {"host": HOST, "x-forwarded-host": HOST}
    if content_type:
        h["content-type"] = content_type
    h.update(headers or {})
    ev = {"rawPath": path, "rawQueryString": query, "headers": h,
          "requestContext": {"http": {"method": method, "sourceIp": "10.0.0.1"}, "requestId": "test-req"},
          "body": base64.b64encode(body).decode() if isinstance(body, bytes) else body,
          "isBase64Encoded": isinstance(body, bytes)}
    if cookies:
        ev["cookies"] = [f"{k}={v}" for k, v in cookies.items()]
    return ev


class Browser:
    """A tiny browser for tests: keeps cookies, follows nothing, reads the CSRF token from pages."""

    def __init__(self):
        self.cookies = {}
        self.csrf = ""

    def _store_cookies(self, resp):
        for c in resp.get("cookies", []):
            name, rest = c.split("=", 1)
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in c:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value

    def request(self, method, path, form=None, headers=None, raw=None, content_type=None):
        query = ""
        if "?" in path:
            path, query = path.split("?", 1)
        body = b""
        if form is not None:
            body = urlencode(form, doseq=True).encode()
            content_type = "application/x-www-form-urlencoded"
        elif raw is not None:
            body = raw
        h = {"origin": f"https://{HOST}"} if method == "POST" else {}
        h.update(headers or {})
        resp = web.handle(make_event(method, path, query, body, h, self.cookies, content_type))
        self._store_cookies(resp)
        m = re.search(r'name="csrf" value="([^"]+)"', resp.get("body") or "")
        if m:
            self.csrf = m.group(1)
        return resp

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, form=None, csrf=True, **kw):
        form = dict(form or {})
        if csrf and "csrf" not in form:
            form["csrf"] = self.csrf
        return self.request("POST", path, form=form, **kw)

    def sign_in_local(self, email):
        self.get("/signin")
        r = self.post("/signin", {"local_user": email}, csrf=False)
        assert r["statusCode"] == 303, r
        home = self.get("/")   # pick up a CSRF token (following one redirect)
        if home["statusCode"] == 303:
            self.get(home["headers"]["location"])
        return r


def body(resp):
    return resp.get("body") or ""


@pytest.fixture
def boss(aws):
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    b.get("/admin")
    return b


def setup_org():
    """Two institutions. DBI: programme with batches A, B, C and three trainers. Other: one batch."""
    i1 = org.create_institution("DBI Skill Park", "DBI")
    p1 = org.create_programme(i1, "DBI OJT Sem 3 - Data Analyst")
    for name in ("IPL", "Airtel", "HDFC"):
        org.add_project(p1, name)
    batches = {n: org.create_batch(p1, f"Batch {n}", n) for n in "ABC"}
    for n, b in zip("123", "ABC"):
        org.create_trainer(f"trainer{n}@robomy.test", f"Trainer {n}")
        org.associate(f"trainer{n}@robomy.test", batches[b])
    i2 = org.create_institution("Second College", "SC")
    p2 = org.create_programme(i2, "Data Analyst Bootcamp")
    org.add_project(p2, "Airtel")
    b2 = org.create_batch(p2, "Morning", "M")
    return {"i1": i1, "p1": p1, "i2": i2, "p2": p2, "b2": b2, **batches}
