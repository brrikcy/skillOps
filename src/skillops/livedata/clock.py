"""The simulation clock of a feed.

A feed's clock answers one question: "at real moment t, how far has data time got?"
We call that D(t). It is measured in data seconds after data_start.

    D(t) = 0                at go_live
    D(t) grows by `speed`   data seconds per real second while running
    D(t) stays flat         while paused

A row whose data time is `o` seconds after data_start is released at the first
real moment when D(t) >= o. That moment is the row's `_release_at`:

    _release_at = go_live + (data_time - data_start) / speed      (no pauses)

The clock is stored as a short list of straight-line pieces ("segments").
Each segment says: from real time t, data time is d and moves at speed s.
Pausing adds a segment with s = 0. Resuming adds a segment with the old speed.
Earlier segments never change, so rows that were already released keep their
release times. Only `set-clock --force` rewrites history, and that bumps
dataset_version so students know to start again.

All arithmetic uses Fractions, so there is no rounding error.
"""
from fractions import Fraction

from . import timeutil

US = 1_000_000


def _f(x):
    return Fraction(str(x))


def _fs(fr):
    fr = Fraction(fr)
    return str(fr.numerator) if fr.denominator == 1 else f"{fr.numerator}/{fr.denominator}"


def parse_speed(text):
    try:
        s = Fraction(str(text).strip())
    except (ValueError, ZeroDivisionError):
        raise ValueError(f"Speed '{text}' is not a number.")
    if s <= 0:
        raise ValueError("Speed must be more than 0.")
    return s


def new_clock(go_live_us, speed):
    """A fresh clock: data_start is released at go_live, then `speed` data hours per real hour."""
    speed = parse_speed(speed)
    return {
        "segments": [{"t": int(go_live_us), "d": "0", "s": _fs(speed)}],
        "base_speed": _fs(speed),
        "speed": _fs(speed),
        "paused": False,
        "paused_at": None,
    }


def _segments(clock):
    return [(int(s["t"]), _f(s["d"]), _f(s["s"])) for s in clock["segments"]]


def data_offset(clock, t_us):
    """D(t): data seconds after data_start that have been released by real time t."""
    segs = _segments(clock)
    current = None
    for seg in segs:  # later segments with the same t win
        if seg[0] <= t_us:
            current = seg
        else:
            break
    if current is None:
        t0, d0, _ = segs[0]
        return d0 + Fraction(t_us - t0, US) * _f(clock["base_speed"])
    t0, d0, s0 = current
    return d0 + Fraction(t_us - t0, US) * s0


def release_at(clock, offset):
    """Inverse of D: the first real moment (us) at which a row with this data offset is released.

    Returns None if the clock is paused before reaching it (no release time yet).
    """
    offset = Fraction(offset)
    segs = _segments(clock)
    t0, d0, _ = segs[0]
    if offset <= d0:
        base = _f(clock["base_speed"])
        return t0 + _ceil((offset - d0) * US / base)
    for i, (t, d, s) in enumerate(segs):
        end_d = segs[i + 1][1] if i + 1 < len(segs) else None
        if s == 0:
            continue
        if end_d is None or offset <= end_d:
            if offset <= d:
                return t
            return t + _ceil((offset - d) * US / s)
    return None


def _ceil(fr):
    fr = Fraction(fr)
    return -((-fr.numerator) // fr.denominator)


def _floor(fr):
    fr = Fraction(fr)
    return fr.numerator // fr.denominator


def floor_offset(clock, t_us):
    """Largest whole data second already released at t (rows hold whole-second offsets)."""
    return _floor(data_offset(clock, t_us))


def _cut_and_append(clock, at_us, speed):
    d = data_offset(clock, at_us)
    kept = [s for s in clock["segments"] if int(s["t"]) <= at_us]
    if not kept:
        # Everything was in the future (for example, paused before go-live).
        # Keep the old line for times before `at_us` by moving the anchor here.
        kept = [{"t": int(at_us), "d": _fs(d), "s": clock["base_speed"]}]
    kept.append({"t": int(at_us), "d": _fs(d), "s": _fs(speed)})
    return kept


def pause(clock, now_us):
    if clock.get("paused"):
        raise ValueError("The feed is already paused.")
    out = dict(clock)
    out["segments"] = _cut_and_append(clock, now_us, 0)
    out["paused"] = True
    out["paused_at"] = int(now_us)
    return out


def resume(clock, now_us):
    if not clock.get("paused"):
        raise ValueError("The feed is not paused.")
    out = dict(clock)
    out["segments"] = _cut_and_append(clock, now_us, _f(clock["speed"]))
    out["paused"] = False
    out["paused_at"] = None
    return out


def change_speed_from_now(clock, now_us, speed):
    """Change speed from this moment on. Rows already released keep their times."""
    if clock.get("paused"):
        raise ValueError("The feed is paused. Resume it first.")
    speed = parse_speed(speed)
    out = dict(clock)
    out["segments"] = _cut_and_append(clock, now_us, speed)
    out["speed"] = _fs(speed)
    return out


def effective_go_live(clock):
    """The real moment at which data_start is (or was) released, pauses included."""
    return release_at(clock, 0)


def same_history(old, new, now_us, min_offset):
    """True if switching from `old` to `new` cannot change any row students may already have.

    `min_offset` is the smallest data offset of any non-static row in the feed
    (None if the feed has no such rows). If neither clock has reached it by now,
    nothing has been released yet, so the change is safe.
    """
    if old["segments"] == new["segments"] and old["base_speed"] == new["base_speed"]:
        return True
    if min_offset is None:
        return True
    reached = max(data_offset(old, now_us), data_offset(new, now_us))
    return reached < min_offset


def status(clock, now_us):
    if clock.get("paused"):
        return "paused"
    go = effective_go_live(clock)
    if go is not None and now_us < go:
        return "waiting"
    return "running"


def describe(clock, now_us, data_start_us, tz):
    """A small dict used by admin.py, the staff pages and the public status page."""
    d_now = data_offset(clock, now_us)
    go = effective_go_live(clock)
    data_now_us = data_start_us + _floor(d_now * US)
    return {
        "status": status(clock, now_us),
        "speed": float(_f(clock["speed"])),
        "go_live": timeutil.us_to_iso(go) if go is not None else None,
        "go_live_local": timeutil.format_local(go, tz, "%Y-%m-%d %H:%M:%S") if go is not None else None,
        "paused_at": timeutil.us_to_iso(clock["paused_at"]) if clock.get("paused_at") else None,
        "data_time_now": timeutil.format_local(data_now_us, tz) if d_now >= 0 else None,
    }
