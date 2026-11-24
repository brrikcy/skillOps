"""Adding data to the Live Data Simulator from the web page: upload, read, map, load."""
import os
import sqlite3
import tempfile

import pytest

from helpers import Browser, body, setup_org
from skillops.core import jobs
from skillops.livedata import catalog, imports


def _db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE orders (id INTEGER, order_time TEXT, amount REAL)")
    con.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                    [(i, f"2025-07-01 {9 + i // 4:02d}:{(i % 4) * 15:02d}:00", 100 + i) for i in range(20)])
    con.execute("CREATE TABLE stores (store_id INTEGER, city TEXT)")
    con.executemany("INSERT INTO stores VALUES (?, ?)", [(1, "Pune"), (2, "Kochi")])
    con.commit()
    con.close()
    with open(path, "rb") as fh:
        data = fh.read()
    os.remove(path)
    return data


def _upload(b, filename, data):
    boundary = "----skillops"
    raw = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf\"\r\n\r\n{b.csrf}\r\n"
           f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
           "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    return b.request("POST", "/admin/live/imports", raw=raw, content_type=f"multipart/form-data; boundary={boundary}")


@pytest.fixture
def boss(aws):
    setup_org()
    jobs.INLINE = True
    b = Browser()
    b.sign_in_local("boss@robomy.test")
    b.get("/admin/live")
    yield b
    jobs.INLINE = False


def test_upload_read_map_and_load(boss):
    r = _upload(boss, "shop.db", _db())
    assert r["statusCode"] == 303, body(r)[:300]
    iid = r["headers"]["location"].rsplit("/", 1)[-1]
    item = imports.get(iid)
    assert item["status"] == "ready"
    assert {t["table"]: t["rows"] for t in item["tables"]} == {"orders": 20, "stores": 2}
    page = body(boss.get(f"/admin/live/imports/{iid}"))
    assert "When does a row appear" in page and "order_time" in page
    form = {"feed_new": "shop", "data_start": "2025-07-01", "go_live": "2026-11-16T09:00", "speed": "2",
            "activate": "1",
            "use0": "1", "name0": "shop_orders", "rule0": "time", "time0": "order_time", "key0": "id",
            "use1": "1", "name1": "shop_stores", "rule1": "static"}
    order = [t["table"] for t in item["tables"]]
    if order[0] != "orders":                                  # tables come in the file's order
        form.update({"name0": "shop_stores", "rule0": "static", "name1": "shop_orders", "rule1": "time",
                     "time1": "order_time", "key1": "id"})
        form.pop("time0"), form.pop("key0")
    r = boss.post(f"/admin/live/imports/{iid}", form)
    assert r["statusCode"] == 303, body(r)[:400]
    item = imports.get(iid)
    assert item["status"] == "done", item["message"]
    ds = {d["dataset"]: d for d in catalog.list_datasets()}
    assert ds["shop_orders"]["row_count"] == 20 and ds["shop_orders"]["rule"] == "at_time"
    assert ds["shop_stores"]["row_count"] == 2 and ds["shop_stores"]["rule"] == "static"
    assert ds["shop_orders"]["active"]
    assert catalog.get_feed("shop")["data_start_local"].startswith("2025-07-01")
    assert "Loaded" in body(boss.get("/admin/live"))


def test_bad_choices_are_explained(boss):
    r = _upload(boss, "shop.db", _db())
    iid = r["headers"]["location"].rsplit("/", 1)[-1]
    r = boss.post(f"/admin/live/imports/{iid}", {"feed_new": "shop", "data_start": "2025-07-01"})
    assert r["statusCode"] == 400 and "goes live" in body(r)
    r = boss.post(f"/admin/live/imports/{iid}", {"feed_new": "shop", "data_start": "2025-07-01",
                                                 "go_live": "2026-11-16T09:00"})
    assert r["statusCode"] == 400 and "tick at least one table" in body(r)
    assert imports.get(iid)["status"] == "ready"


def test_wrong_file_type_and_trainers_are_refused(boss):
    assert _upload(boss, "notes.txt", b"hello")["statusCode"] == 400
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    assert t.get("/admin/live")["statusCode"] == 403


def test_register_form_works_without_javascript(aws):
    from skillops.livedata import ops
    ids = setup_org()
    code = ops.create_code(ids["A"], 40, 30, False, "trainer1@robomy.test")["code"]
    s = Browser()
    page = s.get(f"/register?code={code}")
    assert page["statusCode"] == 200 and code in body(page) and "<script src=\"/public-config.js" not in body(page)
    bad = s.post("/register", {"join_code": "JOIN-NOPE-XX", "full_name": "Asha Rao", "roll_number": "DA24-017"},
                 csrf=False)
    assert bad["statusCode"] == 400 and "join code" in body(bad).lower()
    ok = s.post("/register", {"join_code": code, "full_name": "Asha Rao", "roll_number": "DA24-017"}, csrf=False)
    html = body(ok)
    assert ok["statusCode"] == 201 and "rbk_" in html and "SIMLAB_API_URL=" in html
    assert ok["headers"]["cache-control"] == "no-store"
    assert [r["name"] for r in ops.student_rows(ids["A"])] == ["Asha Rao"]
    assert s.get("/live")["headers"]["location"] == "/register"
    assert "Developer docs" in body(s.get("/docs"))
