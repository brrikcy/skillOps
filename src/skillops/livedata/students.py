"""Join codes, student registration, API keys and the fetch log."""
import re
import secrets as pysecrets

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from . import config, metrics, ratelimit, secret, store, timeutil
from .http import ApiError

# No look-alike characters: no 0/O, no 1/I/L.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
BAD_CODE_MESSAGE = "This join code is not valid or has closed."
BATCH_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")


# ------------------------------------------------------------------ join codes

def new_code_text():
    pick = lambda n: "".join(pysecrets.choice(CODE_ALPHABET) for _ in range(n))  # noqa: E731
    return f"JOIN-{pick(4)}-{pick(2)}"


def normalise_code(text):
    """Accept 'join-7kq4-px', 'JOIN 7KQ4 PX', '7KQ4PX' ... -> 'JOIN-7KQ4-PX'."""
    s = re.sub(r"[^A-Za-z0-9]", "", str(text or "")).upper()
    if s.startswith("JOIN"):
        s = s[4:]
    if len(s) != 6:
        return None
    return f"JOIN-{s[:4]}-{s[4:]}"


def create_code(batch, max_uses, open_minutes, approval, created_by, now_us):
    for _ in range(10):
        code = new_code_text()
        item = {
            "pk": f"CODE#{code}", "sk": "META", "type": "code", "code": code, "batch": batch,
            "max_uses": int(max_uses), "uses": 0, "approval": bool(approval), "closed": False,
            "created_by": created_by, "created_at": timeutil.us_to_iso(now_us),
            "expires_at": int(now_us + int(open_minutes) * 60 * 1_000_000),
        }
        try:
            store.t("CONFIG").put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
            return code_view(store.plain(item), now_us)
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
    raise RuntimeError("Could not make a unique join code. Try again.")


def get_code(code):
    item = store.t("CONFIG").get_item(Key={"pk": f"CODE#{code}", "sk": "META"}, ConsistentRead=True).get("Item")
    return store.plain(item) if item else None


