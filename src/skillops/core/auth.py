"""Staff sign-in and sessions.

Sign-in: the SkillOps sign-in form posts the email and password to our server, which checks
them with Cognito (USER_PASSWORD_AUTH, over HTTPS). Cognito also handles the first-time
password change and "forgot password" codes.

Session: after sign-in we keep a random session id in an HttpOnly cookie. The database keeps
only a hash of it (SESSION#<hash>) with the person's email, role and a CSRF token. Sessions
slide: each visit moves the end time forward (at most once every 10 minutes, to save writes).
"""
import base64
import hashlib
import json
import secrets

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from . import config, org, store, util
from .util import UserError

COOKIE = "so_sid"
FLASH_COOKIE = "so_flash"
PENDING_COOKIE = "so_pend"
PENDING_SECONDS = 3600


def _hash(sid):
    return hashlib.sha256(sid.encode()).hexdigest()


def _cookie(name, value, max_age):
    secure = "" if config.local_mode() else "; Secure"
    return f"{name}={value}; Path=/; HttpOnly; SameSite=Lax; Max-Age={max_age}{secure}"


# ------------------------------------------------------------------ sessions

def create_session(email, role, name):
    sid = secrets.token_urlsafe(32)
    now = util.now_ts()
    ttl = int(now + config.session_hours() * 3600)
    store.t("CORE").put_item(Item={
        "pk": f"SESSION#{_hash(sid)}", "sk": "META", "email": email, "role": role, "name": name,
        "csrf": secrets.token_urlsafe(24), "created_at": util.now_iso(), "seen": int(now), "ttl": ttl})
    return sid, _cookie(COOKIE, sid, config.session_hours() * 3600)


def load_session(req):
    sid = req.cookies.get(COOKIE)
    if not sid or len(sid) > 100:
        return None
    item = store.get("CORE", f"SESSION#{_hash(sid)}")
    now = util.now_ts()
    if not item or item.get("ttl", 0) < now:
        return None
    if now - item.get("seen", 0) > 600:
        store.t("CORE").update_item(
            Key={"pk": f"SESSION#{_hash(sid)}", "sk": "META"}, UpdateExpression="SET seen = :s, #t = :t",
            ExpressionAttributeNames={"#t": "ttl"},
            ExpressionAttributeValues={":s": int(now), ":t": int(now + config.session_hours() * 3600)})
    return item


def end_session(req):
    sid = req.cookies.get(COOKIE)
    if sid and len(sid) <= 100:
        store.t("CORE").delete_item(Key={"pk": f"SESSION#{_hash(sid)}", "sk": "META"})
    return _cookie(COOKIE, "", 0)


def flash_cookie(message, kind="ok"):
    value = base64.urlsafe_b64encode(json.dumps({"m": message[:300], "k": kind}).encode()).decode()
    return _cookie(FLASH_COOKIE, value, 120)


def read_flash(req):
    raw = req.cookies.get(FLASH_COOKIE)
    if not raw:
        return None, None
    try:
        data = json.loads(base64.urlsafe_b64decode(raw.encode()))
        return {"message": str(data.get("m", "")), "kind": data.get("k", "ok")}, _cookie(FLASH_COOKIE, "", 0)
    except (ValueError, TypeError):
        return None, _cookie(FLASH_COOKIE, "", 0)


# ------------------------------------------------------------------ a form that was being saved when the sign-in ended

def stash_pending(path, form):
    """Keep a form sent after the sign-in ended. Returns (id, cookie). The cookie holds a random owner
    key; only the browser that sent the form can see or send it again."""
    pid = secrets.token_urlsafe(12)
    owner = secrets.token_urlsafe(24)
    fields = {k: v for k, v in form.items() if k not in ("csrf",) and isinstance(v, str) and len(v) < 20000}
    store.t("CORE").put_item(Item={"pk": f"PENDING#{pid}", "sk": "META", "kind": "form", "path": path,
                                   "form": fields, "owner": _hash(owner), "at": util.now_iso(),
                                   "ttl": int(util.now_ts() + PENDING_SECONDS)})
    return pid, _cookie(PENDING_COOKIE, owner, PENDING_SECONDS)


def get_pending(pid, req=None):
    """A kept form (only for the browser that sent it) or a sign-in challenge."""
    if not pid or len(pid) > 40:
        return None
    item = store.get("CORE", f"PENDING#{pid}")
    if not item or item.get("ttl", 0) < util.now_ts():
        return None
    if item.get("kind") == "form":
        owner = (req.cookies.get(PENDING_COOKIE) if req else "") or ""
        if not owner or len(owner) > 100 or _hash(owner) != item.get("owner"):
            return None
    return item


# ------------------------------------------------------------------ Cognito

def _cognito():
    return boto3.client("cognito-idp", region_name=config.region(),
                        config=Config(retries={"max_attempts": 4, "mode": "adaptive"},
                                      connect_timeout=5, read_timeout=10))


def _claims(id_token):
    """Read the identity from the ID token Cognito just gave us (direct HTTPS answer, so no signature check needed)."""
    payload = id_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _groups(claims):
    g = claims.get("cognito:groups") or []
    return set(g if isinstance(g, list) else str(g).replace(",", " ").split())


