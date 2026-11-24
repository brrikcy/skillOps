"""DynamoDB access.

Tables (names come from config.table):

CORE      pk/sk strings, plus index gsi1 (gsi1pk, gsi1sk). Small items:
          COURSE#<id> META           a course (Robomy's);  PROJECT#<id> META  one project of a course
          INST#<id> META             an institution
          PROG#<id> META             a programme (its courses, projects added, projects taken out)
          BATCH#<id> META            a batch
          TRAINER#<email> META       a trainer;    TRAINER#<email> INST#<id>   works with an institution;
                                                   TRAINER#<email> BATCH#<id>  a trainer-batch link
          SESSION#<hash>, IDEM#<key>, PENDING#<id>, IMPORT#<id>   short-lived (TTL attribute "ttl")
          JOB#<id>                   a background job
PROGRESS  pk = "B#<batch>#V#<version>", sk = "I#<item>" (tick) or "N#<item>" (note);
          pk = "EVT#<batch>", sk = "<time>#<n>" (tick history)
AUDIT     pk = "AUDIT#<yyyy-mm>", sk = "<time>#<n>"
"""
import json
import os
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Attr, Key  # noqa: F401  (re-exported for callers)
from botocore.config import Config
from botocore.exceptions import ClientError

from . import config

_resource = None
_tables = {}

BOTO_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"}, connect_timeout=5, read_timeout=15)


def reset():
    """Forget cached connections (tests start a fresh fake AWS each time)."""
    global _resource
    _resource = None
    _tables.clear()


def resource():
    global _resource
    if _resource is None:
        _resource = boto3.resource("dynamodb", region_name=config.region(), config=BOTO_CONFIG)
    return _resource


def client():
    return resource().meta.client


def t(name):
    """t('CORE') -> the boto3 Table object."""
    if name not in _tables:
        _tables[name] = resource().Table(config.table(name))
    return _tables[name]


def plain(value):
    """Turn DynamoDB Decimals back into int/float."""
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


def is_conditional_fail(err):
    return isinstance(err, ClientError) and err.response["Error"]["Code"] == "ConditionalCheckFailedException"


def get(table_name, pk, sk="META", consistent=False):
    item = t(table_name).get_item(Key={"pk": pk, "sk": sk}, ConsistentRead=consistent).get("Item")
    return plain(item) if item else None


def put_new(table_name, item):
    """Put an item only if it does not exist yet. Returns False if it already existed."""
    try:
        t(table_name).put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as e:
        if is_conditional_fail(e):
            return False
        raise


def update(table_name, pk, sk, values, must_exist=True):
    """SET the given attributes. values: {name: value}."""
    names = {f"#a{i}": k for i, k in enumerate(values)}
    vals = {f":v{i}": v for i, v in enumerate(values.values())}
    expr = "SET " + ", ".join(f"#a{i} = :v{i}" for i in range(len(values)))
    kwargs = dict(Key={"pk": pk, "sk": sk}, UpdateExpression=expr,
                  ExpressionAttributeNames=names, ExpressionAttributeValues=vals)
    if must_exist:
        kwargs["ConditionExpression"] = "attribute_exists(pk)"
    t(table_name).update_item(**kwargs)


def query_all(table_name, **kwargs):
    table = t(table_name)
    items = []
    resp = table.query(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.query(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return [plain(i) for i in items]


def scan_all(table_name, **kwargs):
    table = t(table_name)
    items = []
    resp = table.scan(**kwargs)
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"], **kwargs)
        items.extend(resp.get("Items", []))
    return items


def raw_scan(table_name):
    """Every item exactly as stored (Decimals kept). Used by export."""
    return scan_all(table_name)


def by_gsi1(pk_value, sk_prefix=None):
    cond = Key("gsi1pk").eq(pk_value)
    if sk_prefix:
        cond = cond & Key("gsi1sk").begins_with(sk_prefix)
    return query_all("CORE", IndexName="gsi1", KeyConditionExpression=cond)


def by_pk(table_name, pk, sk_prefix=None, consistent=False, newest_first=False):
    cond = Key("pk").eq(pk)
    if sk_prefix:
        cond = cond & Key("sk").begins_with(sk_prefix)
    return query_all(table_name, KeyConditionExpression=cond, ConsistentRead=consistent,
                     ScanIndexForward=not newest_first)


# ------------------------------------------------------------------ table layout

def table_definitions():
    """Same layout as template.yaml. Used to create tables for tests and local mode."""
    S = "S"

    def attr(name, typ=S):
        return {"AttributeName": name, "AttributeType": typ}

    pk_sk = [{"AttributeName": "pk", "KeyType": "HASH"}, {"AttributeName": "sk", "KeyType": "RANGE"}]
    return {
        "CORE": dict(
            KeySchema=pk_sk,
            AttributeDefinitions=[attr("pk"), attr("sk"), attr("gsi1pk"), attr("gsi1sk")],
            GlobalSecondaryIndexes=[{
                "IndexName": "gsi1",
                "KeySchema": [{"AttributeName": "gsi1pk", "KeyType": "HASH"},
                              {"AttributeName": "gsi1sk", "KeyType": "RANGE"}],
                "Projection": {"ProjectionType": "ALL"},
            }],
        ),
        "PROGRESS": dict(KeySchema=pk_sk, AttributeDefinitions=[attr("pk"), attr("sk")]),
        "AUDIT": dict(KeySchema=pk_sk, AttributeDefinitions=[attr("pk"), attr("sk")]),
    }


EXTRA_TABLES = {}   # other modules (live data) add their layouts here


def all_table_definitions():
    defs = dict(table_definitions())
    defs.update(EXTRA_TABLES)
    return defs


def create_tables():
    """Create all tables (fake AWS only; in AWS, SAM creates them)."""
    c = client()
    for name, spec in all_table_definitions().items():
        c.create_table(TableName=config.table(name), BillingMode="PAY_PER_REQUEST", **spec)


def s3():
    return boto3.client("s3", region_name=config.region(),
                        config=Config(retries={"max_attempts": 5, "mode": "standard"},
                                      connect_timeout=5, read_timeout=30, signature_version="s3v4"))


def create_buckets():
    """Fake AWS only."""
    c = s3()
    for b in [config.runbook_bucket()] + ([config.web_bucket()] if config.web_bucket() else []):
        c.create_bucket(Bucket=b, CreateBucketConfiguration={"LocationConstraint": config.region()})


def env_is_set_for_tests():
    return os.environ.get("AWS_ACCESS_KEY_ID") in ("testing", "local")
