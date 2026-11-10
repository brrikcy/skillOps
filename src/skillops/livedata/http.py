"""Small helpers for API Gateway (HTTP API, payload version 2.0) requests and responses."""
import base64
import json

from . import config
from .store import plain


class ApiError(Exception):
    """Raise this anywhere to send a student-friendly error.

    Every error has the same JSON shape:
    {"platform": "...", "error": {"code": "...", "message": "...", "hint": "..."}}
    """

    def __init__(self, status, code, message, hint="", extra=None, headers=None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.hint = hint
        self.extra = extra or {}
        self.headers = headers or {}


def response(status, body, headers=None, raw_body=None):
    h = {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}
    h.update(headers or {})
    if raw_body is None:
        raw_body = json.dumps(plain(body), ensure_ascii=False)
    return {"statusCode": status, "headers": h, "body": raw_body}


def ok(body, status=200):
    out = {"platform": config.platform_name()}
    out.update(body)
    return response(status, out)


def error_response(err):
    body = {"platform": config.platform_name(),
            "error": {"code": err.code, "message": err.message, "hint": err.hint}}
    body["error"].update(err.extra)
    return response(err.status, body, headers=err.headers)


class Request:
    def __init__(self, event):
        self.event = event
        ctx = event.get("requestContext", {})
        self.method = ctx.get("http", {}).get("method", "GET").upper()
        self.path = event.get("rawPath", "/")
        self.query = event.get("queryStringParameters") or {}
        self.headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        # Behind CloudFront, sourceIp is CloudFront's own address; the student's address is set by our
        # CloudFront Function in x-skillops-viewer (it replaces any value the student sends).
        self.source_ip = (self.headers.get("x-skillops-viewer") or ctx.get("http", {}).get("sourceIp", "unknown"))[:64]
        self.domain = ctx.get("domainName", "")
        self.claims = ctx.get("authorizer", {}).get("jwt", {}).get("claims", {}) or {}
        self._body = event.get("body")
        if self._body and event.get("isBase64Encoded"):
            self._body = base64.b64decode(self._body).decode("utf-8", "replace")

    def json(self):
        if not self._body:
            return {}
        try:
            data = json.loads(self._body)
        except ValueError:
            raise ApiError(400, "bad_json", "The request body is not valid JSON.",
                           "Send a JSON object, for example {\"join_code\": \"JOIN-7KQ4-PX\"}.")
        if not isinstance(data, dict):
            raise ApiError(400, "bad_json", "The request body must be a JSON object.",
                           "Wrap your values in { }.")
        return data

    def api_url(self):
        # Behind CloudFront the browser's address arrives in x-skillops-host (see template.yaml).
        host = self.headers.get("x-skillops-host") or self.domain
        return config.public_api_url() or (f"https://{host}" if host else "")
