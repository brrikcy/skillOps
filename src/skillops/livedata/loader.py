"""Reading .db / .csv files and loading their rows into DynamoDB (used by admin.py).

Rows are stored exactly as given:
- SQL NULL stays null, '' stays '', numbers stay numbers, text stays text.
- The key column is only a label. Rows that share a key are all kept.
- Each row gets an internal row id (its position in the source table, from 1).
"""
import csv
import json
import os
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Key

from . import catalog, clock as clk, config, store, timeutil

RELEASE_RULES = ("at_time", "end_of_hour", "start_of_day", "end_of_day", "static", "assign")

# ------------------------------------------------------------------ sources


class SqliteSource:
    def __init__(self, path):
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        self.path = path
        self.conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        # Keep text exactly; replace only bytes that are not valid UTF-8.
        self.conn.text_factory = lambda b: b.decode("utf-8", "replace")

    def close(self):
        self.conn.close()

    def tables(self):
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")
        return [r[0] for r in cur]

    def columns(self, table):
        cur = self.conn.execute(f'PRAGMA table_info("{_q(table)}")')
        cols = [(r[1], r[2] or "") for r in cur]
        if not cols:
            raise ValueError(f"Table '{table}' was not found in {self.path}.")
        return cols

    def count(self, table):
        return self.conn.execute(f'SELECT COUNT(*) FROM "{_q(table)}"').fetchone()[0]

    def rows(self, table):
        try:
            cur = self.conn.execute(f'SELECT * FROM "{_q(table)}" ORDER BY rowid')
        except sqlite3.OperationalError:  # WITHOUT ROWID tables
            cur = self.conn.execute(f'SELECT * FROM "{_q(table)}"')
        while True:
            chunk = cur.fetchmany(5000)
            if not chunk:
                break
            for row in chunk:
                yield list(row)

    def min_max(self, table, column):
        lo, hi, nulls, empties = self.conn.execute(
            f'SELECT MIN(c), MAX(c), (SELECT COUNT(*) FROM "{_q(table)}" WHERE "{_q(column)}" IS NULL), '
            f'(SELECT COUNT(*) FROM "{_q(table)}" WHERE "{_q(column)}" = \'\') '
            f'FROM (SELECT "{_q(column)}" AS c FROM "{_q(table)}" WHERE "{_q(column)}" IS NOT NULL AND "{_q(column)}" <> \'\')'
        ).fetchone()
        return lo, hi, nulls or 0, empties or 0


class CsvSource:
    """A CSV file is one table. CSV has no NULL and no types: every value is text, kept as text."""

    def __init__(self, path, table=None):
        if not os.path.exists(path):
            raise FileNotFoundError(f"File not found: {path}")
        self.path = path
        self.table = table or os.path.splitext(os.path.basename(path))[0]

    def close(self):
        pass

    def tables(self):
        return [self.table]

    def _reader(self):
        f = open(self.path, newline="", encoding="utf-8-sig")
        return f, csv.reader(f)

    def columns(self, table):
        f, r = self._reader()
        with f:
            header = next(r)
        return [(h, "TEXT") for h in header]

    def count(self, table):
        f, r = self._reader()
        with f:
            next(r)
            return sum(1 for _ in r)

    def rows(self, table):
        f, r = self._reader()
        with f:
            next(r)
            for row in r:
                yield list(row)

    def min_max(self, table, column):
        names = [c for c, _ in self.columns(table)]
        i = names.index(column)
        vals = [row[i] for row in self.rows(table) if i < len(row)]
        filled = [v for v in vals if v != ""]
        return (min(filled) if filled else None, max(filled) if filled else None, 0, len(vals) - len(filled))


def open_source(path, table=None):
    if path.lower().endswith(".csv"):
        return CsvSource(path, table)
    return SqliteSource(path)


def _q(name):
    return name.replace('"', '""')


# ------------------------------------------------------------------ inspect

_DATE_NAME = re.compile(r"(date|time|_at$|_on$|timestamp|day$)", re.I)
_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def inspect(source):
    """Every table: row count, columns, declared types, and the range of date-like columns."""
    report = []
    for table in source.tables():
        cols = source.columns(table)
        info = {"table": table, "rows": source.count(table), "columns": cols, "dates": []}
        sample = []
        for i, row in enumerate(source.rows(table)):
            sample.append(row)
            if i >= 50:
                break
        for idx, (name, typ) in enumerate(cols):
            values = [r[idx] for r in sample if idx < len(r) and isinstance(r[idx], str)]
            looks = sum(1 for v in values if _DATE_VALUE.match(v))
            if _DATE_NAME.search(name) or "DATE" in typ.upper() or "TIME" in typ.upper() or \
                    (values and looks >= 0.8 * len(values)):
                lo, hi, nulls, empties = source.min_max(table, name)
                info["dates"].append({"column": name, "min": lo, "max": hi, "nulls": nulls, "empty": empties})
        report.append(info)
    return report


