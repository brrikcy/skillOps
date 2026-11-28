"""CloudFormation custom resource: publish the static files, and empty the buckets on delete.

On Create / Update: copy every file in ./static to the web bucket under static/, then ask
CloudFront to drop its cached copies.
On Delete: empty the web bucket and the runbook bucket (every version of every file), so
CloudFormation can delete them and "sam delete" removes everything. Data in the runbook bucket
is lost then; export first.

A custom resource must always answer CloudFormation, even when it fails; otherwise the stack
waits for an hour. send() does that in every case.
"""
import json
import mimetypes
import os
import time
import traceback
import urllib.request

import boto3

from skillops.core import assets

s3 = boto3.client("s3")
cloudfront = boto3.client("cloudfront")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


def send(event, status, reason="", data=None):
    body = json.dumps({
        "Status": status, "Reason": (reason or "See CloudWatch logs")[:900],
        # Always the same id: CloudFormation then never sends a "Delete" during an update,
        # so the buckets are emptied only when the whole stack is deleted.
        "PhysicalResourceId": "skillops-site",
        "StackId": event["StackId"], "RequestId": event["RequestId"], "LogicalResourceId": event["LogicalResourceId"],
        "Data": data or {},
    }).encode()
    req = urllib.request.Request(event["ResponseURL"], data=body, method="PUT",
                                 headers={"content-type": "", "content-length": str(len(body))})
    urllib.request.urlopen(req, timeout=30).read()


def publish(props):
    bucket = props["WebBucket"]
    count = 0
    for root, _, files in os.walk(STATIC):
        for name in files:
            path = os.path.join(root, name)
            key = "static/" + os.path.relpath(path, STATIC).replace(os.sep, "/")
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith("javascript"):
                ctype += "; charset=utf-8"
            with open(path, "rb") as f:
                body = f.read()
            if key == "static/style.css":                    # the same lean copy the app serves itself
                try:
                    body = assets.optimise_css(body.decode("utf-8")).encode("utf-8")
                except Exception:  # noqa: BLE001  (never fail a deploy over the stylesheet: ship it as written)
                    traceback.print_exc()
            # Pages ask for /static/x?v=<hash>, so a new version is a new address: safe to cache long.
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType=ctype,
                          CacheControl="public, max-age=31536000, immutable")
            count += 1
    cloudfront.create_invalidation(
        DistributionId=props["DistributionId"],
        InvalidationBatch={"Paths": {"Quantity": 1, "Items": ["/static/*"]}, "CallerReference": str(time.time())})
    return count


def empty(bucket):
    """Delete every object and every old version (the runbook bucket keeps versions)."""
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Bucket=bucket):
        objs = [{"Key": o["Key"], "VersionId": o["VersionId"]}
                for o in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        for i in range(0, len(objs), 1000):
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objs[i:i + 1000], "Quiet": True})


def handler(event, context):
    print(json.dumps({k: v for k, v in event.items() if k != "ResponseURL"}))
    try:
        props = event["ResourceProperties"]
        if event["RequestType"] == "Delete":
            for b in (props["WebBucket"], props.get("RunbookBucket")):
                if not b:
                    continue
                try:
                    empty(b)
                except s3.exceptions.NoSuchBucket:
                    pass
            send(event, "SUCCESS")
            return
        n = publish(props)
        send(event, "SUCCESS", data={"Files": str(n)})
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        send(event, "FAILED", reason=repr(e))
