"""Loading from .db / .csv: several tables, static tables, exact values and types."""
import json

from boto3.dynamodb.conditions import Key

from livehelpers import at, make_db, set_now, upload
from skillops.livedata import catalog, loader, store


def stored_rows(dataset):
    meta = catalog.get_dataset(dataset)
    items = store.query_all("ROWS", KeyConditionExpression=Key("pk").eq(catalog.rows_pk(dataset, meta["gen"])))
    return [json.loads(i["r"]) for i in items], [int(i["sk"]) for i in items]


def test_several_tables_static_and_exact_values(aws, tmp_path):
    set_now("2026-11-10 10:00")
    db = make_db(str(tmp_path / "shop_live.db"), {
        "orders": ("CREATE TABLE orders (order_id INTEGER, order_date TEXT, amount REAL, note TEXT, qty)", [
            (1, "2025-07-01", 10.5, None, 3),
            (1, "2025-07-01", 10.0, "", "3"),          # duplicate key, '' and number-as-text kept
            (2, "2025-07-02", None, "x", 2.0),         # NULL real; a REAL in an untyped column
        ]),
        "cities": ("CREATE TABLE cities (city_id INTEGER, name TEXT)", [(1, "Pune"), (2, "")]),
    })
    feed = upload(db, {"data_start": "2025-07-01", "datasets": [
        {"table": "orders", "dataset": "shop_orders", "date_column": "order_date", "release": "start_of_day",
         "key_column": "order_id"},
        {"table": "cities", "dataset": "shop_cities", "release": "static", "key_column": "city_id"},
    ]}, go_live="2026-11-16 09:00")
    assert feed == "shop_live"
    rows, _ = stored_rows("shop_orders")
    assert rows == [[1, "2025-07-01", 10.5, None, 3], [1, "2025-07-01", 10.0, "", "3"], [2, "2025-07-02", None, "x", 2.0]]
    assert isinstance(rows[1][2], float) and isinstance(rows[0][4], int) and isinstance(rows[1][4], str)
    assert rows[0][3] is None and rows[1][3] == ""          # NULL and '' stay different
    meta = catalog.get_dataset("shop_orders")
    assert meta["columns"] == ["order_id", "order_date", "amount", "note", "qty"]
    assert meta["types"] == ["INTEGER", "TEXT", "REAL", "TEXT", ""]
    assert meta["row_count"] == 3 and meta["version"] == 1
    cities, sks = stored_rows("shop_cities")
    assert cities == [[1, "Pune"], [2, ""]]
    assert all(sk < 0 for sk in sks)                        # static rows sit before data_start


def test_static_rows_visible_from_activation_before_go_live(aws, tmp_path):
    from livehelpers import api, register, setup_batch
    set_now("2026-11-10 10:00")
    db = make_db(str(tmp_path / "f.db"), {"cities": ("CREATE TABLE cities (id INTEGER, name TEXT)", [(1, "A"), (2, "B")])})
    upload(db, {"data_start": "2025-07-01", "datasets": [{"table": "cities", "dataset": "c", "release": "static"}]},
           go_live="2026-11-16 09:00")                      # go-live is days away
    code = setup_batch()
    key = register(code)[1]["api_key"]
    status, body = api("GET", "/v1/fetch", {"dataset": "c"}, key=key)
    assert status == 200 and body["rows"] == [[1, "A"], [2, "B"]]


def test_inspect_reports_counts_types_and_date_ranges(aws, tmp_path):
    db = make_db(str(tmp_path / "x.db"), {"trips": ("CREATE TABLE trips (trip_id INTEGER, request_date TEXT, fare REAL)", [
        (1, "2025-07-03", 1.0), (2, "2025-07-01", 2.0), (3, None, 3.0), (4, "", 4.0)])})
    rep = loader.inspect(loader.open_source(db))
    t = rep[0]
    assert t["table"] == "trips" and t["rows"] == 4
    assert ("fare", "REAL") in t["columns"]
    d = [x for x in t["dates"] if x["column"] == "request_date"][0]
    assert (d["min"], d["max"], d["nulls"], d["empty"]) == ("2025-07-01", "2025-07-03", 1, 1)


def test_csv_input_keeps_text(aws, tmp_path):
    set_now("2026-11-10 10:00")
    p = tmp_path / "riders.csv"
    p.write_text("rider_id,signup_date,city\n7,2025-07-01,Pune\n7,2025-07-02,\n", encoding="utf-8")
    f = upload(str(p), {"data_start": "2025-07-01", "feed": "uber", "datasets": [
        {"table": "riders", "dataset": "riders_live", "date_column": "signup_date"}]}, go_live="2026-11-16 09:00")
    assert f == "uber"
    rows, _ = stored_rows("riders_live")
    assert rows == [["7", "2025-07-01", "Pune"], ["7", "2025-07-02", ""]]


def test_dirty_dates_are_kept_and_inherit_previous_time(aws, tmp_path):
    set_now("2026-11-10 10:00")
    db = make_db(str(tmp_path / "d.db"), {"t": ("CREATE TABLE t (id INTEGER, d TEXT, h INTEGER)", [
        (1, "2025-07-01", 3), (2, "not a date", 5), (3, "2025-07-01", 99), (4, None, None)])})
    upload(db, {"data_start": "2025-07-01", "datasets": [
        {"table": "t", "dataset": "t", "date_column": "d", "hour_column": "h", "release": "end_of_hour"}]},
        go_live="2026-11-16 09:00")
    rows, sks = stored_rows("t")
    assert len(rows) == 4                                   # every row is kept
    offsets = {r[0]: sk // catalog.ROW_ID_SPACE for r, sk in zip(rows, sks)}
    assert offsets[1] == 4 * 3600                           # 03:00 + 1 hour
    assert offsets[2] == offsets[1]                         # bad date -> previous row's time
    assert offsets[3] == 24 * 3600                          # bad hour -> end of that day
    assert offsets[4] == offsets[3]


def test_replace_bumps_version_and_removes_old_rows(aws, tmp_path):
    set_now("2026-11-10 10:00")
    m = {"data_start": "2025-07-01", "feed": "f", "datasets": [{"table": "t", "dataset": "t", "date_column": "d"}]}
    db1 = make_db(str(tmp_path / "a.db"), {"t": ("CREATE TABLE t (id INTEGER, d TEXT)", [(1, "2025-07-01")])})
    upload(db1, m, go_live="2026-11-16 09:00")
    db2 = make_db(str(tmp_path / "b.db"), {"t": ("CREATE TABLE t (id INTEGER, d TEXT)", [(9, "2025-07-01"), (10, "2025-07-02")])})
    upload(db2, m, go_live=None)
    meta = catalog.get_dataset("t")
    assert meta["version"] == 2 and meta["gen"] == 2
    rows, _ = stored_rows("t")
    assert [r[0] for r in rows] == [9, 10]
    old = store.query_all("ROWS", KeyConditionExpression=Key("pk").eq(catalog.rows_pk("t", 1)))
    assert old == []
    assert at("2026-11-16 09:00")  # (keeps helper import used)