def code_view(item, now_us):
    open_now = (not item["closed"]) and now_us < item["expires_at"] and item["uses"] < item["max_uses"]
    return {
        "code": item["code"], "batch": item["batch"], "max_uses": item["max_uses"], "uses": item["uses"],
        "approval": item["approval"], "closed": item["closed"], "open": open_now,
        "expires_at": timeutil.us_to_iso(item["expires_at"]),
        "seconds_left": max(0, (item["expires_at"] - now_us) // 1_000_000) if open_now else 0,
        "created_by": item.get("created_by"), "created_at": item.get("created_at"),
    }


def list_codes():
    from boto3.dynamodb.conditions import Attr
    return [store.plain(i) for i in store.scan_all("CONFIG", FilterExpression=Attr("type").eq("code"))]


def close_code(code):
    """A closed code is gone: nobody can register with it, and it no longer clutters the list.
    (Students who registered with it keep their accounts; their record keeps the code they used.)"""
    store.t("CONFIG").delete_item(Key={"pk": f"CODE#{code}", "sk": "META"})


def _use_code(code, now_us):
    """Take one use of a join code, atomically. False if it is not open."""
    try:
        store.t("CONFIG").update_item(
            Key={"pk": f"CODE#{code}", "sk": "META"},
            UpdateExpression="ADD uses :one",
            ConditionExpression="attribute_exists(pk) AND closed = :f AND expires_at > :now AND uses < max_uses",
            ExpressionAttributeValues={":one": 1, ":f": False, ":now": now_us},
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


# ------------------------------------------------------------------ students and keys

def norm_roll(roll):
    return re.sub(r"\s+", " ", str(roll)).strip().upper()


def norm_name(name):
    return re.sub(r"\s+", " ", str(name)).strip().casefold()


def student_id(batch, roll):
    return f"{batch}#{norm_roll(roll)}"


def get_student(sid):
    item = store.t("STUDENTS").get_item(Key={"pk": f"STU#{sid}"}, ConsistentRead=True).get("Item")
    return store.plain(item) if item else None


def list_students(batch):
    items = store.query_all("STUDENTS", IndexName="by_batch", KeyConditionExpression=Key("batch").eq(batch))
    return [store.plain(i) for i in items]


def new_api_key():
    return "rbk_" + pysecrets.token_hex(20)


def replace_key(student, now_us):
    """Give a student a new API key. The old key stops working at once. History is kept."""
    key = new_api_key()
    h = secret.hash_key(key)
    table = store.t("STUDENTS")
    # Order matters: 1) the new key item, 2) point the student at the new hash,
    # 3) remove the old key item. Even if step 3 failed, the old key would still be
    # refused, because authenticate() also checks the student's current key_hash.
    table.put_item(Item={"pk": f"KEY#{h}", "student_id": student["student_id"],
                         "created_at": timeutil.us_to_iso(now_us)})
    table.update_item(Key={"pk": f"STU#{student['student_id']}"},
                      UpdateExpression="SET key_hash = :h, key_issued_at = :t ADD key_count :one",
                      ExpressionAttributeValues={":h": h, ":t": timeutil.us_to_iso(now_us), ":one": 1})
    if student.get("key_hash"):
        table.delete_item(Key={"pk": f"KEY#{student['key_hash']}"})
    return key


def register(body, source_ip, api_url, now_us):
    ok, retry = ratelimit.hit("regip", source_ip, config.register_limit_per_ip(), now_us)
    if not ok:
        raise _rate_limited(retry)
    code = normalise_code(body.get("join_code"))
    name = re.sub(r"\s+", " ", str(body.get("full_name") or body.get("name") or "")).strip()
    roll = re.sub(r"\s+", " ", str(body.get("roll_number") or "")).strip()
    if not name or len(name) > 80:
        raise ApiError(400, "missing_field", "Please give your full name (up to 80 letters).",
                       "Send full_name in the JSON body.")
    if not roll or len(roll) > 40:
        raise ApiError(400, "missing_field", "Please give your roll number (up to 40 characters).",
                       "Send roll_number in the JSON body.")
    if not code:
        raise ApiError(400, "join_code_invalid", BAD_CODE_MESSAGE, "Check the join code with your trainer.")
    ok, retry = ratelimit.hit("regcode", code, config.register_limit_per_code(), now_us)
    if not ok:
        raise _rate_limited(retry)
    item = get_code(code)
    if not item or item["closed"] or now_us >= item["expires_at"] or item["uses"] >= item["max_uses"]:
        raise ApiError(400, "join_code_invalid", BAD_CODE_MESSAGE, "Check the join code with your trainer.")
    batch = item["batch"]
    sid = student_id(batch, roll)
    existing = get_student(sid)
    if existing and norm_name(existing["name"]) != norm_name(name):
        raise ApiError(409, "roll_number_taken",
                       "This roll number is already registered in your batch under a different name.",
                       "Check your roll number. If it is right, ask your trainer for help.")
    if not _use_code(code, now_us):
        raise ApiError(400, "join_code_invalid", BAD_CODE_MESSAGE, "Check the join code with your trainer.")

    if existing:
        key = replace_key(existing, now_us)
        student = get_student(sid)
        again = True
    else:
        status = "pending" if item["approval"] else "active"
        key = new_api_key()
        h = secret.hash_key(key)
        stu = {"pk": f"STU#{sid}", "student_id": sid, "batch": batch, "roll": norm_roll(roll),
               "roll_display": roll, "name": name, "status": status, "key_hash": h,
               "registered_at": timeutil.us_to_iso(now_us), "key_issued_at": timeutil.us_to_iso(now_us),
               "key_count": 1, "join_code": code}
        table = store.t("STUDENTS")
        try:
            table.put_item(Item=stu, ConditionExpression="attribute_not_exists(pk)")
            table.put_item(Item={"pk": f"KEY#{h}", "student_id": sid, "created_at": timeutil.us_to_iso(now_us)})
        except ClientError as e:
            if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
                raise
            # Two registrations with the same roll number raced; treat this one as a re-registration.
            key = replace_key(get_student(sid), now_us)
        student = get_student(sid)
        again = False
    metrics.emit("Registrations", 1, Batch=batch)
    return {
        "message": ("Welcome back. Your old API key has stopped working. Use this new one."
                    if again else "You are registered."),
        "student": public_student(student),
        "api_key": key,
        "api_url": api_url,
        "env_lines": f"SIMLAB_API_KEY={key}\nSIMLAB_API_URL={api_url}",
        "warning": "This API key is shown only once. Copy it into your .env file now.",
    }


def _rate_limited(retry):
    return ApiError(429, "rate_limited", "Too many requests. Please slow down.",
                    f"Wait {retry} seconds and try again.", extra={"retry_after_seconds": retry},
                    headers={"retry-after": str(retry)})


def public_student(s):
    status_message = {
        "active": "You are approved. You can fetch data.",
        "pending": "Waiting for your trainer to approve you.",
        "disabled": "Your access is turned off. Speak to your trainer.",
        "rejected": "Your registration was not approved. Speak to your trainer.",
    }[s["status"]]
    return {"name": s["name"], "roll_number": s.get("roll_display", s["roll"]), "batch": _batch_name(s["batch"]),
            "status": s["status"], "message": status_message, "registered_at": s["registered_at"]}


_batch_names = {}


def _batch_name(batch_id):
    """Students see their batch's name ("Batch A"), not SkillOps' internal id ("b-k7q4px")."""
    if batch_id in _batch_names:
        return _batch_names[batch_id]
    name = batch_id
    try:
        from ..core import org
        b = org.get_batch(batch_id, required=False)
        if b:
            name = b["name"]
    except Exception:  # noqa: BLE001  (the student API must answer even if this lookup fails)
        pass
    _batch_names[batch_id] = name
    return name


def authenticate(req, now_us):
    """Find the student who owns the x-api-key header, or raise a 401/429 error."""
    key = (req.headers.get("x-api-key") or "").strip()
    if not key:
        hint = "Send your key in a header named x-api-key."
        if req.query.get("api_key") or req.query.get("x-api-key"):
            hint = "Do not put the key in the URL. Send it in a header named x-api-key."
        elif req.headers.get("authorization"):
            hint = "Use the header x-api-key, not Authorization."
        raise ApiError(401, "missing_key", "No API key was sent.", hint)
    h = secret.hash_key(key)
    item = store.t("STUDENTS").get_item(Key={"pk": f"KEY#{h}"}).get("Item")
    student = get_student(item["student_id"]) if item else None
    if not student or student.get("key_hash") != h:
        raise ApiError(401, "invalid_key", "This API key is not valid.",
                       "Check SIMLAB_API_KEY in your .env file. If you registered again, use the newest key.")
    ok, retry = ratelimit.hit("key", h[:24], config.rate_limit_per_minute(), now_us)
    if not ok:
        log_request(student, "", None, None, 0, 0, 429, now_us, error="rate_limited")
        raise _rate_limited(retry)
    return student


def require_active(student):
    if student["status"] == "pending":
        raise ApiError(403, "pending_approval", "Waiting for your trainer to approve you.",
                       "You can call /v1/whoami to check. Ask your trainer to approve you.")
    if student["status"] in ("disabled", "rejected"):
        raise ApiError(403, "disabled", public_student(student)["message"],
                       "Your trainer can turn your access back on.")


# ------------------------------------------------------------------ fetch log

def get_summary(sid, dataset):
    item = store.t("FETCHLOG").get_item(Key={"pk": sid, "sk": f"SUM#{dataset}"}).get("Item")
    return store.plain(item) if item else None


def log_request(student, dataset, since, until, rows, page, status, now_us, version=None, error=None):
    """Write one fetch-log line, and keep running totals per student and dataset."""
    at = timeutil.us_to_iso(now_us)
    sid = student["student_id"]
    expires = now_us // 1_000_000 + config.fetchlog_ttl_days() * 86400
    store.t("FETCHLOG").put_item(Item={
        "pk": sid, "sk": f"LOG#{at}#{pysecrets.token_hex(3)}", "batch": student["batch"],
        "dataset": dataset or "", "since": since or "", "until": until or "", "rows": rows,
        "page": page, "status": status, "at": at, "error": error or "", "expires": expires,
    })
    if not dataset:
        return
    if status == 200:
        store.t("FETCHLOG").update_item(
            Key={"pk": sid, "sk": f"SUM#{dataset}"},
            UpdateExpression="ADD fetches :f, pages :one, rows_total :r "
                             "SET last_at = :at, last_until = :u, version = :v, sum_batch = :b, "
                             "dataset = :d, student_id = :sid",
            ExpressionAttributeValues={":f": 1 if page == 1 else 0, ":one": 1, ":r": rows, ":at": at,
                                       ":u": until, ":v": version, ":b": student["batch"], ":d": dataset,
                                       ":sid": sid})
        if page == 1:
            metrics.emit("Fetches", 1, Batch=student["batch"])
    else:
        store.t("FETCHLOG").update_item(
            Key={"pk": sid, "sk": f"SUM#{dataset}"},
            UpdateExpression="ADD errors :one SET last_error = :e, last_error_at = :at, sum_batch = :b, "
                             "dataset = :d, student_id = :sid",
            ExpressionAttributeValues={":one": 1, ":e": f"{status} {error or ''}".strip(), ":at": at,
                                       ":b": student["batch"], ":d": dataset, ":sid": sid})


def batch_activity(batch):
    """All per-dataset totals for one batch (one query on the by_batch index)."""
    return [store.plain(i) for i in store.query_all(
        "FETCHLOG", IndexName="by_batch", KeyConditionExpression=Key("sum_batch").eq(batch))]


def student_log(sid, limit=100):
    resp = store.t("FETCHLOG").query(KeyConditionExpression=Key("pk").eq(sid) & Key("sk").begins_with("LOG#"),
                                     ScanIndexForward=False, Limit=limit)
    return [store.plain(i) for i in resp.get("Items", [])]
