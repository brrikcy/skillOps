"""Requests and responses for API Gateway (HTTP API, payload version 2.0)."""
import base64
import csv
import io
import json
import re
import uuid
from email.parser import BytesParser
from email.policy import default as email_policy
from urllib.parse import parse_qsl, quote, urlsplit

from . import config

MAX_FORM_BYTES = 6 * 1024 * 1024


class Request:
    def __init__(self, event):
        self.event = event
        ctx = event.get("requestContext", {}) or {}
        self.method = (ctx.get("http", {}) or {}).get("method", "GET").upper()
        self.path = event.get("rawPath") or "/"
        self.raw_query = event.get("rawQueryString") or ""
        self.query = dict(parse_qsl(self.raw_query, keep_blank_values=True))
        self.headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        self.source_ip = (ctx.get("http", {}) or {}).get("sourceIp", "unknown")
        self.request_id = ctx.get("requestId") or uuid.uuid4().hex[:16]
        self.cookies = {}
        cookie_list = event.get("cookies") or []
        if not cookie_list and self.headers.get("cookie"):
            cookie_list = self.headers["cookie"].split(";")
        for c in cookie_list:
            if "=" in c:
                k, v = c.strip().split("=", 1)
                self.cookies[k.strip()] = v.strip()
        body = event.get("body") or ""
        if event.get("isBase64Encoded"):
            self.body = base64.b64decode(body)
        else:
            self.body = body.encode("utf-8") if isinstance(body, str) else body
        self._form = None
        self._files = None

    # ---- where we are
    def host(self):
        """The address the browser used. CloudFront copies it into x-skillops-host (see template.yaml),
        because it sends API Gateway's own address as Host."""
        if config.public_url():
            return urlsplit(config.public_url()).netloc
        return (self.headers.get("x-skillops-host") or self.headers.get("x-forwarded-host")
                or self.headers.get("host") or "")

    def base_url(self):
        if config.public_url():
            return config.public_url()
        scheme = "http" if config.local_mode() else "https"
        return f"{scheme}://{self.host()}"

    def full_path(self):
        return self.path + ("?" + self.raw_query if self.raw_query else "")

    def wants_json(self):
        return "application/json" in self.headers.get("accept", "") or self.headers.get("x-requested-with") == "fetch"

    def same_origin(self):
        """A POST must come from our own pages (stops other sites posting forms on a signed-in browser)."""
        origin = self.headers.get("origin") or self.headers.get("referer") or ""
        if origin == "null":
            return False
        if not origin:
            return True      # not a browser form post from another site (browsers send Origin on POST)
        return urlsplit(origin).netloc == self.host()

    # ---- form data
    def form(self):
        if self._form is None:
            self._parse_form()
        return self._form

    def files(self):
        if self._files is None:
            self._parse_form()
        return self._files

    def _parse_form(self):
        self._form, self._files = {}, {}
        if len(self.body) > MAX_FORM_BYTES:
            from .util import UserError
            raise UserError("The form is too large.", "Use a smaller file.", status=413)
        ctype = self.headers.get("content-type", "")
        if ctype.startswith("multipart/form-data"):
            msg = BytesParser(policy=email_policy).parsebytes(
                b"Content-Type: " + ctype.encode("latin-1") + b"\r\nMIME-Version: 1.0\r\n\r\n" + self.body)
            for part in msg.iter_parts():
                name = part.get_param("name", header="content-disposition")
                if not name:
                    continue
                filename = part.get_filename()
                data = part.get_payload(decode=True) or b""
                if filename is not None:
                    if data:
                        self._files[name] = {"filename": filename, "data": data,
                                             "content_type": part.get_content_type()}
                else:
                    self._form[name] = data.decode("utf-8", "replace")
        elif ctype.startswith("application/json"):
            try:
                data = json.loads(self.body or b"{}")
            except ValueError:
                data = {}
            self._form = {k: v for k, v in data.items()} if isinstance(data, dict) else {}
        else:
            self._form = dict(parse_qsl(self.body.decode("utf-8", "replace"), keep_blank_values=True))
        # repeated fields (checkbox lists) for urlencoded forms
        self._lists = {}
        if ctype.startswith("application/x-www-form-urlencoded") or not ctype:
            for k, v in parse_qsl(self.body.decode("utf-8", "replace"), keep_blank_values=True):
                self._lists.setdefault(k, []).append(v)

    def form_list(self, name):
        self.form()
        return getattr(self, "_lists", {}).get(name, [])


class Response:
    def __init__(self, status=200, body="", headers=None, cookies=None, binary=False):
        self.status = status
        self.body = body
        self.headers = headers or {}
        self.cookies = list(cookies or [])
        self.binary = binary

    def to_lambda(self):
        out = {"statusCode": self.status, "headers": self.headers}
        if self.binary:
            out["body"] = base64.b64encode(self.body).decode("ascii")
            out["isBase64Encoded"] = True
        else:
            out["body"] = self.body if isinstance(self.body, str) else self.body.decode("utf-8")
        if self.cookies:
            out["cookies"] = self.cookies
        return out


SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "referrer-policy": "same-origin",
    "x-frame-options": "DENY",
    # Everything comes from this app. Inline style attributes are allowed because runbook
    # tables keep their Word cell colours; scripts only from /static.
    "content-security-policy": ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                "script-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
                                "form-action 'self' https://*.amazonaws.com"),
}


def html(body, status=200, cookies=None):
    h = {"content-type": "text/html; charset=utf-8", "cache-control": "no-store"}
    h.update(SECURITY_HEADERS)
    return Response(status, body, h, cookies)


def redirect(location, cookies=None, status=303):
    return Response(status, "", {"location": location, "cache-control": "no-store"}, cookies)


def json_response(obj, status=200, cookies=None):
    return Response(status, json.dumps(obj, ensure_ascii=False, default=str),
                    {"content-type": "application/json; charset=utf-8", "cache-control": "no-store"}, cookies)


def csv_download(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(["" if v is None else v for v in r])
    data = ("﻿" + buf.getvalue()).encode("utf-8")   # the BOM lets Excel read Indian text correctly
    return Response(200, data, {"content-type": "text/csv; charset=utf-8", "cache-control": "no-store",
                                "content-disposition": f'attachment; filename="{filename}"'}, binary=True)


def file_download(filename, data, content_type="application/octet-stream"):
    return Response(200, data, {"content-type": content_type, "cache-control": "no-store",
                                "content-disposition": f'attachment; filename="{filename}"'}, binary=True)


_SAFE_PATH = re.compile(r"^/(?![/\\])[A-Za-z0-9/_\-.~%?=&+:@,]*$")


def safe_next(path):
    """Only allow redirects to our own pages (never to another site). Spaces, tabs, other control
    characters and back slashes are refused, because browsers drop or rewrite them: "/<tab>/evil.example"
    becomes "//evil.example", which is another site."""
    if not path or len(path) > 1000 or not _SAFE_PATH.match(path):
        return "/"
    return path


def q(value):
    return quote(str(value), safe="")
