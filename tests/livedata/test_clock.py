"""Release-time rules, speed, pause / resume and set-clock."""
import random
from fractions import Fraction

import pytest

from livehelpers import api, at, fetch_all, make_db, register, set_now, setup_batch, upload
from skillops.livedata import catalog, clock as clk, feedops, timeutil

HOUR = 3600 * 1_000_000


def released_ids(key, ds):
    return [r[0] for r in fetch_all(key, ds)[0]]


def trips_db(tmp_path, rows):
    return make_db(str(tmp_path / "uber_live.db"), {
        "trips": ("CREATE TABLE trips (trip_id INTEGER, request_date TEXT, request_hour INTEGER)", rows)})


MAP = {"data_start": "2025-07-01", "feed": "uber", "datasets": [
    {"table": "trips", "dataset": "trips", "date_column": "request_date", "hour_column": "request_hour",
     "release": "end_of_hour"}]}


def test_end_of_hour(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(trips_db(tmp_path, [(1, "2025-07-01", 0), (2, "2025-07-01", 1)]), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 09:59:59")
    assert released_ids(key, "trips") == []
    set_now("2026-11-16 10:00:00")                     # hour 0 ends at 01:00 data time = 10:00 real
    assert released_ids(key, "trips") == [1]
    set_now("2026-11-16 11:00:00")
    assert released_ids(key, "trips") == [1, 2]


def test_start_and_end_of_day(aws, tmp_path):
    set_now("2026-11-16 08:00")
    db = make_db(str(tmp_path / "f.db"), {"r": ("CREATE TABLE r (id INTEGER, d TEXT)", [(1, "2025-07-01"), (2, "2025-07-02")])})
    upload(db, {"data_start": "2025-07-01", "datasets": [
        {"table": "r", "dataset": "a", "date_column": "d", "release": "start_of_day"},
        {"table": "r", "dataset": "b", "date_column": "d", "release": "end_of_day"}]}, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 09:00")
    assert released_ids(key, "a") == [1] and released_ids(key, "b") == []
    set_now("2026-11-17 09:00")
    assert released_ids(key, "a") == [1, 2] and released_ids(key, "b") == [1]


def test_speed_two(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(trips_db(tmp_path, [(1, "2025-07-01", 1), (2, "2025-07-01", 3)]), MAP, go_live="2026-11-16 09:00", speed=2)
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 10:00")                        # 1 real hour = 2 data hours -> hour 1 (ends 02:00) is out
    assert released_ids(key, "trips") == [1]
    set_now("2026-11-16 10:59:59")
    assert released_ids(key, "trips") == [1]
    set_now("2026-11-16 11:00")                        # 4 data hours -> hour 3 ends
    assert released_ids(key, "trips") == [1, 2]


def test_pause_and_resume_continue_from_same_data_time(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(trips_db(tmp_path, [(i, "2025-07-01", i) for i in range(6)]), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 11:30")                        # hours 0,1 out (ends 01:00, 02:00)
    rows, until, _, body = fetch_all(key, "trips")
    assert [r[0] for r in rows] == [0, 1]
    feedops.pause("uber", timeutil.now_us(), say=lambda m: None)
    set_now("2026-11-16 15:00")                        # long pause: nothing new
    rows2, until2, _, b2 = fetch_all(key, "trips", since=until)
    assert rows2 == [] and b2["data_time_until"] == "2025-07-01 02:30:00"
    feedops.resume("uber", timeutil.now_us(), say=lambda m: None)
    set_now("2026-11-16 15:29:59")
    assert fetch_all(key, "trips", since=until2)[0] == []
    set_now("2026-11-16 15:30:00")                     # data 03:00 reached 30 min after resume
    rows3, _, _, b3 = fetch_all(key, "trips", since=until2)
    assert [r[0] for r in rows3] == [2] and b3["data_time_until"] == "2025-07-01 03:00:00"
    # Rows released before the pause keep their release times:
    c = catalog.get_feed("uber")["clock"]
    assert clk.release_at(c, 3600) == at("2026-11-16 10:00")


def test_set_clock_needs_force_after_start_and_bumps_version(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(trips_db(tmp_path, [(1, "2025-07-01", 0)]), MAP, go_live="2026-11-16 09:00")
    # Before go-live: free to change.
    feedops.set_clock("uber", at("2026-11-16 10:00"), 1, timeutil.now_us(), say=lambda m: None)
    assert catalog.get_dataset("trips")["version"] == 1
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 12:00")
    _, until, _, _ = fetch_all(key, "trips")
    with pytest.raises(feedops.FeedError, match="--force"):
        feedops.set_clock("uber", at("2026-11-16 11:00"), 2, timeutil.now_us(), say=lambda m: None)
    feedops.set_clock("uber", at("2026-11-16 11:00"), 2, timeutil.now_us(), force=True, say=lambda m: None)
    assert catalog.get_dataset("trips")["version"] == 2
    status, body = api("GET", "/v1/fetch", {"dataset": "trips", "since": until}, key=key)
    assert status == 409 and body["error"]["code"] == "dataset_replaced"
    status, body = api("GET", "/v1/fetch", {"dataset": "trips", "since": ""}, key=key)
    assert status == 200 and body["dataset_version"] == 2


def test_speed_from_now_keeps_history(aws, tmp_path):
    set_now("2026-11-16 08:00")
    upload(trips_db(tmp_path, [(i, "2025-07-01", i) for i in range(8)]), MAP, go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    set_now("2026-11-16 11:00")
    rows, until, _, _ = fetch_all(key, "trips")
    feedops.set_clock("uber", None, 4, timeutil.now_us(), from_now=True, say=lambda m: None)
    assert catalog.get_dataset("trips")["version"] == 1
    set_now("2026-11-16 12:00")                        # data 02:00 + 4 hours = 06:00
    rows2, _, _, b = fetch_all(key, "trips", since=until)
    assert [r[0] for r in rows] == [0, 1] and [r[0] for r in rows2] == [2, 3, 4, 5]
    assert b["data_time_until"] == "2025-07-01 06:00:00"


def test_release_at_is_the_exact_inverse_of_the_clock():
    rnd = random.Random(1)
    c = clk.new_clock(10 ** 15, "1.5")
    t = 10 ** 15
    for _ in range(6):                                  # a messy history of pauses and speed changes
        t += rnd.randint(1, 5000) * 1_000_000
        c = clk.pause(c, t)
        t += rnd.randint(1, 5000) * 1_000_000
        c = clk.resume(c, t)
        t += rnd.randint(1, 5000) * 1_000_000
        c = clk.change_speed_from_now(c, t, str(Fraction(rnd.randint(1, 30), rnd.randint(1, 7))))
    for _ in range(500):
        o = rnd.randint(-10_000, 40_000)
        r = clk.release_at(c, o)
        assert clk.data_offset(c, r) >= o > clk.data_offset(c, r - 1)


def test_pause_before_go_live_shifts_go_live(aws):
    c = clk.new_clock(at("2026-11-16 09:00"), 1)
    c = clk.pause(c, at("2026-11-15 09:00"))
    c = clk.resume(c, at("2026-11-15 12:00"))
    assert clk.effective_go_live(c) == at("2026-11-16 12:00")
