"""Adding data to the Live Data Simulator from the web page.

1. The supervisor uploads a SQLite .db or a .csv file (straight to S3 in AWS).
2. A background job opens it and lists every table: rows, columns, and the date-like columns.
3. The supervisor says, per table: the dataset name, how rows are released (by a time column, by a date
   column, by date + hour, or all at once), the feed, data start, go-live and speed.
4. A second background job loads the rows (the same loader as admin.py) and reports what it did.

Very large files (more than a few million rows) are still best loaded with admin.py from a laptop:
a background job has 15 minutes at most.

Data:  CORE  LDIMP#<id>  META   {id, filename, key, size, status, tables, message, result, who, at}
       S3    livedata/incoming/<id>/<file name>   the uploaded file (removed once loaded)
"""
import os
import re
import tempfile

from ..core import config, jobs, store, util
from ..core.util import NotFound, UserError
from . import catalog, feedops, loader, timeutil

TZ = "Asia/Kolkata"
MAX_BYTES = 400 * 1024 * 1024
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
STATUS_TEXT = {"inspecting": "Reading the file…", "ready": "Ready to map", "loading": "Loading rows…",
               "done": "Loaded", "failed": "Failed"}


def _ext(filename):
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in (".db", ".sqlite", ".sqlite3"):
        return "db"
    if ext == ".csv":
        return "csv"
    raise UserError("Please upload a SQLite database (.db) or a .csv file.")


def new_prefix():
    """Where the browser's direct upload goes in S3: the file keeps its own name under this prefix."""
    iid = util.new_id("imp", 8)
    return iid, f"livedata/incoming/{iid}/"


KEY_RE = re.compile(r"^livedata/incoming/(imp-[a-z0-9]{8})/([^/]{1,200})$")


def get(iid):
    item = store.get("CORE", f"LDIMP#{iid}", consistent=True)
    if not item:
        raise NotFound("This upload does not exist.", "Upload the file again.")
    item["status_text"] = STATUS_TEXT.get(item["status"], item["status"])
    return item


def recent(limit=10):
    items = store.by_gsi1("TYPE#LDIMP")
    for i in items:
        i["status_text"] = STATUS_TEXT.get(i["status"], i["status"])
    return sorted(items, key=lambda i: i["at"], reverse=True)[:limit]


def start(filename, who, data=None, key=None, iid=None):
    """Record an upload and start reading it. Give data (laptop) or the S3 key it was uploaded to."""
    if data is not None:
        _ext(filename)
        iid, prefix = new_prefix()
        key = prefix + re.sub(r"[^A-Za-z0-9_.\-]", "_", filename or "data")[-120:]
        if len(data) > MAX_BYTES:
            raise UserError("The file is larger than 400 MB.", "Load it with admin.py from a laptop instead.")
        store.s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=data)
        size = len(data)
    else:
        m = KEY_RE.match(key or "")
        if not m:
            raise UserError("The upload did not finish.", "Upload the file again.")
        iid, filename = m.group(1), m.group(2)
        size = store.s3().head_object(Bucket=config.runbook_bucket(), Key=key)["ContentLength"]
    _ext(filename)
    if not store.put_new("CORE", {"pk": f"LDIMP#{iid}", "sk": "META", "type": "ldimport", "id": iid,
                                  "filename": (filename or "data")[:200], "key": key, "size": size,
                                  "status": "inspecting", "tables": [], "message": "", "result": {},
                                  "who": who, "at": util.now_iso(), "gsi1pk": "TYPE#LDIMP", "gsi1sk": util.now_iso()}):
        return iid                      # the same upload page loaded twice: one import
    jobs.start("live_inspect", who, {"import_id": iid}, label=f"Read {filename}")
    return iid


def _download(item):
    """The uploaded file, on this machine's disk (Lambda: /tmp)."""
    suffix = "." + _ext(item["filename"])
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    store.s3().download_file(config.runbook_bucket(), item["key"], path)
    return path


def _csv_table(item):
    """A CSV file is one table, named after the file."""
    return re.sub(r"[^A-Za-z0-9_]", "_", os.path.splitext(item["filename"])[0])[:60] if _ext(item["filename"]) == "csv" else None


def _set(iid, **values):
    store.update("CORE", f"LDIMP#{iid}", "META", values)


@jobs.register("live_inspect")
def job_inspect(job):
    iid = job["params"]["import_id"]
    item = get(iid)
    try:
        path = _download(item)
    except Exception:
        _set(iid, status="failed", message=f"The uploaded file could not be fetched (job {job['id']}). Upload it again.")
        raise
    try:
        src = loader.open_source(path, _csv_table(item))
        try:
            report = loader.inspect(src)
        finally:
            src.close()
    except Exception as e:  # noqa: BLE001  (an unreadable file is the supervisor's to fix, not a crash)
        _set(iid, status="failed", message=f"The file could not be read as {'CSV' if _ext(item['filename']) == 'csv' else 'SQLite'}: {e}")
        return {"message": "The file could not be read."}
    finally:
        os.remove(path)
    tables = [{"table": t["table"], "rows": t["rows"], "columns": [c for c, _ in t["columns"]],
               "types": [typ or "" for _, typ in t["columns"]],
               "dates": [{k: (str(v) if v is not None else "") for k, v in d.items()} for d in t["dates"]]}
              for t in report]
    _set(iid, status="ready", tables=tables,
         message=f"Found {len(tables)} table(s), {sum(t['rows'] for t in tables):,} rows in all.")
    return {"message": f"Found {len(tables)} table(s)."}


# ------------------------------------------------------------------ the supervisor's choices

