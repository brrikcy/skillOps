"""Staff actions on Live Data Simulator, used by the batch's Live data page and the supervisor's
feeds page. (Ported from the old staff API; permissions are checked by the page before these run.)"""
from . import catalog, clock as clk, feedops, store, students, timeutil

STATUS_ACTIONS = {"approve": "active", "reject": "rejected", "disable": "disabled", "enable": "active"}


class LiveError(Exception):
    pass


def batch_codes(batch_id):
    """The batch's open join codes. Codes that have expired or are used up are removed as they are found."""
    now = timeutil.now_us()
    out = []
    for c in students.list_codes():
        if c["batch"] != batch_id:
            continue
        v = students.code_view(c, now)
        if v["open"]:
            out.append(v)
        else:
            students.close_code(c["code"])
    return sorted(out, key=lambda c: c.get("created_at") or "", reverse=True)


def create_code(batch_id, max_uses, open_minutes, approval, who):
    try:
        max_uses, open_minutes = int(max_uses), int(open_minutes)
    except (TypeError, ValueError):
        raise LiveError("Maximum uses and minutes must be whole numbers.")
    if not 1 <= max_uses <= 1000 or not 1 <= open_minutes <= 60 * 24 * 14:
        raise LiveError("Uses must be 1 to 1,000 and the open time 1 minute to 14 days.")
    return students.create_code(batch_id, max_uses, open_minutes, bool(approval), who, timeutil.now_us())


def close_code(batch_id, code):
    code = students.normalise_code(code)
    item = students.get_code(code) if code else None
    if not item or item["batch"] != batch_id:
        raise LiveError("That join code does not belong to this batch.")
    students.close_code(code)


def student_rows(batch_id):
    studs = students.list_students(batch_id)
    per = {}
    for a in students.batch_activity(batch_id):
        p = per.setdefault(a["student_id"], {"fetches": 0, "rows": 0, "last": "", "errors": 0})
        p["fetches"] += a.get("fetches", 0)
        p["rows"] += a.get("rows_total", 0)
        p["errors"] += a.get("errors", 0)
        p["last"] = max(p["last"], a.get("last_at", "") or "")
    out = []
    for s in studs:
        p = per.get(s["student_id"], {"fetches": 0, "rows": 0, "last": "", "errors": 0})
        out.append({"student_id": s["student_id"], "name": s["name"], "roll": s.get("roll_display", s["roll"]),
                    "status": s["status"], "registered_at": s["registered_at"], "last_fetch": p["last"] or "",
                    "fetches": p["fetches"], "rows": p["rows"], "errors": p["errors"]})
    return sorted(out, key=lambda r: r["roll"])


def set_student_status(batch_id, student_id, action, who):
    s = students.get_student(student_id)
    if not s or s["batch"] != batch_id:
        raise LiveError("That student is not in this batch.")
    if action == "reset_key":
        return {"api_key": students.replace_key(s, timeutil.now_us()), "name": s["name"]}
    new_status = STATUS_ACTIONS.get(action)
    if not new_status:
        raise LiveError("Unknown action.")
    store.t("STUDENTS").update_item(Key={"pk": f"STU#{student_id}"}, UpdateExpression="SET #s = :s, status_by = :by",
                                    ExpressionAttributeNames={"#s": "status"},
                                    ExpressionAttributeValues={":s": new_status, ":by": who})
    return {"status": new_status, "name": s["name"]}


def approve_all(batch_id, who):
    n = 0
    for s in students.list_students(batch_id):
        if s["status"] == "pending":
            set_student_status(batch_id, s["student_id"], "approve", who)
            n += 1
    return n


def activity(batch_id):
    studs = {s["student_id"]: s for s in students.list_students(batch_id)}
    rows = []
    for a in students.batch_activity(batch_id):
        s = studs.get(a["student_id"])
        if not s:
            continue
        rows.append({"student_id": a["student_id"], "name": s["name"], "roll": s.get("roll_display", s["roll"]),
                     "dataset": a.get("dataset"), "fetches": a.get("fetches", 0), "pages": a.get("pages", 0),
                     "rows": a.get("rows_total", 0), "last_fetch": a.get("last_at"), "errors": a.get("errors", 0),
                     "last_error": a.get("last_error")})
    return sorted(rows, key=lambda r: (r["roll"], r["dataset"] or ""))


def summary(batch_id):
    """Counts for the dashboard: registered, fetched at least once, open join codes."""
    rows = student_rows(batch_id)
    return {"registered": len([r for r in rows if r["status"] in ("active", "pending")]),
            "active": len([r for r in rows if r["status"] == "active"]),
            "pending": len([r for r in rows if r["status"] == "pending"]),
            "fetched": len([r for r in rows if r["fetches"]]),
            "open_codes": [c for c in batch_codes(batch_id) if c["open"]]}


def feeds():
    now = timeutil.now_us()
    return [feedops.feed_report(f["feed"], now) for f in catalog.list_feeds()]


def pause(feed, resume=False):
    msgs = []
    (feedops.resume if resume else feedops.pause)(feed, timeutil.now_us(), say=msgs.append)
    return msgs


def set_clock(feed_name, go_live_text, speed, force=False, from_now=False):
    feed = catalog.get_feed(feed_name)
    if not feed:
        raise LiveError("That feed does not exist.")
    speed = speed or feed["clock"]["speed"]
    try:
        clk.parse_speed(speed)
        go = timeutil.parse_local(go_live_text.replace("T", " "), feed["tz"]) if go_live_text else \
            clk.effective_go_live(feed["clock"])
    except ValueError as e:
        raise LiveError(str(e))
    msgs = []
    feedops.set_clock(feed["feed"], go, speed, timeutil.now_us(), force=force, from_now=from_now, say=msgs.append)
    return msgs


def set_dataset_active(dataset, active):
    msgs = []
    feedops.set_active(dataset, active, timeutil.now_us(), say=msgs.append)
    return msgs


# ------------------------------------------------------------------ deleting data

def delete_dataset(name, workers=8, say=print):
    """Remove a dataset: its rows (every upload of it), its histogram and its entry. Students fetching it get
    "unknown dataset" afterwards. Rows go in the background job that calls this."""
    from boto3.dynamodb.conditions import Key
    from . import loader
    from .store import t
    meta = catalog.get_dataset(name)
    if not meta:
        raise LiveError(f"There is no dataset called {name}.")
    for gen in {int(meta.get("gen") or 0), int(meta.get("pending_gen") or 0)} - {0}:
        say(f"Removing the rows of {name} (upload {gen})...")
        loader.delete_partition(catalog.rows_pk(name, gen), workers, say)
        catalog.delete_histogram(name, gen)
    # any older uploads left behind by an earlier "keep old" load
    for item in t("ROWS").query(KeyConditionExpression=Key("pk").eq(f"HIST#{name}")).get("Items", []):
        loader.delete_partition(catalog.rows_pk(name, int(item["sk"])), workers, say)
        catalog.delete_histogram(name, int(item["sk"]))
    t("CONFIG").delete_item(Key={"pk": f"DATASET#{name}", "sk": "META"})
    catalog._scan_cache.clear()
    return meta


def delete_feed(feed, say=print):
    """Remove a feed with all its datasets."""
    from .store import t
    if not catalog.get_feed(feed):
        raise LiveError(f"There is no feed called {feed}.")
    names = [d["dataset"] for d in catalog.list_datasets(feed)]
    for n in names:
        delete_dataset(n, say=say)
    t("CONFIG").delete_item(Key={"pk": f"FEED#{feed}", "sk": "META"})
    catalog._scan_cache.clear()
    return names