# ------------------------------------------------------------------ mapping


def read_mapping(path):
    with open(path, encoding="utf-8") as f:
        m = json.load(f)
    if "datasets" not in m or not isinstance(m["datasets"], list):
        raise ValueError("The mapping file needs a 'datasets' list.")
    if "data_start" not in m:
        raise ValueError("The mapping file needs 'data_start', for example \"2025-07-01\".")
    for d in m["datasets"]:
        for need in ("table", "dataset"):
            if need not in d:
                raise ValueError(f"Each dataset in the mapping needs '{need}'. Problem in: {d}")
        if not re.match(r"^[A-Za-z0-9_\-]{1,64}$", d["dataset"]):
            raise ValueError(f"Dataset name '{d['dataset']}' may only use letters, digits, _ and -.")
        rule_of(d)  # validates
    return m


def rule_of(entry):
    """Work out the release rule of one mapping entry."""
    release = entry.get("release")
    if release == "static":
        return "static"
    if entry.get("time_column"):
        return "at_time"
    if entry.get("date_column") and entry.get("hour_column"):
        if release not in (None, "end_of_hour"):
            raise ValueError(f"{entry['dataset']}: with an hour_column, release must be 'end_of_hour'.")
        return "end_of_hour"
    if entry.get("date_column"):
        if release in (None, "start_of_day"):
            return "start_of_day"
        if release == "end_of_day":
            return "end_of_day"
        raise ValueError(f"{entry['dataset']}: with only a date_column, release must be "
                         "'start_of_day' or 'end_of_day'.")
    if release == "assign":
        return "assign"
    raise ValueError(f"{entry['dataset']}: no time column. Use \"release\": \"static\" "
                     "or give --assign-release.")


# ------------------------------------------------------------------ data times

_DT_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
               "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d %H:%M", "%Y-%m-%d")
_D_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d")


def _parse_date(value):
    if not isinstance(value, str):
        return None
    v = value.strip()
    if _DATE_VALUE.match(v):
        try:
            return datetime.strptime(v[:10], "%Y-%m-%d")
        except ValueError:
            return None
    for fmt in _D_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return None


def _parse_datetime(value):
    if not isinstance(value, str):
        return None
    v = value.strip()
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(v, fmt)
        except ValueError:
            continue
    return _parse_date(v)


def _parse_hour(value):
    try:
        h = int(str(value).strip())
    except (TypeError, ValueError):
        try:
            f = float(str(value).strip())
            h = int(f) if f == int(f) else -1
        except (TypeError, ValueError):
            return None
    return h if 0 <= h <= 23 else None


