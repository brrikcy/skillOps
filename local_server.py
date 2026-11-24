"""Run SkillOps on your laptop with a fake AWS (moto). No AWS account needed.

    python local_server.py              then open http://127.0.0.1:8790
    python local_server.py --seed       the same, with sample institutions, batches and trainers

The same page code as in AWS answers every request. Differences, on purpose:
- sign-in: a "Local mode: sign in as" list instead of Cognito passwords (laptop only);
- background jobs run in a thread instead of a second Lambda;
- everything is kept in memory and is gone when you stop the server (Ctrl+C).
"""
import argparse
import base64
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))


def local_env():
    """Fake AWS settings. Must run before boto3 is used."""
    for k, v in {"AWS_ACCESS_KEY_ID": "local", "AWS_SECRET_ACCESS_KEY": "local", "AWS_DEFAULT_REGION": "ap-south-1",
                 "AWS_REGION": "ap-south-1", "SKILLOPS_LOCAL": "1"}.items():
        os.environ[k] = v
    os.environ.setdefault("LOCAL_SUPERADMINS", "supervisor@local.test")
    os.environ.setdefault("SIMLAB_SECRET", "local-only-secret")      # live data: API-key hashing on the laptop
    os.environ.setdefault("SIMLAB_NO_METRICS", "1")
    for k in ("USER_POOL_ID", "USER_POOL_CLIENT_ID", "JOBS_FUNCTION", "MCQ_SECRET_ARN"):
        os.environ.pop(k, None)


class Handler(BaseHTTPRequestHandler):
    server_version = "SkillOpsLocal/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("  [local] " + (fmt % args) + "\n")

    def do_GET(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def _dispatch(self):
        from skillops import web
        parts = urlsplit(self.path)
        length = int(self.headers.get("content-length") or 0)
        raw = self.rfile.read(length) if length else b""
        headers = {k.lower(): v for k, v in self.headers.items()}
        headers.setdefault("x-forwarded-host", headers.get("host", ""))
        event = {
            "rawPath": parts.path, "rawQueryString": parts.query, "headers": headers,
            "requestContext": {"http": {"method": self.command, "sourceIp": self.client_address[0]},
                               "requestId": os.urandom(6).hex()},
            "body": "", "isBase64Encoded": False, "_raw_body": raw,
        }
        resp = web.handle(event)
        data = resp.get("body") or ""
        data = base64.b64decode(data) if resp.get("isBase64Encoded") else data.encode("utf-8")
        self.send_response(resp["statusCode"])
        for k, v in resp.get("headers", {}).items():
            self.send_header(k, v)
        for c in resp.get("cookies", []):
            self.send_header("set-cookie", c)
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def start(port=8790, quiet=False):
    """Start the server in a background thread (tests). Returns (server, base_url). The fake AWS must
    already be running."""
    import threading
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.quiet = quiet
    url = f"http://127.0.0.1:{server.server_address[1]}"
    os.environ["PUBLIC_API_URL"] = url        # what /v1/register tells students to put in .env
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, url


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--seed", action="store_true",
                    help="add sample institutions, batches, trainers, the sample runbook, live data and a fake MCQ system")
    ap.add_argument("--sample", action="store_true",
                    help="with --seed: also fill every screen with temporary sample data (ticks over weeks, plans, students, audit)")
    args = ap.parse_args()
    local_env()
    from moto import mock_aws
    with mock_aws():
        from skillops import livedata  # noqa: F401  (registers the live-data tables)
        from skillops.core import store
        store.create_tables()
        store.create_buckets()
        if args.seed:
            import demo
            ids = demo.seed_all(say=print)
            if args.sample:
                import demo_rich
                demo_rich.seed_rich(ids, say=print)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
        print(f"\nSkillOps (local mode) is running at http://127.0.0.1:{args.port}   Stop with Ctrl+C.\n")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nStopped. Everything in this local run is gone.")


if __name__ == "__main__":
    main()
