"""Join codes and registration."""
import os
import re

from skillops.livedata import ops, students  # noqa: E402
from livehelpers import api, at, fetch_all, make_db, register, set_now, setup_batch, upload
from skillops.livedata import students

BAD = "This join code is not valid or has closed."


def test_code_format_has_no_look_alike_characters(aws):
    for _ in range(200):
        c = students.new_code_text()
        assert re.fullmatch(r"JOIN-[A-Z2-9]{4}-[A-Z2-9]{2}", c)
        assert not set(c[5:]) & set("0O1IL")
    assert students.normalise_code("join 7kq4 px") == "JOIN-7KQ4-PX"


def test_wrong_expired_closed_and_used_up_codes_give_the_same_message(aws):
    set_now("2026-11-16 09:00")
    expired = setup_batch("A", minutes=10)
    used_up = students.create_code("A", 1, 30, False, "x", at("2026-11-16 09:00"))["code"]
    closed = students.create_code("A", 50, 30, False, "x", at("2026-11-16 09:00"))["code"]
    students.close_code(closed)
    assert register(used_up, roll="R1")[0] == 201
    set_now("2026-11-16 09:11")
    results = [register(c, roll="R9") for c in ("JOIN-ZZZZ-ZZ", expired, closed, used_up, "nonsense")]
    for status, body in results:
        assert status == 400 and body["error"]["code"] == "join_code_invalid"
        assert body["error"]["message"] == BAD


def test_reregister_same_roll_replaces_key_and_keeps_history(aws, tmp_path):
    set_now("2026-11-16 08:00")
    db = make_db(str(tmp_path / "p.db"), {"o": ("CREATE TABLE o (id INTEGER, d TEXT)", [(1, "2025-07-01")])})
    upload(db, {"data_start": "2025-07-01", "datasets": [{"table": "o", "dataset": "ds", "date_column": "d"}]},
           go_live="2026-11-16 09:00")
    code = setup_batch("A", minutes=600)
    old_key = register(code, name="Asha Rao", roll="da24-017")[1]["api_key"]
    set_now("2026-11-16 10:00")
    fetch_all(old_key, "ds")
    status, body = register(code, name="  asha   RAO ", roll="DA24-017")      # same person, new laptop
    assert status == 201 and "old API key has stopped working" in body["message"]
    new_key = body["api_key"]
    assert new_key != old_key
    assert api("GET", "/v1/whoami", key=old_key)[1]["error"]["code"] == "invalid_key"
    assert api("GET", "/v1/whoami", key=new_key)[0] == 200
    rows = ops.student_rows("A")
    assert len(rows) == 1
    s = rows[0]
    assert s["fetches"] == 1 and s["rows"] == 1 and students.get_student(s["student_id"])["key_count"] == 2
    # Different name with the same roll number is refused:
    status, body = register(code, name="Someone Else", roll="DA24-017")
    assert status == 409 and body["error"]["code"] == "roll_number_taken"


def test_pending_student_only_whoami(aws):
    set_now("2026-11-16 09:00")
    code = setup_batch("A", approval=True)
    status, body = register(code)
    assert body["student"]["status"] == "pending"
    key = body["api_key"]
    assert api("GET", "/v1/whoami", key=key)[1]["student"]["message"] == "Waiting for your trainer to approve you."
    assert api("GET", "/v1/datasets", key=key)[1]["error"]["code"] == "pending_approval"
    assert api("GET", "/v1/fetch", {"dataset": "x"}, key=key)[1]["error"]["code"] == "pending_approval"


def test_keys_are_stored_only_as_hashes(aws):
    from skillops.livedata import store
    set_now("2026-11-16 09:00")
    key = register(setup_batch("A"))[1]["api_key"]
    dump = repr(store.scan_all("STUDENTS"))
    assert key not in dump
    assert students.secret.hash_key(key) in dump


def test_register_rate_limited_per_ip_and_per_code(aws, monkeypatch):
    set_now("2026-11-16 09:00:05")
    code = setup_batch("A")
    monkeypatch.setitem(os.environ, "REGISTER_LIMIT_PER_IP_PER_MINUTE", "3")
    for i in range(3):
        assert register(code, roll=f"R{i}", ip="1.1.1.1")[0] == 201
    status, body = register(code, roll="R9", ip="1.1.1.1")
    assert status == 429 and body["error"]["retry_after_seconds"] == 55
    assert register(code, roll="R9", ip="2.2.2.2")[0] == 201              # another IP is fine
    monkeypatch.setitem(os.environ, "REGISTER_LIMIT_PER_IP_PER_MINUTE", "100")
    monkeypatch.setitem(os.environ, "REGISTER_LIMIT_PER_CODE_PER_MINUTE", "5")
    status, body = register(code, roll="R10", ip="3.3.3.3")                # 5th use of this code this minute
    assert status == 201
    status, body = register(code, roll="R11", ip="4.4.4.4")
    assert status == 429


def test_join_code_uses_count_and_close(aws):
    set_now("2026-11-16 09:00")
    code = setup_batch("A", max_uses=2)
    register(code, roll="R1")
    c = ops.batch_codes("A")[0]
    assert c["uses"] == 1 and c["open"] and c["seconds_left"] == 30 * 60
    ops.close_code("A", code)
    assert register(code, roll="R2")[1]["error"]["message"] == BAD
