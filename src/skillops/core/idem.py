"""Idempotency: a form sent twice (double click, retry after a timeout) is done once.

Every form carries a random key made when the page was shown. The first request with the
key stores IDEM#<key> (conditional write, so only one request can win), does the work and
saves the result. A repeat gets the saved result back instead of doing the work again.
"""
from botocore.exceptions import ClientError

from . import store, util

KEEP_SECONDS = 24 * 3600


def run_once(key, who, fn):
    """fn() must return a small JSON-able dict (for example {"redirect": "/x", "flash": "Saved."})."""
    key = (key or "").strip()
    if not key or len(key) > 80:
        return fn()
    pk = f"IDEM#{key}"
    now = util.now_ts()
    try:
        store.t("CORE").put_item(
            Item={"pk": pk, "sk": "META", "status": "running", "who": who, "at": util.now_iso(),
                  "ttl": int(now + KEEP_SECONDS)},
            ConditionExpression="attribute_not_exists(pk)")
    except ClientError as e:
        if not store.is_conditional_fail(e):
            raise
        item = store.get("CORE", pk, consistent=True) or {}
        if item.get("status") == "done":
            return item.get("result") or {}
        raise util.Conflict("This is already being saved.", "Wait a few seconds and refresh the page.")
    try:
        result = fn()
    except Exception:
        # The work failed: forget the key so the person can simply try again.
        store.t("CORE").delete_item(Key={"pk": pk, "sk": "META"})
        raise
    store.t("CORE").update_item(
        Key={"pk": pk, "sk": "META"}, UpdateExpression="SET #s = :d, #r = :r",
        ExpressionAttributeNames={"#s": "status", "#r": "result"},
        ExpressionAttributeValues={":d": "done", ":r": result or {}})
    return result or {}
