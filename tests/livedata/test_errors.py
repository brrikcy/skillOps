"""Every student-facing error code, with the standard JSON shape."""
import os

from skillops.livedata import ops  # noqa: E402
from livehelpers import api, make_db, register, set_now, setup_batch, upload
from skillops.livedata import feedops, timeutil


def world(tmp_path, approval=False):
    set_now("2026-11-16 08:00")
    db = make_db(str(tmp_path / "p.db"), {"o": ("CREATE TABLE o (id INTEGER, d TEXT)", [(1, "2025-07-01"), (2, "2025-07-02")])})
    upload(db, {"data_start": "2025-07-01", "feed": "practice", "datasets": [
        {"table": "o", "dataset": "practice_orders", "date_column": "d"},
        {"table": "o", "dataset": "hidden_ds", "date_column": "d"}]}, go_live="2026-11-16 09:00")
    feedops.set_active("hidden_ds", False, timeutil.now_us(), say=lambda m: None)
    code = setup_batch(approval=approval, minutes=600)
    set_now("2026-11-16 10:00")
    return code


def check(status, body, want_status, want_code):
    assert status == want_status, body
    assert body["platform"] == "Live Data Simulator"
    assert set(body["error"]) >= {"code", "message", "hint"}
    assert body["error"]["code"] == want_code
    return body["error"]


def test_400_errors(aws, tmp_path):
    key = register(world(tmp_path))[1]["api_key"]
    e = check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": "2026-11-16 03:30"}, key=key), 400, "bad_since")
    assert e["message"] == "since must be a UTC time like 2026-11-16T03:30:00Z"
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": "2026-11-16T03:30:00"}, key=key), 400, "bad_since")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": "2026-11-16T03:30:00 00:00"}, key=key), 400, "bad_since")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": "2030-01-01T00:00:00Z"}, key=key), 400, "bad_since")
    e = check(*api("GET", "/v1/fetch", {"dataset": "practice_ordrs"}, key=key), 400, "unknown_dataset")
    assert e["available"] == ["practice_orders"]                   # inactive datasets are not listed
    check(*api("GET", "/v1/fetch", {}, key=key), 400, "missing_dataset")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "page_token": "abc.def"}, key=key), 400, "bad_page_token")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "page_size": "0"}, key=key), 400, "bad_page_size")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "page_size": "9000"}, key=key), 400, "bad_page_size")


def test_401_errors(aws, tmp_path):
    world(tmp_path)
    e = check(*api("GET", "/v1/whoami"), 401, "missing_key")
    assert "x-api-key" in e["hint"]
    check(*api("GET", "/v1/whoami", {"api_key": "rbk_x"}), 401, "missing_key")
    check(*api("GET", "/v1/whoami", key="rbk_not_a_real_key"), 401, "invalid_key")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders"}, key="rbk_nope"), 401, "invalid_key")


def test_403_errors(aws, tmp_path):
    code = world(tmp_path, approval=True)
    status, body = register(code)
    key = body["api_key"]
    status, who = api("GET", "/v1/whoami", key=key)               # pending: whoami works ...
    assert status == 200 and who["student"]["message"] == "Waiting for your trainer to approve you."
    e = check(*api("GET", "/v1/datasets", key=key), 403, "pending_approval")   # ... nothing else
    assert e["message"] == "Waiting for your trainer to approve you."
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders"}, key=key), 403, "pending_approval")
    sid = "A#R001"
    ops.set_student_status("A", sid, "approve", "boss@robomy.test")
    assert api("GET", "/v1/fetch", {"dataset": "practice_orders"}, key=key)[0] == 200
    check(*api("GET", "/v1/fetch", {"dataset": "hidden_ds"}, key=key), 403, "dataset_inactive")
    ops.set_student_status("A", sid, "disable", "boss@robomy.test")
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders"}, key=key), 403, "disabled")
    check(*api("GET", "/v1/whoami", key=key), 403, "disabled")


def test_409_dataset_replaced(aws, tmp_path):
    key = register(world(tmp_path))[1]["api_key"]
    set_now("2026-11-17 10:00")                                   # both rows released, so there is a second page
    status, b = api("GET", "/v1/fetch", {"dataset": "practice_orders", "page_size": "1"}, key=key)
    token, until = b["next_page_token"], b["until"]
    db = make_db(str(tmp_path / "p2.db"), {"o": ("CREATE TABLE o (id INTEGER, d TEXT)", [(5, "2025-07-01")])})
    upload(db, {"data_start": "2025-07-01", "feed": "practice", "datasets": [
        {"table": "o", "dataset": "practice_orders", "date_column": "d"}]}, go_live=None)
    e = check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": until}, key=key), 409, "dataset_replaced")
    assert "empty since" in e["hint"]
    check(*api("GET", "/v1/fetch", {"dataset": "practice_orders", "page_token": token}, key=key), 409, "dataset_replaced")
    status, b = api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": ""}, key=key)
    assert status == 200 and b["rows"] == [[5, "2025-07-01"]] and b["dataset_version"] == 2
    # After starting again, since works again:
    assert api("GET", "/v1/fetch", {"dataset": "practice_orders", "since": b["until"]}, key=key)[0] == 200


def test_429_rate_limited(aws, tmp_path, monkeypatch):
    monkeypatch.setitem(os.environ, "RATE_LIMIT_PER_MINUTE", "3")
    key = register(world(tmp_path))[1]["api_key"]
    set_now(timeutil.parse_utc_iso("2026-11-16T05:00:10Z"))
    for _ in range(3):
        assert api("GET", "/v1/whoami", key=key)[0] == 200
    e = check(*api("GET", "/v1/whoami", key=key), 429, "rate_limited")
    assert e["retry_after_seconds"] == 50
    set_now(timeutil.parse_utc_iso("2026-11-16T05:01:00Z"))       # a new minute
    assert api("GET", "/v1/whoami", key=key)[0] == 200


def test_404_405_and_health(aws, tmp_path):
    world(tmp_path)
    status, b = api("GET", "/v1/health")
    assert status == 200 and b["status"] == "ok" and b["platform"] == "Live Data Simulator"
    check(*api("GET", "/v1/fetchh"), 404, "not_found")
    check(*api("POST", "/v1/fetch", body={}), 405, "method_not_allowed")
    check(*api("GET", "/v1/register"), 405, "method_not_allowed")


def test_platform_name_is_one_setting(aws, tmp_path, monkeypatch):
    world(tmp_path)
    monkeypatch.setitem(os.environ, "LIVE_DATA_NAME", "Robomy SimLab")
    assert api("GET", "/v1/health")[1]["platform"] == "Robomy SimLab"
    assert api("GET", "/v1/whoami")[1]["platform"] == "Robomy SimLab"
