"""Class days: the days a batch meets (for example Monday, Tuesday and Thursday).

The supervisor sets them on the batch's settings: the weekdays, the first and last date of the course, days
off (holidays) and extra class days. Trainers see them on a calendar, with the days they have already
planned a session for, and plan each class day from there.

Data:  CORE  BATCH#<bid>  META   class_days = {weekdays: [0..6] (0 = Monday), start, end, off: [...], extra: [...]}
"""
import calendar
import re
from datetime import date, timedelta

from .core import org, store, util
from .core.util import UserError

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def get(batch):
    cfg = (batch or {}).get("class_days") or {}
    return {"weekdays": sorted(int(d) for d in cfg.get("weekdays", [])), "start": cfg.get("start", ""),
            "end": cfg.get("end", ""), "off": sorted(cfg.get("off", [])), "extra": sorted(cfg.get("extra", []))}


def is_set(cfg):
    return bool(cfg["weekdays"] or cfg["extra"])


def is_class_day(cfg, day):
    """day: 'YYYY-MM-DD'."""
    if day in cfg["off"]:
        return False
    if day in cfg["extra"]:
        return True
    if cfg["start"] and day < cfg["start"]:
        return False
    if cfg["end"] and day > cfg["end"]:
        return False
    return date.fromisoformat(day).weekday() in cfg["weekdays"]


def _dates(text):
    out = []
    for part in re.split(r"[\s,;]+", text or ""):
        if not part:
            continue
        if not DATE_RE.match(part):
            raise UserError(f'"{part}" is not a date.', "Write dates like 2026-12-25, one per line or separated by commas.")
        try:
            date.fromisoformat(part)
        except ValueError:
            raise UserError(f'"{part}" is not a real date.')
        out.append(part)
    return sorted(set(out))


def save(bid, form, weekdays):
    org.get_batch(bid)
    wd = sorted({int(d) for d in weekdays if str(d).isdigit() and 0 <= int(d) <= 6})
    start, end = (form.get("start") or "").strip(), (form.get("end") or "").strip()
    for v, what in ((start, "first day"), (end, "last day")):
        if v and not DATE_RE.match(v):
            raise UserError(f"Please give the {what} as a date.")
    if start and end and end < start:
        raise UserError("The last day is before the first day.")
    cfg = {"weekdays": wd, "start": start, "end": end, "off": _dates(form.get("off")),
           "extra": _dates(form.get("extra"))}
    store.update("CORE", f"BATCH#{bid}", "META", {"class_days": cfg, "class_days_at": util.now_iso()})
    return cfg


def describe(cfg):
    """'Mon, Tue, Thu · 1 Nov 2026 to 28 Feb 2027'"""
    if not is_set(cfg):
        return "No class days set yet"
    days = ", ".join(WEEKDAYS[d] for d in cfg["weekdays"]) or "Extra days only"
    span = ""
    if cfg["start"] or cfg["end"]:
        span = f" · {_short(cfg['start']) or 'from now'} to {_short(cfg['end']) or 'no end date'}"
    return days + span


def _short(day):
    if not day:
        return ""
    d = date.fromisoformat(day)
    return f"{d.day} {d.strftime('%b %Y')}"


def month(cfg, year, month_no, planned, today):
    """A month for the calendar: {title, prev, next, weeks: [[cell or None]*7]}.
    cell = {date, day, class_day, planned (entries count), today, past}."""
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(year, month_no):
        row = []
        for d in week:
            iso = d.isoformat()
            if d.month != month_no:
                row.append(None)
                continue
            row.append({"date": iso, "day": d.day, "class_day": is_class_day(cfg, iso), "planned": planned.get(iso, 0),
                        "today": iso == today, "past": iso < today})
        weeks.append(row)
    first = date(year, month_no, 1)
    prev_m = (first - timedelta(days=1)).replace(day=1)
    next_m = (first + timedelta(days=32)).replace(day=1)
    return {"title": first.strftime("%B %Y"), "key": first.strftime("%Y-%m"), "prev": prev_m.strftime("%Y-%m"),
            "next": next_m.strftime("%Y-%m"), "weeks": weeks}


def next_class_days(cfg, today, count=5):
    """The next class days from today (today included), for the batch page."""
    out, d = [], date.fromisoformat(today)
    for _ in range(400):
        iso = d.isoformat()
        if cfg["end"] and iso > cfg["end"] and iso not in cfg["extra"]:
            if not any(x >= iso for x in cfg["extra"]):
                break
        if is_class_day(cfg, iso):
            out.append(iso)
            if len(out) >= count:
                break
        d += timedelta(days=1)
    return out
