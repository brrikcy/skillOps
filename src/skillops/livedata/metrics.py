"""Custom CloudWatch metrics using the Embedded Metric Format (EMF).

We print one JSON line to the log. CloudWatch reads it and turns it into a metric.
No extra API call is needed, so it is cheap and fast.
"""
import json
import os
import time

NAMESPACE = os.environ.get("METRICS_NAMESPACE", "SkillOpsLiveData")


def emit(name, value=1, unit="Count", **dimensions):
    if os.environ.get("SIMLAB_NO_METRICS"):
        return
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [{
                "Namespace": NAMESPACE,
                "Dimensions": [list(dimensions.keys())] if dimensions else [[]],
                "Metrics": [{"Name": name, "Unit": unit}],
            }],
        },
        name: value,
    }
    record.update({k: str(v) for k, v in dimensions.items()})
    print(json.dumps(record))
