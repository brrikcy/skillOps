"""Talk to an institution's MCQ Test System (Google Apps Script web app with Api.gs).

Each institution can have its own Apps Script system and Google Sheet, or none.
  CORE  MCQCFG#<inst>   the web-app address and when the connection was last tested
  The shared secret is kept in Secrets Manager (MCQ_SECRET_ARN, one JSON map: inst -> secret).
  On a laptop (local mode) the secret is kept in CORE instead (MCQSECRET#<inst>, never exported).

Every call: 10-second timeout, up to 2 retries with a growing wait for network trouble.
Writes carry an idempotency key, which Api.gs remembers for 10 minutes, so a retry is never done twice.
Apps Script answers a POST to /exec with a redirect (302) to script.googleusercontent.com; the
answer is fetched from there with GET (urllib does this by itself).
If Google answers with a web page instead of JSON (daily quota reached, script error, the
deployment was removed), we say so in plain words; the rest of SkillOps keeps working.
"""
import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.config import Config

from ..core import config, store, util

READ_TIMEOUT = 5          # a page waits at most this long for the test system
WRITE_TIMEOUT = 10
DOWN_SECONDS = 60         # after a failure, pages do not ask again for a minute
_secret_cache = {"at": 0, "map": None}


class McqUnavailable(Exception):
    """The test system did not answer properly. Pages show a calm message and carry on."""


class McqError(Exception):
    """The test system answered with an error meant for the person (for example "That test does not exist")."""


# ================================================================== settings

def get_settings(iid):
    return store.get("CORE", f"MCQCFG#{iid}") or {}


def _secrets_client():
    return boto3.client("secretsmanager", region_name=config.region(),
                        config=Config(retries={"max_attempts": 3, "mode": "standard"}, connect_timeout=5, read_timeout=10))


def _secret_map(fresh=False):
    if config.local_mode() or not config.mcq_secret_arn():
        return None
    if not fresh and _secret_cache["map"] is not None and time.time() - _secret_cache["at"] < 300:
        return _secret_cache["map"]
    raw = _secrets_client().get_secret_value(SecretId=config.mcq_secret_arn())["SecretString"]
    _secret_cache.update(at=time.time(), map=json.loads(raw or "{}"))
    return _secret_cache["map"]


def get_secret(iid):
    m = _secret_map()
    if m is None:
        item = store.get("CORE", f"MCQSECRET#{iid}")
        return (item or {}).get("secret", "")
    return m.get(iid, "")


def save_settings(iid, url, secret):
    url = (url or "").strip()
    secret = (secret or "").strip()
    if secret and (len(secret) < 16 or len(secret) > 200):
        raise util.UserError("That does not look like the secret from setupApi.", "Copy it again from the Apps Script log.")
    if url and not (url.startswith("https://script.google.com/") and url.rstrip("/").endswith("/exec")) \
            and not (config.local_mode() and url.startswith("fake://")):
        raise util.UserError("The address must be the Apps Script web-app link that ends in /exec.",
                             "In the Apps Script editor: Deploy > Manage deployments > copy the Web app URL.")
    store.t("CORE").put_item(Item={"pk": f"MCQCFG#{iid}", "sk": "META", "inst_id": iid, "url": url,
                                   "updated_at": util.now_iso(), "last_ok": get_settings(iid).get("last_ok", ""),
                                   "has_secret": bool(secret) or get_settings(iid).get("has_secret", False)})
    store.t("CORE").delete_item(Key={"pk": f"MCQDOWN#{iid}", "sk": "META"})
    if secret:
        if config.local_mode() or not config.mcq_secret_arn():
            store.t("CORE").put_item(Item={"pk": f"MCQSECRET#{iid}", "sk": "META", "secret": secret})
        else:
            m = dict(_secret_map(fresh=True) or {})
            m[iid] = secret
            _secrets_client().put_secret_value(SecretId=config.mcq_secret_arn(), SecretString=json.dumps(m))
            _secret_cache.update(at=time.time(), map=m)


