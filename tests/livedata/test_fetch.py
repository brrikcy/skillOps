"""The fetch window, column order, paging, and the no-skip / no-repeat promise."""
import random

from livehelpers import api, at, fetch_all, make_db, register, set_now, setup_batch, upload
from skillops.livedata import catalog, clock as clk, secret, timeutil

SEC = 1_000_000


def every_second_db(tmp_path, n, start="2025-07-01 00:00:00", step=7):
    from datetime import datetime, timedelta
    t0 = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    rows = []
    for i in range(n):
        t = t0 + timedelta(seconds=step * (i // 3))     # three rows share each time (same release moment)
        rows.append(("txt", i + 1, t.strftime("%Y-%m-%d %H:%M:%S"), i % 5 or None))
    # Column order on purpose not alphabetical, key column not first.
    return make_db(str(tmp_path / "ev.db"), {
        "ev": ("CREATE TABLE ev (zeta TEXT, event_id INTEGER, at_time TEXT, alpha INTEGER)", rows)})


MAP = {"data_start": "2025-07-01", "feed": "ev", "datasets": [
    {"table": "ev", "dataset": "events", "time_column": "at_time", "key_column": "event_id"}]}


def test_rows_have_only_original_columns_in_source_order(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 6), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 10:00")
    status, body = api("GET", "/v1/fetch", {"dataset": "events"}, key=key)
    assert status == 200
    assert body["columns"] == ["zeta", "event_id", "at_time", "alpha"]
    assert all(len(r) == 4 for r in body["rows"])
    assert body["rows"][0] == ["txt", 1, "2025-07-01 00:00:00", None]
    assert list(body.keys()) == ["platform", "dataset", "dataset_version", "since", "until", "data_time_until",
                                 "columns", "rows", "row_count", "next_page_token"]


def test_window_excludes_since_and_includes_until(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 9), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    # Rows 1-3 release at 09:00:00, 4-6 at 09:00:07, 7-9 at 09:00:14.
    set_now("2026-11-16 09:00:07")
    rows, until, _, _ = fetch_all(key, "events")
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6]            # release == until is included
    assert until == "2026-11-16T03:30:07Z"
    set_now("2026-11-16 09:00:14")
    rows, _, _, _ = fetch_all(key, "events", since=until)
    assert [r[1] for r in rows] == [7, 8, 9]                      # release == since is excluded


def test_no_row_skipped_or_repeated_across_fetches_and_pages(aws, tmp_path, monkeypatch):
    """Random fetch moments and random page sizes; the union must be every released row, once."""
    monkeypatch.setitem(__import__("os").environ, "RATE_LIMIT_PER_MINUTE", "100000")
    n = 600
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, n, step=3), MAP, go_live="2026-11-16 09:00", speed="1.7")
    key = register(setup_batch())[1]["api_key"]
    rnd = random.Random(42)
    now = at("2026-11-16 08:59:50")
    since, seen, fetches = "", [], 0
    while len(seen) < n:
        now += rnd.choice([0, 1, 2, 3, 7, 30, 90]) * SEC + rnd.randint(0, 999_999)
        set_now(now)
        rows, until, pages, _ = fetch_all(key, "events", since=since, page_size=rnd.choice([1, 2, 5, 17, 5000]))
        seen.extend(r[1] for r in rows)
        since = until
        fetches += 1
        # Never more than what is released now:
        c = catalog.get_feed("ev")["clock"]
        released = sum(1 for i in range(n) if (3 * (i // 3)) <= clk.floor_offset(c, timeutil.floor_seconds(now)))
        assert len(seen) == released
    assert sorted(seen) == list(range(1, n + 1))
    assert len(seen) == len(set(seen))
    assert seen == sorted(seen)                                    # sorted by release time, then row id
    assert fetches > 10


def test_every_page_of_one_fetch_has_the_same_until(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 30), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 10:00")
    status, p1 = api("GET", "/v1/fetch", {"dataset": "events", "page_size": "10"}, key=key)
    set_now("2026-11-16 11:00")                                     # time moves on between pages
    status, p2 = api("GET", "/v1/fetch", {"dataset": "events", "page_token": p1["next_page_token"], "page_size": "10"}, key=key)
    assert p1["until"] == p2["until"] == "2026-11-16T04:30:00Z"
    assert [r[1] for r in p2["rows"]] == list(range(11, 21))


def test_no_future_rows_from_any_endpoint(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 30, step=60), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 09:01:30")                                  # rows 1-6 released (00:00 and 00:01)
    rows, _, _, _ = fetch_all(key, "events")
    assert [r[1] for r in rows] == [1, 2, 3, 4, 5, 6]
    status, d = api("GET", "/v1/datasets", key=key)
    assert d["datasets"][0]["rows_released"] == 6
    assert "row_count" not in d["datasets"][0] and "rows_total" not in d["datasets"][0]
    status, s = api("GET", "/v1/status")
    assert s["feeds"][0]["datasets"][0]["rows_released"] == 6
    # A forged page token asking for a future `until` is refused (signature check):
    forged = secret.sign_token({"ds": "events", "v": 1, "since": None, "until": at("2026-12-01 00:00"),
                                "after": 0, "page": 1})
    status, b = api("GET", "/v1/fetch", {"dataset": "events", "page_token": forged}, key=key)
    assert status == 400 and b["error"]["code"] == "bad_page_token"
    # A token edited by hand is refused too:
    status, p = api("GET", "/v1/fetch", {"dataset": "events", "page_size": "2"}, key=key)
    tampered = p["next_page_token"][:-3] + ("AAA" if not p["next_page_token"].endswith("AAA") else "BBB")
    status, b = api("GET", "/v1/fetch", {"dataset": "events", "page_token": tampered}, key=key)
    assert status == 400


def test_data_time_until_and_since_echo(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 3), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now(at("2026-11-16 11:30:12") + 400_000)
    status, b = api("GET", "/v1/fetch", {"dataset": "events", "since": "2026-11-16T05:00:00Z"}, key=key)
    assert b["since"] == "2026-11-16T05:00:00Z" and b["until"] == "2026-11-16T06:00:12Z"
    assert b["data_time_until"] == "2025-07-01 02:30:12"
    assert timeutil.parse_utc_iso(b["until"]) <= timeutil.now_us()


def test_wide_rows_end_a_page_early_without_losing_rows(aws, tmp_path, monkeypatch):
    from skillops.livedata import fetch as fetch_mod
    monkeypatch.setattr(fetch_mod, "MAX_PAGE_BYTES", 200)
    set_now("2026-11-16 08:00")
    upload(every_second_db(tmp_path, 40), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 10:00")
    rows, _, pages, _ = fetch_all(key, "events")
    assert [r[1] for r in rows] == list(range(1, 41)) and pages > 5
