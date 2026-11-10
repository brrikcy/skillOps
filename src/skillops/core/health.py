"""The /health page: is each part working?"""
import time

import boto3
from botocore.config import Config

from . import config, store

CHECKS = []          # other modules add (name, function) pairs; a function raises on failure


def _timed(name, fn):
    t0 = time.time()
    try:
        detail = fn() or "Working."
        ok = True
    except Exception as e:  # noqa: BLE001  (a failed check is shown, never raised)
        ok, detail = False, f"Not working: {type(e).__name__}: {str(e)[:200]}"
    return {"name": name, "ok": ok, "detail": detail, "ms": int((time.time() - t0) * 1000)}


def _short_client(service):
    return boto3.client(service, region_name=config.region(),
                        config=Config(retries={"max_attempts": 1}, connect_timeout=3, read_timeout=5))


def _table(name):
    def check():
        status = _short_client("dynamodb").describe_table(TableName=config.table(name))["Table"]["TableStatus"]
        if status != "ACTIVE":
            raise RuntimeError(f"table status is {status}")
        return f"Table {config.table(name)} is active."
    return check


def _bucket(bucket):
    def check():
        _short_client("s3").head_bucket(Bucket=bucket)
        return f"Bucket {bucket} answers."
    return check


def _cognito():
    if config.local_mode() or not config.user_pool_id():
        return "Local mode: Cognito is not used."
    pool = _short_client("cognito-idp").describe_user_pool(UserPoolId=config.user_pool_id())["UserPool"]
    return f"User pool {pool['Name']} answers."


def run_all():
    results = []
    for name in sorted(store.all_table_definitions()):
        results.append(_timed(f"Database table: {name.lower()}", _table(name)))
    results.append(_timed("Runbook file storage (S3)", _bucket(config.runbook_bucket())))
    if config.web_bucket():
        results.append(_timed("Web file storage (S3)", _bucket(config.web_bucket())))
    results.append(_timed("Staff sign-in (Cognito)", _cognito))
    for name, fn in CHECKS:
        for r in (fn() if callable(fn) else []):
            results.append(r)
    return results