class OffsetMaker:
    """Turns one row into its data offset: whole seconds after data_start.

    Dirty values are expected. A row whose date cannot be read takes the time of
    the row before it (or data_start for the first row). A good date with a bad
    hour is released at the end of that day. Both cases are counted and reported.
    """

    def __init__(self, columns, entry, rule, data_start_us, tz, assign=None, feed_clock=None, total_rows=0):
        names = [c for c, _ in columns]
        self.rule = rule
        self.tz = ZoneInfo(tz)
        self.data_start_us = data_start_us
        self.cache = {}
        self.last = 0
        self.bad_date = 0
        self.bad_hour = 0

        def idx(key):
            col = entry.get(key)
            if col is None:
                return None
            if col not in names:
                raise ValueError(f"{entry['dataset']}: column '{col}' ({key}) is not in table '{entry['table']}'. "
                                 f"Columns are: {', '.join(names)}")
            return names.index(col)

        self.i_time = idx("time_column")
        self.i_date = idx("date_column")
        self.i_hour = idx("hour_column")
        if rule == "assign":
            self.assign = assign
            self.feed_clock = feed_clock
            self.total = total_rows
            self.n = 0

    def _offset(self, naive):
        us = timeutil.dt_to_us(naive.replace(tzinfo=self.tz))
        return (us - self.data_start_us) // 1_000_000

    def __call__(self, row):
        r = self.rule
        if r == "static":
            return catalog.STATIC_OFFSET
        if r == "assign":
            return self._assign()
        if r == "at_time":
            raw = row[self.i_time]
            key = ("t", raw)
            if key not in self.cache:
                dt = _parse_datetime(raw)
                self.cache[key] = None if dt is None else self._offset(dt)
        elif r == "end_of_hour":
            raw_d, raw_h = row[self.i_date], row[self.i_hour]
            key = ("h", raw_d, raw_h)
            if key not in self.cache:
                d = _parse_date(raw_d)
                if d is None:
                    self.cache[key] = None
                else:
                    h = _parse_hour(raw_h)
                    if h is None:
                        self.cache[key] = ("badhour", self._offset(d + timedelta(days=1)))
                    else:
                        self.cache[key] = self._offset(d + timedelta(hours=h + 1))
        else:  # start_of_day / end_of_day
            raw = row[self.i_date]
            key = ("d", raw)
            if key not in self.cache:
                d = _parse_date(raw)
                if d is None:
                    self.cache[key] = None
                else:
                    self.cache[key] = self._offset(d + timedelta(days=1 if r == "end_of_day" else 0))
        val = self.cache[key]
        if isinstance(val, tuple):
            self.bad_hour += 1
            val = val[1]
        if val is None:
            self.bad_date += 1
            return self.last
        self.last = val
        return val

    def _assign(self):
        start_us, end_us, every_s, per_step = self.assign
        i = self.n
        self.n += 1
        if every_s:
            real = start_us + (i // per_step) * every_s * 1_000_000
        else:
            steps = max(self.total - 1, 1)
            real = start_us + (end_us - start_us) * i // steps
        return clk.floor_offset(self.feed_clock, real)


# ------------------------------------------------------------------ writing rows


def _thread_table(name):
    local = _thread_table.local
    if not hasattr(local, "tables"):
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
        local.res = boto3.session.Session().resource("dynamodb", region_name=region, config=store.BOTO_CONFIG)
        local.tables = {}
    if name not in local.tables:
        local.tables[name] = local.res.Table(config.table(name))
    return local.tables[name]


_thread_table.local = threading.local()


def _write_chunk(items):
    with _thread_table("ROWS").batch_writer() as bw:  # retries unprocessed items for us
        for it in items:
            bw.put_item(Item=it)
    return len(items)


def _delete_chunk(keys):
    with _thread_table("ROWS").batch_writer() as bw:
        for k in keys:
            bw.delete_item(Key=k)
    return len(keys)


def delete_partition(pk, workers=8, say=print):
    """Delete every row in one rows partition (an old upload)."""
    table = store.t("ROWS")
    deleted = 0
    kw = dict(KeyConditionExpression=Key("pk").eq(pk), ProjectionExpression="pk, sk")
    with ThreadPoolExecutor(workers) as pool:
        resp = table.query(**kw)
        while True:
            keys = resp.get("Items", [])
            futures = [pool.submit(_delete_chunk, keys[i:i + 500]) for i in range(0, len(keys), 500)]
            deleted += sum(f.result() for f in futures)
            if deleted and deleted % 50000 < len(keys):
                say(f"  deleted {deleted:,} old rows...")
            if "LastEvaluatedKey" not in resp:
                break
            resp = table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kw)
    return deleted


def _value(v, stats):
    if isinstance(v, (bytes, bytearray, memoryview)):
        stats["blobs"] += 1
        return bytes(v).hex()
    return v


