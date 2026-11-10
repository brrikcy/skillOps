"""A first end-to-end check: upload, register, fetch."""
from livehelpers import api, fetch_all, make_db, register, set_now, setup_batch, upload


def test_end_to_end(aws, tmp_path):
    rows = [(i, f"2025-07-01", h, f"r{i}") for i, h in enumerate([0, 0, 1, 2, 5], start=1)]
    db = make_db(str(tmp_path / "uber_live.db"), {
        "trips": ("CREATE TABLE trips (trip_id INTEGER, request_date TEXT, request_hour INTEGER, note TEXT)", rows)})
    set_now("2026-11-16 08:00")
    upload(db, {"data_start": "2025-07-01", "datasets": [
        {"table": "trips", "dataset": "uber_trips_live", "date_column": "request_date",
         "hour_column": "request_hour", "release": "end_of_hour", "key_column": "trip_id"}]},
        go_live="2026-11-16 09:00")
    code = setup_batch()
    status, body = register(code)
    assert status == 201, body
    key = body["api_key"]

    set_now("2026-11-16 10:30")   # data time 01:30 -> hour-0 rows (released 01:00) are out
    got, until, pages, last = fetch_all(key, "uber_trips_live")
    assert [r[0] for r in got] == [1, 2]
    assert last["data_time_until"] == "2025-07-01 01:30:00"
    set_now("2026-11-16 12:00")
    got2, until2, _, _ = fetch_all(key, "uber_trips_live", since=until)
    assert [r[0] for r in got2] == [3, 4]
    status, body = api("GET", "/v1/datasets", key=key)
    assert status == 200 and body["datasets"][0]["rows_released"] == 4
