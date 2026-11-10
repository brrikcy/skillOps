"""MCQ connection: the client against a fake Apps Script server (including Google's 302 redirect and
quota pages), the pages, and that everything else keeps working when the test system is down."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from helpers import Browser, body, setup_org
from skillops.mcq import client, fake

SECRET = "x" * 32


class FakeAppsScript(BaseHTTPRequestHandler):
    """Behaves like a deployed Apps Script web app: POST /exec answers 302 to an "echo" address,
    which then answers the JSON with a GET."""
    mode = "ok"
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("content-length") or 0)
        payload = json.loads(self.rfile.read(length))
        FakeAppsScript.seen.append(payload)
        if FakeAppsScript.mode == "quota":
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>Service invoked too many times for one day: urlfetch quota limit.</body></html>")
            return
        if payload.get("secret") != SECRET:
            answer = {"ok": False, "code": "bad_secret", "message": "Wrong or missing secret."}
        else:
            answer = {"ok": True, "result": {"echo": payload["action"], "args": payload.get("args"),
                                             "idem": payload.get("idem")}}
        FakeAppsScript.answer = json.dumps(answer).encode()
        self.send_response(302)
        self.send_header("location", f"http://127.0.0.1:{self.server.server_port}/macros/echo?user_content_key=abc")
        self.end_headers()

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(FakeAppsScript.answer)


@pytest.fixture
def apps_script():
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAppsScript)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    FakeAppsScript.mode = "ok"
    FakeAppsScript.seen = []
    yield f"http://127.0.0.1:{server.server_port}/macros/s/abc/exec"
    server.shutdown()


@pytest.fixture
def fake_system(aws):
    fake.reset()
    ids = setup_org()
    client.save_settings(ids["i1"], "fake://demo", fake.SECRET)
    return ids


def test_client_follows_googles_redirect_and_sends_the_secret(aws, apps_script):
    ids = setup_org()
    from skillops.core import store
    store.t("CORE").put_item(Item={"pk": f"MCQCFG#{ids['i1']}", "sk": "META", "url": apps_script, "has_secret": True})
    store.t("CORE").put_item(Item={"pk": f"MCQSECRET#{ids['i1']}", "sk": "META", "secret": SECRET})
    r = client.call(ids["i1"], "listTests", {"batches": ["A"]})
    assert r == {"echo": "listTests", "args": {"batches": ["A"]}, "idem": None}
    w = client.call(ids["i1"], "setTestActive", {"testId": "W01", "active": True}, write=True)
    assert len(w["idem"]) == 32                       # writes carry an idempotency key
    FakeAppsScript.mode = "quota"
    with pytest.raises(client.McqUnavailable) as e:
        client.call(ids["i1"], "ping")
    assert "daily limit" in str(e.value)
    FakeAppsScript.mode = "ok"
    seen = len(FakeAppsScript.seen)
    with pytest.raises(client.McqUnavailable):            # for a minute, pages do not ask again
        client.call(ids["i1"], "ping")
    assert len(FakeAppsScript.seen) == seen
    store.t("CORE").put_item(Item={"pk": f"MCQSECRET#{ids['i1']}", "sk": "META", "secret": "y" * 32})
    with pytest.raises(client.McqUnavailable) as e:
        client.call(ids["i1"], "ping", fresh=True)        # "Test connection" always asks
    assert "secret does not match" in str(e.value)


def test_client_retries_then_gives_up_calmly(aws, monkeypatch):
    ids = setup_org()
    monkeypatch.setattr("time.sleep", lambda s: None)
    client.save_settings(ids["i1"], "fake://down", fake.SECRET)
    with pytest.raises(client.McqUnavailable) as e:
        client.call(ids["i1"], "ping")
    assert "not answering" in str(e.value)


def test_address_must_be_an_apps_script_link(aws):
    ids = setup_org()
    import os
    os.environ["SKILLOPS_LOCAL"] = "0"
    try:
        with pytest.raises(Exception) as e:
            client.save_settings(ids["i1"], "https://example.com/x", SECRET)
        assert "ends in /exec" in str(e.value)
    finally:
        os.environ["SKILLOPS_LOCAL"] = "1"


def test_trainer_pages_with_the_fake_system(fake_system):
    ids = fake_system
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"/b/{ids['A']}/mcq")
    assert page["statusCode"] == 200 and "W02" in body(page) and "Import questions" in body(page)
    r = t.post(f"/b/{ids['A']}/mcq/tests/W02/active", {"active": "0"})       # W02 is for batches A and B
    assert r["statusCode"] == 303
    assert next(x for x in fake._st()["tests"] if x["id"] == "W02")["active"] is False
    r = t.post(f"/b/{ids['A']}/mcq/tests/W01/active", {"active": "1"})       # W01 is shared: supervisor only
    assert r["statusCode"] == 400 and "shared by all batches" in body(r)
    assert next(x for x in fake._st()["tests"] if x["id"] == "W01")["active"] is False
    mon = t.get(f"/b/{ids['A']}/mcq/monitor/W02")
    assert mon["statusCode"] == 200 and "Student A1" in body(mon) and "Student B1" not in body(mon)   # own batch only
    panel = t.get(f"/b/{ids['A']}/mcq/monitor/W02/panel")
    assert "+5 min" in body(panel)
    r = t.post(f"/b/{ids['A']}/mcq/monitor/W02/session", {"session": "L0", "action": "add", "minutes": "5"})
    assert r["statusCode"] == 303
    res = t.get(f"/b/{ids['A']}/mcq/results/W01")
    assert "Student A1" in body(res)
    csv = t.get(f"/b/{ids['A']}/mcq/register?format=csv")
    assert csv["statusCode"] == 200 and csv["headers"]["content-type"].startswith("text/csv")


def test_pages_still_load_when_the_test_system_is_down(fake_system, monkeypatch):
    ids = fake_system
    monkeypatch.setattr("time.sleep", lambda s: None)
    client.save_settings(ids["i1"], "fake://down", fake.SECRET)
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = t.get(f"/b/{ids['A']}/mcq")
    assert page["statusCode"] == 200 and "The test system is not answering. Everything else works." in body(page)
    assert t.get(f"/b/{ids['A']}")["statusCode"] == 200
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    d = b.get("/dashboard?programme=all&institution=all")
    assert d["statusCode"] == 200 and "not available" in body(d)
    h = b.get("/health")
    assert h["statusCode"] == 200 and "MCQ test system: DBI Skill Park" in body(h) and "Not working" in body(h)


def test_supervisor_connects_and_tests(aws):
    ids = setup_org()
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    b.get("/admin/mcq")
    r = b.post(f"/admin/mcq/{ids['i1']}", {"url": "fake://demo", "secret": fake.SECRET})
    assert r["statusCode"] == 303
    r = b.post(f"/admin/mcq/{ids['i1']}/test", {})
    page = b.get("/admin/mcq")
    assert "connected" in body(page)
    from skillops.core import export
    blob, _ = export.build_zip()
    assert fake.SECRET.encode() not in blob            # secrets never go into an export
