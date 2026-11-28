"""The files used to deploy: the SAM template, the publisher that copies the static files, the GitHub workflow."""
import os
import re
import sys

import yaml

from helpers import ROOT

sys.path.insert(0, os.path.join(ROOT, "src"))


def _text(*parts):
    return open(os.path.join(ROOT, *parts), encoding="utf-8").read()


def test_the_template_has_no_by_robomy_wording():
    assert "by Robomy" not in _text("template.yaml")


def test_the_support_email_reaches_every_function():
    t = _text("template.yaml")
    assert re.search(r"^  SupportEmail:\n    Type: String\n    Default: \"\"", t, re.M)
    assert "SUPPORT_EMAIL: !Ref SupportEmail" in t
    pattern = re.search(r'SupportEmail:.*?AllowedPattern: "([^"]+)"', t, re.S).group(1).replace("\\\\", "\\")
    assert re.match(pattern, "") and re.match(pattern, "help@robomy.test")      # empty is allowed
    assert not re.match(pattern, "not an address") and not re.match(pattern, "a b@c.d")


class _FakeS3:
    def __init__(self):
        self.put = {}

    def put_object(self, Bucket, Key, Body, ContentType, CacheControl):
        self.put[Key] = (Body, ContentType, CacheControl)


class _FakeCloudFront:
    def create_invalidation(self, **kw):
        self.kw = kw


def test_the_publisher_uploads_the_lean_stylesheet_and_leaves_other_files_alone(monkeypatch):
    import publisher
    fake, cf = _FakeS3(), _FakeCloudFront()
    monkeypatch.setattr(publisher, "s3", fake)
    monkeypatch.setattr(publisher, "cloudfront", cf)
    count = publisher.publish({"WebBucket": "b", "DistributionId": "d"})
    assert count == len(fake.put) and "static/style.css" in fake.put
    css, ctype, cache = fake.put["static/style.css"]
    source = open(os.path.join(publisher.STATIC, "style.css"), "rb").read()
    assert len(css) < len(source) * 0.85 and css.count(b"{") == css.count(b"}")
    assert ctype.startswith("text/css") and "immutable" in cache
    assert fake.put["static/app.js"][0] == open(os.path.join(publisher.STATIC, "app.js"), "rb").read()
    assert cf.kw["DistributionId"] == "d"


def test_the_publisher_ships_the_stylesheet_as_written_if_optimising_fails(monkeypatch):
    import publisher
    fake = _FakeS3()
    monkeypatch.setattr(publisher, "s3", fake)
    monkeypatch.setattr(publisher, "cloudfront", _FakeCloudFront())
    monkeypatch.setattr(publisher.assets, "optimise_css", lambda text: 1 / 0)
    publisher.publish({"WebBucket": "b", "DistributionId": "d"})
    source = open(os.path.join(publisher.STATIC, "style.css"), "rb").read()
    assert fake.put["static/style.css"][0] == source


def test_every_parameter_the_workflow_passes_exists_in_the_template():
    workflow = _text(".github", "workflows", "deploy.yml")
    template = _text("template.yaml")
    passed = re.findall(r'"([A-Za-z]+)=', workflow)
    assert passed and all(re.search(rf"^  {name}:$", template, re.M) for name in passed)
    parsed = yaml.safe_load(workflow)
    assert list(parsed["jobs"]) == ["check", "deploy"] and parsed["jobs"]["deploy"]["environment"] == "production"
