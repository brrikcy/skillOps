"""GET /v1/fetch: return rows with  since < _release_at <= until.

How it works:
1. `until` is fixed to the current time (whole seconds) on the first page.
2. The feed's clock turns since and until into data offsets: D(since) and D(until).
   A row is released in (since, until] exactly when D(since) < its offset <= D(until).
3. Rows are stored sorted by (offset, row id), so this is one DynamoDB range query.
4. If there are more than page_size rows, we return a signed page token that carries
   the same `until`, the last row's sort key and the dataset_version.
"""
import json
import math

from boto3.dynamodb.conditions import Key

from . import catalog, clock as clk, config, secret, store, students, timeutil
from .http import ApiError

MAX_PAGE_BYTES = 4_500_000


def available_datasets():
    return [d["dataset"] for d in catalog.list_datasets(max_age=10) if d.get("active") and d.get("gen")]


def load_dataset(name):
    if not name:
        raise ApiError(400, "missing_dataset", "Please say which dataset you want.",
                       "Add ?dataset=<name> to the URL. Call /v1/datasets to see the names.",
                       extra={"available": available_datasets()})
    meta = catalog.get_dataset(name)
    if not meta or not meta.get("gen"):
        raise ApiError(400, "unknown_dataset", f"There is no dataset called '{name}'.",
                       "Check the spelling. These datasets are available now.",
                       extra={"available": available_datasets()})
    if not meta.get("active"):
        raise ApiError(403, "dataset_inactive", f"The dataset '{name}' is not open yet.",
                       "Wait for your trainer to open it, or use another dataset.",
                       extra={"available": available_datasets()})
    return meta


def _replaced(name):
    return ApiError(409, "dataset_replaced",
                    f"The dataset '{name}' was replaced or its clock was reset since your last fetch.",
                    "Delete the rows you loaded from it, then fetch again with an empty since "
                    "(since=) to start from the beginning.")


def _page_size(query):
    raw = query.get("page_size")
    if raw in (None, ""):
        return config.default_page_size()
    try:
        n = int(raw)
    except ValueError:
        n = 0
    if not 1 <= n <= config.default_page_size():
        raise ApiError(400, "bad_page_size", f"page_size must be a whole number from 1 to {config.default_page_size()}.",
                       "Leave page_size out to use the default.")
    return n


def fetch(req, student, now_us):
    """Returns (raw JSON body text, rows returned, details for the fetch log)."""
    students.require_active(student)
    q = req.query
    name = (q.get("dataset") or "").strip()
    meta = load_dataset(name)
    feed = catalog.get_feed(meta["feed"])
    page_size = _page_size(q)
    token = (q.get("page_token") or "").strip()
    ctx = {"dataset": name, "since": None, "until": None, "page": 1, "version": meta["version"]}

    if token:
        p = secret.verify_token(token)
        if not p or p.get("ds") != name or p.get("until", 0) > now_us:
            raise ApiError(400, "bad_page_token", "This page_token is not valid.",
                           "Copy next_page_token exactly from the last response, and keep the same dataset.")
        if p["v"] != meta["version"]:
            raise _replaced(name)
        since_us, until_us, after, page = p["since"], p["until"], p["after"], p["page"] + 1
    else:
        raw = q.get("since")
        since_us = None
        if raw not in (None, ""):
            try:
                since_us = timeutil.parse_utc_iso(raw)
            except ValueError:
                raise ApiError(400, "bad_since", "since must be a UTC time like 2026-11-16T03:30:00Z",
                               "Use the 'until' value from your last successful fetch, or leave since empty "
                               "to start from the beginning. If you used +00:00, the + may have become a space; use Z.")
            if since_us > now_us:
                raise ApiError(400, "bad_since", "since is in the future.",
                               "Use the 'until' value from your last successful fetch.")
            summary = students.get_summary(student["student_id"], name)
            if summary and summary.get("version") is not None and summary["version"] != meta["version"]:
                raise _replaced(name)
        until_us = max(timeutil.floor_seconds(now_us), since_us or 0)
        after, page = None, 1

    ctx.update(since=timeutil.us_to_iso(since_us) if since_us is not None else None,
               until=timeutil.us_to_iso(until_us), page=page)

    c = feed["clock"]
    lo = None if since_us is None else clk.floor_offset(c, since_us)
    hi = clk.floor_offset(c, until_us)
    items = _query(catalog.rows_pk(name, meta["gen"]), lo, hi, after, page_size + 1)
    more = len(items) > page_size
    items = items[:page_size]
    # A Lambda response must stay under 6 MB. Very wide rows end the page early (more pages, same result).
    size = 0
    for i, it in enumerate(items):
        size += len(it["r"]) + 1
        if size > MAX_PAGE_BYTES and i > 0:
            items, more = items[:i], True
            break

    next_token = None
    if more:
        next_token = secret.sign_token({"ds": name, "v": meta["version"], "since": since_us,
                                        "until": until_us, "after": int(items[-1]["sk"]), "page": page})
    data_until_us = feed["data_start_us"] + math.floor(clk.data_offset(c, until_us) * 1_000_000)
    head = {
        "platform": config.platform_name(),
        "dataset": name,
        "dataset_version": meta["version"],
        "since": ctx["since"],
        "until": ctx["until"],
        "data_time_until": timeutil.format_local(data_until_us, feed["tz"]),
        "columns": meta["columns"],
        "rows": "__ROWS__",
        "row_count": len(items),
        "next_page_token": next_token,
    }
    # The rows are already stored as JSON text, so we join them without parsing.
    body = json.dumps(head, ensure_ascii=False).replace('"__ROWS__"', "[" + ",".join(i["r"] for i in items) + "]", 1)
    return body, len(items), ctx


def _query(pk, lo_offset, hi_offset, after_sk, limit):
    lo, hi = catalog.sk_range(lo_offset, hi_offset)
    if lo is not None and lo > hi:
        return []
    if lo is None:
        cond = Key("pk").eq(pk) & Key("sk").lte(hi)
    else:
        cond = Key("pk").eq(pk) & Key("sk").between(lo, hi)
    items = []
    start = after_sk
    while len(items) < limit:
        kw = {"KeyConditionExpression": cond, "Limit": limit - len(items),
              "ProjectionExpression": "#sk, #r", "ExpressionAttributeNames": {"#sk": "sk", "#r": "r"}}
        if start is not None:
            kw["ExclusiveStartKey"] = {"pk": pk, "sk": start}
        resp = store.t("ROWS").query(**kw)
        items.extend(resp.get("Items", []))
        if "LastEvaluatedKey" not in resp:
            break
        start = resp["LastEvaluatedKey"]["sk"]
    return items