def upload_dataset(source, entry, feed_item, *, assign=None, activate=None, keep_old=False,
                   workers=8, say=print, data_start_us=None):
    """Load one table into one dataset. Returns a summary dict.

    The new rows go into a fresh partition (a new 'generation'). Only when every
    row is written do we switch the dataset to it, so students never see half an upload.
    """
    table_name = entry["table"]
    dataset = entry["dataset"]
    rule = "assign" if assign else rule_of(entry)
    columns = source.columns(table_name)
    total = source.count(table_name)
    old = catalog.get_dataset(dataset)
    if old and old.get("feed") != feed_item["feed"]:
        raise ValueError(f"Dataset {dataset} belongs to feed '{old['feed']}', not '{feed_item['feed']}'.")
    gen = max(int(old.get("gen", 0)), int(old.get("pending_gen", 0))) + 1 if old else 1
    pk = catalog.rows_pk(dataset, gen)

    if old:
        store.t("CONFIG").update_item(Key={"pk": f"DATASET#{dataset}", "sk": "META"},
                                      UpdateExpression="SET pending_gen = :g", ExpressionAttributeValues={":g": gen})
    # Clean up leftovers of an earlier upload that stopped half-way.
    leftovers = store.t("ROWS").query(KeyConditionExpression=Key("pk").eq(pk), Limit=1).get("Items")
    if leftovers:
        say(f"  removing rows left by an earlier unfinished upload of {dataset}...")
        delete_partition(pk, workers, say)

    make = OffsetMaker(columns, entry, rule, data_start_us or feed_item["data_start_us"], entry.get("timezone") or feed_item["tz"],
                       assign=assign, feed_clock=feed_item["clock"], total_rows=total)
    stats = {"blobs": 0}
    hist_raw = {}
    min_off = None
    written = 0
    started = time.time()
    say(f"Uploading {table_name} -> {dataset} ({total:,} rows, rule: {rule})")
    with ThreadPoolExecutor(workers) as pool:
        pending, chunk = [], []
        for rid, row in enumerate(source.rows(table_name), start=1):
            if rid >= catalog.ROW_ID_SPACE:
                raise ValueError("Too many rows: a dataset can hold up to 999,999,999 rows.")
            off = make(row)
            if rule != "static" and (min_off is None or off < min_off):
                min_off = off
            hist_raw[off // 60] = hist_raw.get(off // 60, 0) + 1  # per data minute; regrouped below
            chunk.append({"pk": pk, "sk": catalog.sk_for(off, rid),
                          "r": json.dumps([_value(v, stats) for v in row], ensure_ascii=False, separators=(",", ":"))})
            if len(chunk) == 500:
                pending.append(pool.submit(_write_chunk, chunk))
                chunk = []
            if len(pending) >= workers * 4:
                written += sum(f.result() for f in pending)
                pending = []
                rate = written / max(time.time() - started, 0.001)
                say(f"  {written:,} / {total:,} rows ({100 * written / max(total, 1):.0f}%), {rate:,.0f} rows/s")
        if chunk:
            pending.append(pool.submit(_write_chunk, chunk))
        written += sum(f.result() for f in pending)

    bucket, hist = _regroup(hist_raw)
    catalog.put_histogram(dataset, gen, bucket, hist)
    now = timeutil.now_us()
    meta = {
        "dataset": dataset, "feed": feed_item["feed"], "table": table_name,
        "columns": [c for c, _ in columns], "types": [t for _, t in columns],
        "key_column": entry.get("key_column") or "", "rule": rule,
        "release_columns": {k: entry[k] for k in ("time_column", "date_column", "hour_column") if entry.get(k)},
        "gen": gen, "version": (int(old["version"]) + 1) if old else 1,
        "active": bool(old["active"]) if old and activate is None else bool(activate),
        "row_count": written, "min_offset": min_off, "uploaded_at": timeutil.us_to_iso(now),
        "activated_at": old.get("activated_at") if old else None,
    }
    if meta["active"] and not meta["activated_at"]:
        meta["activated_at"] = timeutil.us_to_iso(now)
    catalog.put_dataset(meta)
    took = time.time() - started
    say(f"  done: {written:,} rows in {took:,.0f} s. dataset_version is now {meta['version']}.")
    if make.bad_date:
        say(f"  note: {make.bad_date:,} rows had a date that could not be read. "
            "They are released with the row before them.")
    if make.bad_hour:
        say(f"  note: {make.bad_hour:,} rows had an hour outside 0-23. They are released at the end of their day.")
    if stats["blobs"]:
        say(f"  note: {stats['blobs']:,} BLOB values were stored as hex text.")
    if old and not keep_old:
        say(f"  removing the previous upload (generation {old['gen']})...")
        delete_partition(catalog.rows_pk(dataset, old["gen"]), workers, say)
        catalog.delete_histogram(dataset, old["gen"])
    return {"dataset": dataset, "rows": written, "version": meta["version"], "gen": gen,
            "bad_date": make.bad_date, "bad_hour": make.bad_hour, "seconds": round(took, 1)}


def _regroup(per_minute):
    """Choose a bucket size (1 hour or 1 day) that keeps the histogram small."""
    for bucket in (3600, 86400, 86400 * 7):
        hist = {}
        for minute, c in per_minute.items():
            b = (minute * 60) // bucket
            hist[b] = hist.get(b, 0) + c
        if len(hist) <= 15000:
            return bucket, hist
    return bucket, hist
