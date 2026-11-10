"""Fixed-window rate limits stored in DynamoDB.

Each limit is a counter item for one minute, for example "key#ab12...#29711234".
DynamoDB's TTL feature deletes old counters automatically.
"""
from . import store


def hit(kind, ident, limit, now_us):
    """Count one request. Returns (allowed, retry_after_seconds)."""
    minute = now_us // 60_000_000
    resp = store.t("RATELIMIT").update_item(
        Key={"pk": f"{kind}#{ident}#{minute}"},
        UpdateExpression="ADD n :one SET expires = :exp",
        ExpressionAttributeValues={":one": 1, ":exp": int(now_us // 1_000_000) + 180},
        ReturnValues="UPDATED_NEW",
    )
    n = int(resp["Attributes"]["n"])
    if n > limit:
        seconds_into_minute = (now_us // 1_000_000) % 60
        return False, max(1, 60 - seconds_into_minute)
    return True, 0
