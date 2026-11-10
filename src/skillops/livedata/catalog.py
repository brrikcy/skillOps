"""Feeds and datasets: their settings, clocks, and how many rows are released."""
import json
import time

from boto3.dynamodb.conditions import Attr, Key

from . import clock as clk
from . import store, timeutil

ROW_ID_SPACE = 10 ** 9            # row ids must stay below one billion
STATIC_OFFSET = -(10 ** 9)        # 'static' rows: about 31 years before data_start, so always released


def sk_for(offset, row_id):
    """Sort key of a row: data offset first, then row id. One number keeps both in order."""
    return int(offset) * ROW_ID_SPACE + int(row_id)


def sk_range(lo_offset_exclusive, hi_offset_inclusive):
    """Sort-key bounds for rows with lo < offset <= hi (offsets are whole seconds)."""
    lo = (lo_offset_exclusive + 1) * ROW_ID_SPACE if lo_offset_exclusive is not None else None
    hi = hi_offset_inclusive * ROW_ID_SPACE + (ROW_ID_SPACE - 1)
    return lo, hi


def rows_pk(dataset, gen):
    return f"{dataset}#g{int(gen)}"


# ---------------------------------------------------------------- feeds

def get_feed(feed):
    item = store.t("CONFIG").get_item(Key={"pk": f"FEED#{feed}", "sk": "META"}, ConsistentRead=True).get("Item")
    if not item:
        return None
    item = store.plain(item)
    item["clock"] = json.loads(item["clock"])
    return item


def put_feed(feed_item):
    item = dict(feed_item)
    item["pk"] = f"FEED#{item['feed']}"
    item["sk"] = "META"
    item["clock"] = json.dumps(item["clock"])
    item["updated_at"] = timeutil.us_to_iso(timeutil.now_us())
    store.t("CONFIG").put_item(Item=item)


def list_feeds(max_age=0):
    if max_age:
        return cached("feeds", list_feeds, max_age)
    items = store.scan_all("CONFIG", FilterExpression=Attr("sk").eq("META") & Attr("pk").begins_with("FEED#"))
    out = []
    for it in items:
        it = store.plain(it)
        it["clock"] = json.loads(it["clock"])
        out.append(it)
    return sorted(out, key=lambda f: f["feed"])


# ---------------------------------------------------------------- datasets

def get_dataset(name):
    item = store.t("CONFIG").get_item(Key={"pk": f"DATASET#{name}", "sk": "META"}, ConsistentRead=True).get("Item")
    return store.plain(item) if item else None


def put_dataset(meta):
    item = dict(meta)
    item["pk"] = f"DATASET#{item['dataset']}"
    item["sk"] = "META"
    store.t("CONFIG").put_item(Item=item)


_scan_cache = {}


def cached(name, fn, seconds=10):
    """Remember a small scan for a few seconds inside one Lambda container (public endpoints only)."""
    hit = _scan_cache.get(name)
    if hit and time.time() - hit[0] < seconds:
        return hit[1]
    value = fn()
    _scan_cache[name] = (time.time(), value)
    return value


def list_datasets(feed=None, max_age=0):
    if max_age:
        out = cached("datasets", list_datasets, max_age)
        return [d for d in out if d.get("feed") == feed] if feed else out
    items = store.scan_all("CONFIG", FilterExpression=Attr("sk").eq("META") & Attr("pk").begins_with("DATASET#"))
    out = [store.plain(i) for i in items]
    if feed:
        out = [d for d in out if d.get("feed") == feed]
    return sorted(out, key=lambda d: d["dataset"])


# The histogram (rows per hour of data time) lives in the ROWS table under pk "HIST#<dataset>",
# so that scans of the small CONFIG table stay cheap.
def put_histogram(dataset, gen, bucket, hist):
    store.t("ROWS").put_item(Item={
        "pk": f"HIST#{dataset}", "sk": int(gen),
        "bucket": int(bucket), "r": json.dumps({str(k): v for k, v in hist.items()}),
    })


def delete_histogram(dataset, gen):
    store.t("ROWS").delete_item(Key={"pk": f"HIST#{dataset}", "sk": int(gen)})


_hist_cache = {}


def get_histogram(dataset, gen):
    key = (dataset, int(gen))
    if key not in _hist_cache:
        item = store.t("ROWS").get_item(Key={"pk": f"HIST#{dataset}", "sk": int(gen)}).get("Item")
        if not item:
            return None, {}
        _hist_cache[key] = (int(item["bucket"]), {int(k): int(v) for k, v in json.loads(item["r"]).items()})
    return _hist_cache[key]


def feed_min_offset(feed):
    """Smallest non-static data offset across the feed's datasets (None if there is none)."""
    vals = [d["min_offset"] for d in list_datasets(feed) if d.get("min_offset") is not None]
    return min(vals) if vals else None


def released_count(meta, feed_item, now_us):
    """How many rows of this dataset have been released by now (never counts future rows)."""
    upto = clk.floor_offset(feed_item["clock"], now_us)
    bucket, hist = get_histogram(meta["dataset"], meta["gen"])
    if not hist:
        return 0
    full_limit = (upto + 1) // bucket          # buckets below this index are fully released
    total = sum(c for b, c in hist.items() if b < full_limit)
    if full_limit in hist and full_limit * bucket <= upto:
        lo = full_limit * bucket * ROW_ID_SPACE
        _, hi = sk_range(None, upto)
        resp = store.t("ROWS").query(
            KeyConditionExpression=Key("pk").eq(rows_pk(meta["dataset"], meta["gen"])) & Key("sk").between(lo, hi),
            Select="COUNT")
        total += resp["Count"]
        while "LastEvaluatedKey" in resp:
            resp = store.t("ROWS").query(
                KeyConditionExpression=Key("pk").eq(rows_pk(meta["dataset"], meta["gen"])) & Key("sk").between(lo, hi),
                Select="COUNT", ExclusiveStartKey=resp["LastEvaluatedKey"])
            total += resp["Count"]
    return total


def feed_summary(feed_item, now_us):
    c = feed_item["clock"]
    info = clk.describe(c, now_us, feed_item["data_start_us"], feed_item["tz"])
    info.update({"feed": feed_item["feed"], "data_start": feed_item["data_start_local"],
                 "timezone": feed_item["tz"]})
    return info


_learner_cache = {"at": 0, "value": 0}


def active_learners(now_us, window_minutes=60):
    """Number of students who fetched in the last hour (a count only, never names)."""
    if time.time() - _learner_cache["at"] < 60:
        return _learner_cache["value"]
    cutoff = timeutil.us_to_iso(now_us - window_minutes * 60 * 1_000_000)
    items = store.scan_all("FETCHLOG", IndexName="by_batch")
    students = {i["pk"] for i in items if i.get("last_at", "") >= cutoff}
    _learner_cache.update(at=time.time(), value=len(students))
    return len(students)
