"""Background jobs: work that takes longer than a page request (export, runbook processing).

start() writes JOB#<id> (status "queued") and asks the Jobs Lambda to run it without waiting.
The page shows the job's status; refreshing the page shows progress. On a laptop the job runs
in a background thread instead.
"""
import json
import threading
import traceback

import boto3
from botocore.config import Config

from . import config, store, util

RUNNERS = {}          # kind -> function(job) -> result dict; modules register here
INLINE = False        # tests and demo seeding: run the job at once, in this thread


def register(kind):
    def deco(fn):
        RUNNERS[kind] = fn
        return fn
    return deco


def start(kind, who, params=None, label=""):
    if kind not in RUNNERS:
        raise ValueError(f"unknown job kind {kind}")
    jid = util.new_id("job", 8)
    store.put_new("CORE", {"pk": f"JOB#{jid}", "sk": "META", "type": "job", "id": jid, "kind": kind,
                           "label": label or kind, "params": params or {}, "status": "queued", "message": "",
                           "result": {}, "who": who, "created_at": util.now_iso(), "updated_at": util.now_iso(),
                           "gsi1pk": f"JOBS#{kind}", "gsi1sk": util.now_iso()})
    if INLINE:
        run(jid)
    elif config.local_mode() or not config.jobs_function():
        threading.Thread(target=run, args=(jid,), daemon=True).start()
    else:
        boto3.client("lambda", region_name=config.region(),
                     config=Config(retries={"max_attempts": 4, "mode": "standard"}, connect_timeout=5,
                                   read_timeout=10)).invoke(
            FunctionName=config.jobs_function(), InvocationType="Event",
            Payload=json.dumps({"job_id": jid}).encode())
    return jid


def get(jid):
    return store.get("CORE", f"JOB#{jid}", consistent=True)


def list_jobs(kind, limit=20):
    items = store.by_gsi1(f"JOBS#{kind}")
    return sorted(items, key=lambda j: j["created_at"], reverse=True)[:limit]


def set_status(jid, status, message="", result=None):
    values = {"status": status, "message": message[:1000], "updated_at": util.now_iso()}
    if result is not None:
        values["result"] = result
    store.update("CORE", f"JOB#{jid}", "META", values)


def progress(jid, message):
    store.update("CORE", f"JOB#{jid}", "META", {"message": message[:1000], "updated_at": util.now_iso()})


def run(jid):
    job = get(jid)
    if not job or job["status"] not in ("queued",):
        return
    set_status(jid, "running", "Started.")
    try:
        result = RUNNERS[job["kind"]](job) or {}
        set_status(jid, "done", result.pop("message", "Finished."), result)
    except Exception as e:  # noqa: BLE001  (a job must always end with a status)
        traceback.print_exc()
        msg = getattr(e, "message", None) or "The job stopped because of an error on our side."
        set_status(jid, "failed", f"{msg} (job {jid})")