def is_set_up(iid):
    s = get_settings(iid)
    return bool(s.get("url")) and bool(s.get("has_secret"))


# ================================================================== calls

def _post(url, payload, timeout):
    if url.startswith("fake://"):
        from . import fake
        return fake.handle(url, payload)
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"content-type": "application/json", "user-agent": "SkillOps/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:   # follows Google's 302 with a GET
        return resp.read().decode("utf-8", "replace")


def _down(iid):
    item = store.get("CORE", f"MCQDOWN#{iid}")
    return item if item and item.get("ttl", 0) >= util.now_ts() else None


def _mark_down(iid, reason):
    store.t("CORE").put_item(Item={"pk": f"MCQDOWN#{iid}", "sk": "META", "reason": reason[:300],
                                   "ttl": int(util.now_ts() + DOWN_SECONDS)})


def call(iid, action, args=None, write=False, idem=None, retries=None, fresh=False):
    """Reads: one try, 5 seconds. Writes: two tries, 10 seconds each, with an idempotency key (the form's
    own key when given, so sending the same form again is done once). Worst case stays under the
    29 seconds a page may take. After a failure, reads say "not answering" at once for a minute."""
    s = get_settings(iid)
    secret = get_secret(iid) if s.get("url") else ""
    if not s.get("url") or not secret:
        raise McqUnavailable("The MCQ test system is not connected for this institution yet.")
    if not write and not fresh:
        down = _down(iid)
        if down:
            raise McqUnavailable(down.get("reason") or "The test system is not answering.")
    payload = {"secret": secret, "action": action, "args": args or {}}
    if write:
        payload["idem"] = (f"{iid}:{idem}" if idem else uuid.uuid4().hex)[:80]
    retries = (1 if write else 0) if retries is None else retries
    timeout = WRITE_TIMEOUT if write else READ_TIMEOUT
    try:
        return _call(iid, s["url"], payload, retries, timeout)
    except McqUnavailable as e:
        _mark_down(iid, str(e))
        raise


def _call(iid, url, payload, retries, timeout):
    last = None
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(min(4, 0.6 * (2 ** attempt)))
        try:
            text = _post(url, payload, timeout)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (401, 403, 404):
                raise McqUnavailable("The MCQ web app refused the request (is it deployed for 'Anyone'?).")
            continue
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = str(e)
            continue
        try:
            body = json.loads(text)
        except ValueError:
            last = "not JSON"
            if "quota" in text.lower() or "too many" in text.lower() or "limit" in text.lower():
                raise McqUnavailable("The test system has reached a Google daily limit. It works again after the limit resets.")
            continue
        if not body.get("ok"):
            if body.get("code") == "bad_secret":
                raise McqUnavailable("The secret does not match. Run setupApi again and paste the new secret into SkillOps.")
            raise McqError(body.get("message") or "The test system could not do this.")
        return body.get("result")
    raise McqUnavailable(f"The test system is not answering ({last}).")


def call_cached(iid, action, args=None, seconds=5):
    """Reads that many people ask for at once (the live monitor) share one call for a few seconds."""
    key = hashlib.sha1(json.dumps([action, args], sort_keys=True).encode()).hexdigest()[:16]
    pk = f"MCQCACHE#{iid}#{key}"
    item = store.get("CORE", pk)
    if item and item.get("ttl", 0) >= util.now_ts():
        return json.loads(item["data"])
    result = call(iid, action, args)
    data = json.dumps(result)
    if len(data) < 350000:
        store.t("CORE").put_item(Item={"pk": pk, "sk": "META", "data": data, "ttl": int(util.now_ts() + seconds)})
    return result


def test_connection(iid):
    result = call(iid, "ping", retries=1, fresh=True)
    store.t("CORE").delete_item(Key={"pk": f"MCQDOWN#{iid}", "sk": "META"})
    store.update("CORE", f"MCQCFG#{iid}", "META", {"last_ok": util.now_iso(), "last_ping": json.dumps(result)[:500]})
    return result
