"""Live-data tests, copied from ROBOMY DSE and pointed at skillops.livedata.
Shared test setup. Tests run against moto, a fake AWS that lives in memory. No AWS account needed."""
import json
import os
import sqlite3
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "src"))

os.environ.update({
    "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "ap-south-1", "AWS_REGION": "ap-south-1",
    "SIMLAB_SECRET": "unit-test-secret", "SIMLAB_NO_METRICS": "1", "LIVE_DATA_NAME": "SkillOps Live Data",
})
os.environ.pop("USER_POOL_ID", None)

from moto import mock_aws  # noqa: E402

from skillops.livedata import catalog, feedops, loader, ops, secret, store, student_routes, students, timeutil  # noqa: E402,E501,F401

IST = "Asia/Kolkata"


@pytest.fixture
def aws():
    with mock_aws():
        store.reset()
        secret.reset()
        catalog._hist_cache.clear()
        catalog._scan_cache.clear()
        catalog._learner_cache["at"] = 0
        store.create_tables()
        yield
        timeutil.set_fake_now(None)
        store.reset()


def at(text, tz=IST):
    """Real time from a local string, e.g. at('2026-11-16 09:00')."""
    return timeutil.parse_local(text, tz)


def set_now(text_or_us, tz=IST):
    us = text_or_us if isinstance(text_or_us, int) else at(text_or_us, tz)
    timeutil.set_fake_now(us)
    return us


def make_db(path, tables):
    """tables: {name: (create_sql, [rows])}"""
    conn = sqlite3.connect(path)
    for name, (ddl, rows) in tables.items():
        conn.execute(ddl)
        if rows:
            ph = ",".join("?" * len(rows[0]))
            conn.executemany(f'INSERT INTO "{name}" VALUES ({ph})', rows)
    conn.commit()
    conn.close()
    return path


def upload(db_path, mapping, go_live, speed=1, activate=True, force=False, assign=None, only=None):
    feed = mapping.get("feed") or os.path.splitext(os.path.basename(db_path))[0]
    tz = mapping.get("timezone", IST)
    now = timeutil.now_us()
    f = feedops.ensure_feed(feed, mapping["data_start"], tz, at(go_live, tz) if go_live else None,
                            speed, now, force=force, say=lambda m: None)
    src = loader.open_source(db_path)
    for entry in mapping["datasets"]:
        if only and entry["table"] != only:
            continue
        loader.upload_dataset(src, entry, f, activate=activate, say=lambda m: None, assign=assign, workers=2)
    return feed


def event(method, path, query=None, body=None, key=None, claims=None, ip="10.0.0.1"):
    headers = {"content-type": "application/json"}
    if key:
        headers["x-api-key"] = key
    ev = {
        "rawPath": path, "headers": headers, "queryStringParameters": query or None,
        "requestContext": {"http": {"method": method, "sourceIp": ip}, "domainName": "api.example.test"},
        "body": json.dumps(body) if body is not None else None, "isBase64Encoded": False,
    }
    if claims is not None:
        ev["requestContext"]["authorizer"] = {"jwt": {"claims": claims}}
    return ev


def api(method, path, query=None, body=None, key=None, ip="10.0.0.1"):
    """Call the student API. Returns (status, json body)."""
    r = student_routes.handle(event(method, path, query, body, key, ip=ip))
    return r["statusCode"], json.loads(r["body"])


SUPER = ("boss@robomy.test", "superadmin")


def setup_batch(batch="A", approval=False, max_uses=50, minutes=30):
    return students.create_code(batch, max_uses, minutes, approval, "boss@robomy.test", timeutil.now_us())["code"]


def register(code, name="Asha Rao", roll="R001", ip="10.0.0.1"):
    return api("POST", "/v1/register", body={"join_code": code, "full_name": name, "roll_number": roll}, ip=ip)


def fetch_all(key, dataset, since=None, page_size=None):
    """Fetch every page like a good client. Returns (rows, until, pages, last_body)."""
    q = {"dataset": dataset, "since": since or ""}
    if page_size:
        q["page_size"] = str(page_size)
    rows, pages = [], 0
    while True:
        status, body = api("GET", "/v1/fetch", q, key=key)
        assert status == 200, body
        rows.extend(body["rows"])
        pages += 1
        if not body["next_page_token"]:
            return rows, body["until"], pages, body
        q = {"dataset": dataset, "page_token": body["next_page_token"]}
        if page_size:
            q["page_size"] = str(page_size)