SIGNIN_FAILED = "The email or password is not correct."


def _cognito_error(e):
    code = e.response["Error"]["Code"]
    if code in ("NotAuthorizedException", "UserNotFoundException"):
        msg = e.response["Error"].get("Message", "")
        if "disabled" in msg.lower():
            return UserError("This account is turned off.", "Ask your supervisor.", status=403)
        if "attempts exceeded" in msg.lower():
            return UserError("Too many wrong tries.", "Wait a few minutes and try again.", status=429)
        return UserError(SIGNIN_FAILED, "Check both and try again. If you have forgotten your password, use 'Forgot password'.",
                         status=401)
    if code == "InvalidPasswordException":
        return UserError("This password is too weak.",
                         "Use at least 10 characters with a capital letter, a small letter and a number.")
    if code in ("CodeMismatchException", "ExpiredCodeException"):
        return UserError("The code is wrong or too old.", "Ask for a new code and use it within one hour.")
    if code in ("LimitExceededException", "TooManyRequestsException", "TooManyFailedAttemptsException"):
        return UserError("Too many tries.", "Wait a few minutes and try again.", status=429)
    if code == "InvalidParameterException":
        return UserError("Something in the form is not valid.", "Check the email and try again.")
    return None


def password_sign_in(email, password):
    """Returns ("ok", identity) or ("new_password", cognito_session) or ("reset", None)."""
    email = (email or "").strip().lower()
    if not email or not password:
        raise UserError("Please type your email and password.")
    try:
        r = _cognito().initiate_auth(ClientId=config.user_pool_client_id(), AuthFlow="USER_PASSWORD_AUTH",
                                     AuthParameters={"USERNAME": email, "PASSWORD": password})
    except ClientError as e:
        if e.response["Error"]["Code"] == "PasswordResetRequiredException":
            return "reset", None
        err = _cognito_error(e)
        if err:
            raise err
        raise
    if r.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        return "new_password", r["Session"]
    if r.get("ChallengeName"):
        raise UserError("This sign-in step is not supported here.", "Ask your supervisor.")
    return "ok", identity_from_claims(_claims(r["AuthenticationResult"]["IdToken"]))


def complete_new_password(email, cognito_session, new_password):
    try:
        r = _cognito().respond_to_auth_challenge(
            ClientId=config.user_pool_client_id(), ChallengeName="NEW_PASSWORD_REQUIRED", Session=cognito_session,
            ChallengeResponses={"USERNAME": email, "NEW_PASSWORD": new_password})
    except ClientError as e:
        if e.response["Error"]["Code"] == "NotAuthorizedException" and "session" in e.response["Error"].get("Message", "").lower():
            raise UserError("This step took too long.", "Sign in again with your temporary password.")
        err = _cognito_error(e)
        if err:
            raise err
        raise
    return identity_from_claims(_claims(r["AuthenticationResult"]["IdToken"]))


def start_forgot(email):
    email = (email or "").strip().lower()
    if not org.EMAIL_RE.match(email):
        raise UserError("Please type a valid email address.")
    try:
        _cognito().forgot_password(ClientId=config.user_pool_client_id(), Username=email)
    except ClientError as e:
        err = _cognito_error(e)
        if err and err.status == 429:
            raise err
        # For any other answer we say the same thing, so nobody can find out which emails have accounts.


def confirm_forgot(email, code, new_password):
    email = (email or "").strip().lower()
    try:
        _cognito().confirm_forgot_password(ClientId=config.user_pool_client_id(), Username=email,
                                           ConfirmationCode=(code or "").strip(), Password=new_password or "")
    except ClientError as e:
        err = _cognito_error(e)
        if err:
            raise err
        raise


def identity_from_claims(claims):
    email = str(claims.get("email") or claims.get("cognito:username") or "").lower()
    groups = _groups(claims)
    name = claims.get("name") or email
    if "superadmin" in groups:
        return {"email": email, "role": "superadmin", "name": name}
    if "trainer" in groups:
        t = org.get_trainer(email, required=False)
        if not t or not t.get("enabled"):
            raise UserError("This trainer account is turned off.", "Ask your supervisor.", status=403)
        return {"email": email, "role": "trainer", "name": t.get("name") or name}
    raise UserError("This account is not a SkillOps staff account.", "Ask your supervisor.", status=403)


# ------------------------------------------------------------------ local mode (laptop only)

def local_users():
    """People you can sign in as on the laptop. Never used in AWS."""
    users = [{"email": e.strip().lower(), "role": "superadmin", "name": "Supervisor (local)"}
             for e in config._get("LOCAL_SUPERADMINS", "supervisor@local.test").split(",") if e.strip()]
    for t in org.list_trainers():
        users.append({"email": t["email"], "role": "trainer", "name": t["name"], "enabled": t.get("enabled")})
    return users


def local_sign_in(email):
    if not config.local_mode():
        raise UserError("Not available.", status=404)
    for u in local_users():
        if u["email"] == (email or "").lower():
            if u["role"] == "trainer" and not u.get("enabled", True):
                raise UserError("This trainer account is turned off.", "Ask your supervisor.", status=403)
            return {"email": u["email"], "role": u["role"], "name": u["name"]}
    raise UserError("Unknown local user.")
