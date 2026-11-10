"""The trainer's reference client against the real API code (through the local HTTP server)."""
import os
import sqlite3
import sys

import pytest

from livehelpers import ROOT, make_db, register, set_now, setup_batch, upload

sys.path.insert(0, os.path.join(ROOT, "reference"))
sys.path.insert(0, ROOT)
import live_client  # noqa: E402
import local_server  # noqa: E402


@pytest.fixture
def server(aws):
    srv, url = local_server.start(0, quiet=True)
    yield url
    srv.shutdown()
    os.environ.pop("PUBLIC_API_URL", None)


def student_setup(tmp_path, url):
    set_now("2026-11-16 08:00")
    src = make_db(str(tmp_path / "live.db"), {"trips": ("CREATE TABLE trips (trip_id INTEGER, d TEXT, h INTEGER, fare REAL)",
                                                         [(i, "2025-07-01", i % 24, None if i % 4 else 9.5) for i in range(40)])})
    upload(src, {"data_start": "2025-07-01", "feed": "uber", "datasets": [
        {"table": "trips", "dataset": "uber_trips_live", "date_column": "d", "hour_column": "h", "release": "end_of_hour"}]},
        go_live="2026-11-16 09:00")
    key = register(setup_batch())[1]["api_key"]
    env = tmp_path / ".env"
    env.write_text(f"SIMLAB_API_KEY={key}\nSIMLAB_API_URL={url}\n", encoding="utf-8")
    db = make_db(str(tmp_path / "uber.db"), {"trips": ("CREATE TABLE trips (trip_id INTEGER, d TEXT, h INTEGER, fare REAL)",
                                                        [(-1, "2025-06-30", 23, 1.0)])})
    return db, str(env)


def test_two_loads_no_skip_no_repeat(server, tmp_path):
    db, env = student_setup(tmp_path, server)
    set_now("2026-11-16 12:30")
    r1 = live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    set_now("2026-11-16 15:00")
    r2 = live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    conn = sqlite3.connect(db)
    ids = [r[0] for r in conn.execute("SELECT trip_id FROM staging_trips")]
    log = conn.execute("SELECT since, until, rows, status, dataset_version FROM load_log").fetchall()
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    # 12:30 real = 03:30 data: hours 0-2 have ended. 15:00 real = 06:00 data: hours 0-5.
    assert (r1["rows"], r2["rows"]) == (6, 6)
    assert sorted(ids) == sorted(i for i in range(40) if i % 24 < 6) and len(set(ids)) == len(ids)
    assert ids[:6] == [0, 24, 1, 25, 2, 26]                           # by release time, then row id
    assert [x[3] for x in log] == ["ok", "ok"] and log[1][0] == log[0][1]
    assert r2["rows"] > 0 and mode == "wal"


def test_failure_rolls_back_and_since_does_not_move(server, tmp_path, monkeypatch):
    db, env = student_setup(tmp_path, server)
    set_now("2026-11-16 12:30")
    live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    set_now("2026-11-16 15:00")
    real = live_client.write_log

    def broken(conn, *a, **k):          # fail while writing the 'ok' row, inside the transaction
        if a[5] == "ok":
            raise RuntimeError("disk full (simulated)")
        return real(conn, *a, **k)
    monkeypatch.setattr(live_client, "write_log", broken)
    with pytest.raises(RuntimeError):
        live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    monkeypatch.setattr(live_client, "write_log", real)
    conn = sqlite3.connect(db)
    n_after_fail = conn.execute("SELECT COUNT(*) FROM staging_trips").fetchone()[0]
    statuses = [r[0] for r in conn.execute("SELECT status FROM load_log")]
    conn.close()
    assert statuses == ["ok", "failed"]
    r = live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    conn = sqlite3.connect(db)
    ids = [x[0] for x in conn.execute("SELECT trip_id FROM staging_trips")]
    conn.close()
    assert n_after_fail == 6 and r["rows"] == 6 and len(ids) == len(set(ids)) == 12


def test_stops_when_columns_differ(server, tmp_path):
    db, env = student_setup(tmp_path, server)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE trips ADD COLUMN extra TEXT")
    conn.commit()
    conn.close()
    set_now("2026-11-16 12:30")
    with pytest.raises(live_client.StopLoad, match="Columns differ"):
        live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)


def test_stops_when_dataset_replaced(server, tmp_path):
    db, env = student_setup(tmp_path, server)
    set_now("2026-11-16 12:30")
    live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    src = make_db(str(tmp_path / "live2.db"), {"trips": ("CREATE TABLE trips (trip_id INTEGER, d TEXT, h INTEGER, fare REAL)",
                                                          [(100, "2025-07-01", 0, 1.0)])})
    upload(src, {"data_start": "2025-07-01", "feed": "uber", "datasets": [
        {"table": "trips", "dataset": "uber_trips_live", "date_column": "d", "hour_column": "h", "release": "end_of_hour"}]},
        go_live=None)
    with pytest.raises(live_client.StopLoad, match="dataset_replaced"):
        live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    live_client.reset(db, "uber_trips_live", "trips")
    r = live_client.load_live(db, "uber_trips_live", "trips", env, verbose=False)
    assert r["rows"] == 1 and r["dataset_version"] == 2
