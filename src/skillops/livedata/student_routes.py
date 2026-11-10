"""The student API (version 1). Called by student_api.handler."""
import traceback

from . import catalog, clock as clk, config, students, timeutil
from .fetch import fetch
from .http import ApiError, Request, error_response, ok, response


def handle(event):
    req = Request(event)
    now = timeutil.now_us()
    student = None
    try:
        route = (req.method, req.path.rstrip("/") or "/")
        if route == ("GET", "/v1/health"):
            return ok({"status": "ok", "server_time": timeutil.us_to_iso(timeutil.floor_seconds(now))})
        if route == ("GET", "/v1/status"):
            return ok(public_status(now))
        if route == ("POST", "/v1/register"):
            return ok(students.register(req.json(), req.source_ip, req.api_url(), now), status=201)
        if route == ("GET", "/v1/whoami"):
            student = students.authenticate(req, now)
            if student["status"] in ("disabled", "rejected"):
                students.require_active(student)
            return ok({"student": students.public_student(student),
                       "server_time": timeutil.us_to_iso(timeutil.floor_seconds(now))})
        if route == ("GET", "/v1/datasets"):
            student = students.authenticate(req, now)
            students.require_active(student)
            return ok({"datasets": list_for_students(now)})
        if route == ("GET", "/v1/fetch"):
            student = students.authenticate(req, now)
            ctx = {"dataset": (req.query.get("dataset") or "").strip()}
            try:
                body, rows, ctx = fetch(req, student, now)
            except ApiError as e:
                students.log_request(student, ctx["dataset"], req.query.get("since"), None, 0, 0, e.status, now,
                                     error=e.code)
                raise
            students.log_request(student, ctx["dataset"], ctx["since"], ctx["until"], rows, ctx["page"], 200,
                                 now, version=ctx["version"])
            return response(200, None, raw_body=body)
        if req.path.startswith("/v1/") and route[1] in ("/v1/health", "/v1/status", "/v1/register",
                                                         "/v1/whoami", "/v1/datasets", "/v1/fetch"):
            raise ApiError(405, "method_not_allowed", f"{req.method} is not allowed on {req.path}.",
                           "Check the method in the Developer docs (GET or POST).")
        raise ApiError(404, "not_found", f"There is no endpoint at {req.path}.",
                       "Check the URL. Endpoints are /v1/whoami, /v1/datasets and /v1/fetch.")
    except ApiError as e:
        return error_response(e)
    except Exception:
        traceback.print_exc()
        return error_response(ApiError(500, "server_error", "Something went wrong on the server.",
                                       "Try again in a minute. If it keeps happening, tell your trainer."))


def list_for_students(now):
    """Active datasets. Only facts about released rows; nothing about future rows."""
    feeds = {f["feed"]: f for f in catalog.list_feeds(max_age=10)}
    out = []
    for d in catalog.list_datasets(max_age=10):
        if not d.get("active") or not d.get("gen") or d["feed"] not in feeds:
            continue
        f = feeds[d["feed"]]
        info = clk.describe(f["clock"], now, f["data_start_us"], f["tz"])
        out.append({
            "dataset": d["dataset"], "feed": d["feed"], "columns": d["columns"],
            "key_column": d.get("key_column") or None, "dataset_version": d["version"],
            "feed_status": info["status"], "current_data_time": info["data_time_now"],
            "rows_released": catalog.released_count(d, f, now),
        })
    return out


def public_status(now):
    feeds = []
    for f in catalog.list_feeds(max_age=10):
        ds = [d for d in catalog.list_datasets(f["feed"], max_age=10) if d.get("active") and d.get("gen")]
        if not ds:
            continue
        info = clk.describe(f["clock"], now, f["data_start_us"], f["tz"])
        feeds.append({
            "feed": f["feed"], "status": info["status"], "current_data_time": info["data_time_now"],
            "datasets": [{"dataset": d["dataset"], "rows_released": catalog.released_count(d, f, now)} for d in ds],
        })
    return {"server_time": timeutil.us_to_iso(timeutil.floor_seconds(now)), "feeds": feeds,
            "active_learners_last_hour": catalog.active_learners(now)}
