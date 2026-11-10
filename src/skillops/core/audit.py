"""Audit log: every write action (and every sign-in), who did it, and the result.

Items: pk = "AUDIT#<yyyy-mm>", sk = "<time>#<random>". One partition per month keeps reads fast.
"""
import secrets

from . import store, util


def write(who, role, action, batch="", target="", result="ok", detail="", request_id=""):
    at = util.now_iso()
    store.t("AUDIT").put_item(Item={
        "pk": f"AUDIT#{at[:7]}", "sk": f"{at}#{secrets.token_hex(3)}", "at": at,
        "who": (who or "")[:200], "role": role or "", "action": action, "batch": batch or "",
        "target": str(target or "")[:300], "result": result, "detail": str(detail or "")[:500],
        "request_id": request_id or "",
    })


def _months(start, end):
    """'2026-09', '2026-11' -> ['2026-11', '2026-10', '2026-09'] (newest first)."""
    y, m = int(end[:4]), int(end[5:7])
    out = []
    while True:
        cur = f"{y:04d}-{m:02d}"
        out.append(cur)
        if cur <= start or len(out) > 120:
            break
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def read(start_date="", end_date="", who="", action="", batch="", result="", limit=1000):
    """Newest first. Dates are 'YYYY-MM-DD' (UTC), both optional."""
    end = end_date or util.now_iso()[:10]
    start = start_date or "2026-01-01"
    if start > end:
        start, end = end, start
    items = []
    for month in _months(start[:7], end[:7]):
        for it in store.by_pk("AUDIT", f"AUDIT#{month}", newest_first=True):
            day = it["at"][:10]
            if day < start or day > end:
                continue
            if who and who.lower() not in it["who"].lower():
                continue
            if action and action.lower() not in it["action"].lower():
                continue
            if batch and it.get("batch") != batch:
                continue
            if result and it.get("result") != result:
                continue
            items.append(it)
            if len(items) >= limit:
                return items
    return items


CSV_HEADER = ["time (UTC)", "who", "role", "action", "batch", "target", "result", "detail", "request id"]


def csv_rows(items):
    return [[i["at"], i["who"], i["role"], i["action"], i.get("batch", ""), i.get("target", ""),
             i["result"], i.get("detail", ""), i.get("request_id", "")] for i in items]
