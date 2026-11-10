"""DynamoDB access.

Five tables (names come from config.table):

ROWS       pk = "<dataset>#g<generation>", sk = data_offset * 1e9 + row_id (a Number)
           r  = the row's values as a JSON list, exactly as in the source table.
           One partition per dataset upload. Sorted by data time, then row id,
           so a fetch is a single range query on the sort key.
CONFIG     pk/sk strings. Small items: FEED#<feed>, DATASET#<name>, BATCH#<id>,
           STAFF#<email>, CODE#<join code>. Read with get_item or a small scan.
STUDENTS   pk = "STU#<batch>#<roll>" for a student, "KEY#<hash>" for an API key.
           GSI by_batch (batch, roll) lists the students of one batch.
FETCHLOG   pk = student id. sk = "LOG#<time>#<n>" for each request,
           "SUM#<dataset>" for running totals. GSI by_batch on the totals.
RATELIMIT  pk = "<what>#<minute>", a counter that expires after two minutes.
"""
import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key  # noqa: F401  (re-exported)
from botocore.config import Config

from . import config

_resource = None
_tables = {}

BOTO_CONFIG = Config(retries={"max_attempts": 10, "mode": "adaptive"})


def reset():
    """Forget cached connections (tests start a fresh fake AWS each time)."""
    global _resource
    _resource = None
    _tables.clear()


def resource():
    global _resource
    if _resource is None:
        region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
        _resource = boto3.resource("dynamodb", region_name=region, config=BOTO_CONFIG)
    return _resource


def t(name):
    """t('ROWS') -> the boto3 Table object for that table."""
    if name not in _tables:
        _tables[name] = resource().Table(config.table(name))
    return _tables[name]


def plain(value):
    """Turn DynamoDB Decimals back into int/float so they can be sent as JSON."""
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, set):
        return sorted(plain(v) for v in value)
    return value


def dumps(obj):
    return json.dumps(plain(obj), ensure_ascii=False, separators=(",", ":"))


def scan_all(table_name, **kwargs):
    table = t(table_name)
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def query_all(table_name, **kwargs):
    table = t(table_name)
    items = []
    resp = table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def table_definitions():
    """Same layout as template.yaml. Used to create tables for tests and the local demo."""
    S, N = "S", "N"

    def attr(name, typ):
        return {"AttributeName": name, "AttributeType": typ}

    return {
        "ROWS": dict(
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[attr("pk", S), attr("sk", N)],
        ),
        "CONFIG": dict(
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[attr("pk", S), attr("sk", S)],
        ),
        "STUDENTS": dict(
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[attr("pk", S), attr("batch", S), attr("roll", S)],
            GlobalSecondaryIndexes=[{
                "IndexName": "by_batch",
                "KeySchema": [{"AttributeName": "batch", "KeyType": "HASH"}, {"AttributeName": "roll", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        ),
        "FETCHLOG": dict(
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
            AttributeDefinitions=[attr("pk", S), attr("sk", S), attr("sum_batch", S)],
            GlobalSecondaryIndexes=[{
                "IndexName": "by_batch",
                "KeySchema": [{"AttributeName": "sum_batch", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        ),
        "RATELIMIT": dict(
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            AttributeDefinitions=[attr("pk", S)],
        ),
    }


def create_tables():
    """Create all tables (local fake AWS only; in AWS, SAM creates them)."""
    client = resource().meta.client
    for name, spec in table_definitions().items():
        client.create_table(TableName=config.table(name), BillingMode="PAY_PER_REQUEST", **spec)
