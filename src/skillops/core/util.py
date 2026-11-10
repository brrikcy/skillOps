"""Small shared helpers: times, ids, errors."""
import secrets
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
_fake_now = None


# ------------------------------------------------------------------ time

def set_fake_now(epoch_seconds):
    """Tests only: freeze the clock (None = real time)."""
    global _fake_now
    _fake_now = epoch_seconds


def now_ts():
    return _fake_now if _fake_now is not None else time.time()


def now_iso(ts=None):
    """UTC time as 2026-11-09T10:15:00.123Z (sorts correctly as text)."""
    ts = now_ts() if ts is None else ts
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(ts * 1000) % 1000:03d}Z"


def parse_iso(text):
    if not text:
        return None
    return datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def show_time(iso_text):
    """'2026-11-09T10:15:00.000Z' -> '9 Nov 2026, 3:45 pm' in India time."""
    d = parse_iso(iso_text)
    if not d:
        return ""
    d = d.astimezone(IST)
    return f"{d.day} {d.strftime('%b %Y')}, {d.strftime('%I:%M %p').lstrip('0').lower()}"


# ------------------------------------------------------------------ ids

_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"


def new_id(prefix, n=6):
    """Short readable id such as 'b-k7q4px'. The prefix says what it is."""
    return prefix + "-" + "".join(secrets.choice(_ALPHABET) for _ in range(n))


def new_token(nbytes=32):
    return secrets.token_urlsafe(nbytes)


# ------------------------------------------------------------------ errors

class UserError(Exception):
    """Something the person can fix. Shown on screen in plain English.

    message: what went wrong.  next_step: what to do now.
    """
    status = 400

    def __init__(self, message, next_step="", status=None):
        super().__init__(message)
        self.message = message
        self.next_step = next_step
        if status:
            self.status = status


class NotFound(UserError):
    status = 404


class Forbidden(UserError):
    status = 403


class Conflict(UserError):
    status = 409


def clean_text(value, field, max_len=120, required=True):
    """Trim a form value and check it. Raises UserError with a plain message."""
    v = " ".join(str(value or "").split())
    if required and not v:
        raise UserError(f"Please fill in {field}.")
    if len(v) > max_len:
        raise UserError(f"{field.capitalize()} is too long. Use at most {max_len} characters.")
    return v
