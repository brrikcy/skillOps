"""Page templates (Jinja2, with automatic escaping of every value)."""
import hashlib
import os
import uuid
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config, util

SRC = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATES = os.path.join(SRC, "templates")
STATIC = os.path.join(SRC, "static")


def _static_version():
    h = hashlib.sha1()
    for root, _, files in os.walk(STATIC):
        for f in sorted(files):
            with open(os.path.join(root, f), "rb") as fh:
                h.update(fh.read())
    return h.hexdigest()[:10]


_VERSION = _static_version()


def static(name):
    return f"/static/{name}?v={_VERSION}"


def pct(part, whole):
    if not whole:
        return "0%"
    return f"{round(100 * part / whole)}%"


env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]),
                  trim_blocks=True, lstrip_blocks=True)
env.globals.update(platform_name=config.platform_name, company=config.company,
                   live_data_name=config.live_data_name, static=static, local_mode=config.local_mode,
                   support_email=config.support_email, build_id=lambda: _VERSION,
                   new_idem=lambda: uuid.uuid4().hex, pct=pct)
env.filters["when"] = util.show_time
env.filters["q"] = lambda v: quote(str(v), safe="")


def day_name(value):
    """'2026-11-10' -> 'Tue 10 Nov 2026' (a plain date, no time)."""
    from datetime import date
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return str(value)
    return f"{d.strftime('%a')} {d.day} {d.strftime('%b %Y')}"


env.filters["day_name"] = day_name


def render(name, **ctx):
    return env.get_template(name).render(**ctx)
