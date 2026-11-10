"""Changing feeds and datasets: create a feed, set-clock, pause / resume, activate.

Used by admin.py (your AWS credentials) and by the superadmin web page (through
the staff API). Both paths run exactly the same code.
"""
from . import catalog, clock as clk, timeutil


class FeedError(Exception):
    pass


def ensure_feed(feed, data_start_local, tz, go_live_us, speed, now_us, force=False, say=print):
    """Create the feed if it is new. If it exists, check data_start and apply any clock change."""
    data_start_us = timeutil.parse_local(data_start_local, tz)
    existing = catalog.get_feed(feed)
    if not existing:
        if go_live_us is None:
            raise FeedError(f"Feed '{feed}' is new. Give --go-live to say when it starts.")
        catalog.put_feed({
            "feed": feed, "tz": tz, "data_start_local": timeutil.format_local(data_start_us, tz),
            "data_start_us": data_start_us, "clock": clk.new_clock(go_live_us, speed or 1),
            "clock_version": 1, "created_at": timeutil.us_to_iso(now_us),
        })
        say(f"Created feed '{feed}'. data_start {data_start_local} ({tz}), go-live "
            f"{timeutil.format_local(go_live_us, tz)}, speed {speed or 1}.")
        return catalog.get_feed(feed)
    if existing["data_start_us"] != data_start_us:
        raise FeedError(f"Feed '{feed}' already has data_start {existing['data_start_local']} ({existing['tz']}). "
                        f"Your mapping says {data_start_local} ({tz}). Use the same data_start, or a new feed name.")
    if go_live_us is not None or speed is not None:
        same_go = go_live_us is None or go_live_us == clk.effective_go_live(existing["clock"]) or \
            go_live_us == existing["clock"]["segments"][0]["t"]
        same_speed = speed is None or clk.parse_speed(speed) == clk.parse_speed(existing["clock"]["speed"])
        if same_go and same_speed:
            return existing              # nothing to change (for example, loading one more table into a feed)
        go = go_live_us if go_live_us is not None else clk.effective_go_live(existing["clock"])
        set_clock(feed, go, speed if speed is not None else existing["clock"]["speed"], now_us, force=force, say=say)
    return catalog.get_feed(feed)


def set_clock(feed, go_live_us, speed, now_us, force=False, from_now=False, say=print):
    """Change go-live and/or speed.

    from_now=True: change speed from this moment only (history kept, no reset needed).
    Otherwise the whole clock is replaced. If that changes when rows that may already
    be released were released, you must pass force=True. Then dataset_version goes up
    for every dataset in the feed, and students must start again from an empty since.
    """
    f = catalog.get_feed(feed)
    if not f:
        raise FeedError(f"There is no feed called '{feed}'.")
    old = f["clock"]
    if from_now:
        f["clock"] = clk.change_speed_from_now(old, now_us, speed)
        f["clock_version"] = int(f.get("clock_version", 1)) + 1
        catalog.put_feed(f)
        say(f"Speed of '{feed}' is now {speed} from this moment. Rows already released keep their times.")
        return {"feed": feed, "bumped": False}
    if old.get("paused"):
        raise FeedError("The feed is paused. Resume it first, then set the clock.")
    new = clk.new_clock(go_live_us, speed)
    if old["segments"] == new["segments"] and old["base_speed"] == new["base_speed"]:
        say("The clock is already set like this. Nothing changed.")
        return {"feed": feed, "bumped": False}
    safe = clk.same_history(old, new, now_us, catalog.feed_min_offset(feed))
    if not safe and not force:
        raise FeedError(
            "This change moves release times of rows that students may already have "
            "(the feed has already started, or would start in the past). "
            "Run again with --force. dataset_version will go up and every student must "
            "delete the rows they loaded and fetch again from an empty since.")
    f["clock"] = new
    f["clock_version"] = int(f.get("clock_version", 1)) + 1
    catalog.put_feed(f)
    bumped = []
    if not safe:
        bumped = bump_versions(feed)
        say(f"WARNING: dataset_version went up for {', '.join(bumped) or 'no datasets'}. "
            "Tell students to reset and fetch again from an empty since.")
    say(f"Clock of '{feed}' set: go-live {timeutil.format_local(go_live_us, f['tz'])} ({f['tz']}), speed {speed}.")
    return {"feed": feed, "bumped": bool(bumped), "datasets_bumped": bumped}


def bump_versions(feed):
    names = []
    for d in catalog.list_datasets(feed):
        d["version"] = int(d["version"]) + 1
        for k in ("pk", "sk"):
            d.pop(k, None)
        catalog.put_dataset(d)
        names.append(d["dataset"])
    return names


def pause(feed, now_us, say=print):
    f = _feed(feed)
    try:
        f["clock"] = clk.pause(f["clock"], now_us)
    except ValueError as e:
        raise FeedError(str(e))
    f["clock_version"] = int(f.get("clock_version", 1)) + 1
    catalog.put_feed(f)
    say(f"Feed '{feed}' is paused. Nothing new is released until you resume.")


def resume(feed, now_us, say=print):
    f = _feed(feed)
    try:
        f["clock"] = clk.resume(f["clock"], now_us)
    except ValueError as e:
        raise FeedError(str(e))
    f["clock_version"] = int(f.get("clock_version", 1)) + 1
    catalog.put_feed(f)
    say(f"Feed '{feed}' is running again from the same data time.")


def set_active(dataset, active, now_us, say=print):
    d = catalog.get_dataset(dataset)
    if not d or not d.get("gen"):
        raise FeedError(f"There is no dataset called '{dataset}'.")
    d["active"] = bool(active)
    if active and not d.get("activated_at"):
        d["activated_at"] = timeutil.us_to_iso(now_us)
    for k in ("pk", "sk"):
        d.pop(k, None)
    catalog.put_dataset(d)
    say(f"Dataset '{dataset}' is now {'active' if active else 'inactive'}.")


def _feed(feed):
    f = catalog.get_feed(feed)
    if not f:
        raise FeedError(f"There is no feed called '{feed}'.")
    return f


def feed_report(feed, now_us):
    """What admin.py clock and the staff pages show."""
    f = _feed(feed)
    info = catalog.feed_summary(f, now_us)
    info["datasets"] = [{
        "dataset": d["dataset"], "active": bool(d.get("active")), "version": d["version"],
        "rows_total": d.get("row_count", 0), "rows_released": catalog.released_count(d, f, now_us),
        "rule": d.get("rule"), "table": d.get("table"),
    } for d in catalog.list_datasets(feed)]
    return info
