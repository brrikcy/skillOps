"""Time helpers. All real times are stored in UTC.

Inside the code, a real moment is an integer number of microseconds since
1970-01-01 00:00 UTC ("us"). Integers never have rounding problems, which matters
because the no-skip / no-repeat promise depends on exact comparisons.
"""
import re
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

# Tests replace this function to control "now".
_fake_now_us = None


def set_fake_now(us):
    """Tests only: pretend the current time is `us` (or None for the real clock)."""
    global _fake_now_us
    _fake_now_us = us


def now_us():
    if _fake_now_us is not None:
        return _fake_now_us
    return time.time_ns() // 1000


def dt_to_us(dt):
    if dt.tzinfo is None:
        raise ValueError("datetime must have a time zone")
    delta = dt - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds


def us_to_dt(us):
    return _EPOCH + timedelta(microseconds=us)


def us_to_iso(us):
    """2026-11-16T03:30:00Z (whole seconds) or with .ffffff if needed."""
    dt = us_to_dt(us)
    if dt.microsecond:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def floor_seconds(us):
    return us - (us % 1_000_000)


_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$"
)


def parse_utc_iso(text):
    """Parse a UTC time sent by a student. Returns us, or raises ValueError.

    We accept 2026-11-16T03:30:00Z and 2026-11-16T03:30:00+00:00 (with optional
    fractions). A time without a zone is refused, because it is ambiguous.
    """
    m = _ISO_RE.match(text.strip())
    if not m:
        raise ValueError("not an ISO UTC time")
    y, mo, d, h, mi, s, frac, zone = m.groups()
    micro = int((frac or ".0")[1:].ljust(6, "0"))
    if zone == "Z":
        tz = UTC
    else:
        sign = 1 if zone[0] == "+" else -1
        hh, mm = int(zone[1:3]), int(zone[4:6])
        tz = timezone(sign * timedelta(hours=hh, minutes=mm))
    dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=tz)
    return dt_to_us(dt)


def parse_local(text, tz_name):
    """Parse '2026-11-16 09:00' or '2026-11-16 09:00:00' or '2026-11-16' in a named zone."""
    text = text.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            naive = datetime.strptime(text, fmt)
            return dt_to_us(naive.replace(tzinfo=ZoneInfo(tz_name)))
        except ValueError:
            continue
    raise ValueError(f"Cannot read the time '{text}'. Use YYYY-MM-DD HH:MM.")


def format_local(us, tz_name, fmt="%Y-%m-%d %H:%M:%S"):
    return us_to_dt(us).astimezone(ZoneInfo(tz_name)).strftime(fmt)


def parse_duration(text):
    """'30s', '5m', '2h', '1d' or a plain number of seconds -> seconds (int)."""
    text = str(text).strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(float(text))
