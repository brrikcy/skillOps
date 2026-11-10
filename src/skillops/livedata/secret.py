"""The one server secret (kept in AWS Secrets Manager).

It is used for two things:
1. Hashing API keys. We store HMAC-SHA256(secret, key), never the key itself.
2. Signing page tokens, so a student cannot edit `until` inside a token.

Locally (tests, demo) the secret comes from the SIMLAB_SECRET environment variable.
"""
import base64
import hashlib
import hmac
import json
import os

from . import config

_cached = None


def get_secret():
    global _cached
    if _cached is not None:
        return _cached
    local = os.environ.get("SIMLAB_SECRET")
    if local:
        _cached = local
        return _cached
    import boto3

    region = os.environ.get("AWS_REGION") or "ap-south-1"
    client = boto3.client("secretsmanager", region_name=region)
    _cached = client.get_secret_value(SecretId=config.secret_arn())["SecretString"]
    return _cached


def reset():
    global _cached
    _cached = None


def hash_key(api_key):
    return hmac.new(get_secret().encode(), api_key.strip().encode(), hashlib.sha256).hexdigest()


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_token(payload):
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    mac = hmac.new(get_secret().encode(), b"page|" + body, hashlib.sha256).digest()[:18]
    return _b64(body) + "." + _b64(mac)


def verify_token(token):
    """Returns the payload dict, or None if the token was changed or is not ours."""
    try:
        body_part, mac_part = token.split(".", 1)
        body = _unb64(body_part)
        want = hmac.new(get_secret().encode(), b"page|" + body, hashlib.sha256).digest()[:18]
        if not hmac.compare_digest(want, _unb64(mac_part)):
            return None
        return json.loads(body)
    except Exception:
        return None