def plan(iid, form):
    """Check the mapping form and turn it into the loader's mapping. Raises UserError with a plain message."""
    item = get(iid)
    if item["status"] not in ("ready", "failed") or not item["tables"]:
        raise UserError("This upload is not ready to load.", "Wait until it has been read, then refresh.")
    feed = (form.get("feed_new") or form.get("feed") or "").strip()
    if not NAME_RE.match(feed):
        raise UserError("Please give the feed a short name (letters, digits, _ and -).")
    existing = catalog.get_feed(feed)
    data_start = (form.get("data_start") or "").strip()
    if existing:
        data_start = existing["data_start_local"][:19]
    elif not re.match(r"^\d{4}-\d{2}-\d{2}", data_start):
        raise UserError("Please give the data start: the date the data begins, for example 2025-07-01.")
    go_live = (form.get("go_live") or "").strip()
    speed = (form.get("speed") or "").strip() or None
    if not existing and not go_live:
        raise UserError("This feed is new. Please say when it goes live.")
    entries = []
    taken = {d["dataset"] for d in catalog.list_datasets()}
    for k, t in enumerate(item["tables"]):
        if form.get(f"use{k}") != "1":
            continue
        name = (form.get(f"name{k}") or t["table"]).strip()
        if not NAME_RE.match(name):
            raise UserError(f"Dataset name for {t['table']}: use only letters, digits, _ and - (up to 64).")
        mode = form.get(f"rule{k}", "static")
        entry = {"table": t["table"], "dataset": name}
        col = lambda f: (form.get(f"{f}{k}") or "").strip()  # noqa: E731
        if mode == "time":
            entry["time_column"] = col("time")
        elif mode in ("start_of_day", "end_of_day"):
            entry["date_column"], entry["release"] = col("date"), mode
        elif mode == "hour":
            entry["date_column"], entry["hour_column"] = col("date"), col("hour")
        else:
            entry["release"] = "static"
        for c in ("time_column", "date_column", "hour_column"):
            if c in entry and entry[c] not in t["columns"]:
                raise UserError(f"{t['table']}: please choose the {c.split('_')[0]} column.")
        if col("key") and col("key") in t["columns"]:
            entry["key_column"] = col("key")
        try:
            loader.rule_of(entry)
        except ValueError as e:
            raise UserError(str(e))
        old = catalog.get_dataset(name)
        if old and old.get("feed") != feed:
            raise UserError(f"Dataset {name} already belongs to feed {old['feed']}.", "Choose another name.")
        entry["replaces"] = name in taken
        entries.append(entry)
    if not entries:
        raise UserError("Please tick at least one table to load.")
    if len({e["dataset"] for e in entries}) != len(entries):
        raise UserError("Two tables have the same dataset name.")
    return {"feed": feed, "new_feed": not existing, "data_start": data_start, "go_live": go_live, "speed": speed,
            "activate": form.get("activate") == "1", "entries": entries}


def load(iid, form, who):
    p = plan(iid, form)
    _set(iid, status="loading", message="Waiting to start…", plan=p)
    jobs.start("live_load", who, {"import_id": iid}, label=f"Load {get(iid)['filename']}")
    return p


@jobs.register("live_load")
def job_load(job):
    from ..core import audit
    iid = job["params"]["import_id"]
    item = get(iid)
    p = item["plan"]
    now = timeutil.now_us()

    def say(msg):
        jobs.progress(job["id"], msg)
        _set(iid, message=str(msg)[:500])
    try:
        go = timeutil.parse_local(p["go_live"], TZ) if p.get("go_live") else None
        f = feedops.ensure_feed(p["feed"], p["data_start"], TZ, go, p.get("speed"), now, say=say)
        path = _download(item)
        out = []
        try:
            src = loader.open_source(path, _csv_table(item))
            try:
                for e in p["entries"]:
                    entry = {k: v for k, v in e.items() if k != "replaces"}
                    out.append(loader.upload_dataset(src, entry, f, activate=True if p.get("activate") else None,
                                                     say=say))
            finally:
                src.close()
        finally:
            os.remove(path)
    except (feedops.FeedError, ValueError, UserError) as e:
        _set(iid, status="failed", message=str(e))
        audit.write(job["who"], "superadmin", "load Live Data Simulator data", target=item["filename"],
                    result="failed", detail=str(e)[:300])
        return {"message": f"Not loaded: {e}"}
    except Exception:
        _set(iid, status="failed", message=f"Loading stopped because of an error on our side (job {job['id']}). "
                                           "Nothing half-loaded is shown to students. Try again, or use admin.py.")
        raise
    store.s3().delete_object(Bucket=config.runbook_bucket(), Key=item["key"])
    out = [{k: (str(v) if isinstance(v, float) else v) for k, v in r.items()} for r in out]   # DynamoDB keeps no floats
    msg = "; ".join(f"{r['dataset']}: {r['rows']:,} rows (version {r['version']})" for r in out)
    _set(iid, status="done", message=f"Loaded. {msg}.", result={"datasets": out})
    audit.write(job["who"], "superadmin", "load Live Data Simulator data", target=item["filename"],
                detail=msg[:300])
    return {"message": f"Loaded {len(out)} dataset(s)."}



@jobs.register("live_delete")
def job_delete(job):
    """Delete a dataset or a whole feed (rows can take minutes, so it runs in the background)."""
    from ..core import audit
    from . import ops
    p = job["params"]
    say = lambda m: jobs.progress(job["id"], str(m))  # noqa: E731
    if p.get("feed"):
        names = ops.delete_feed(p["feed"], say=say)
        what = f"feed {p['feed']} ({', '.join(names) or 'no datasets'})"
    else:
        ops.delete_dataset(p["dataset"], say=say)
        what = f"dataset {p['dataset']}"
    audit.write(job["who"], "superadmin", "delete Live Data Simulator data", target=what)
    return {"message": f"Deleted {what}."}
