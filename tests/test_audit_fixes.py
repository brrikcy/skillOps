"""The audit fixes: leaner stylesheet, help and footer, branded error page, today hero, skip link and headings."""
import os
import re

from helpers import Browser, body, setup_org
from skillops import web
from skillops.core import assets, html


# ------------------------------------------------------------------ the served stylesheet

def test_a_later_rule_for_the_same_selector_replaces_the_earlier_declaration():
    css = "/* c */ a{color:red;margin:0} b{x:y} a{color:blue} @media (max-width:5px){a{color:green} a{color:pink}}"
    assert assets.optimise_css(css) == "a{margin:0}b{x:y}a{color:blue}@media (max-width:5px){a{color:pink}}"


def test_fallbacks_and_important_declarations_are_kept():
    assert assets.optimise_css("c{display:-webkit-box;display:flex}") == "c{display:-webkit-box;display:flex}"
    assert assets.optimise_css("a{color:red!important} a{color:blue}") == "a{color:red!important}a{color:blue}"
    assert "background:red" in assets.optimise_css("a{background:red} a{background:color-mix(in srgb,red,blue)}")


def test_strings_and_data_urls_survive():
    css = 'a::before{content:"a  b;c";background:url("data:image/svg+xml;utf8,<svg  xmlns=\'x\'/>")}'
    assert assets.optimise_css(css) == css.replace("a::before{", "a::before{")


def _winners(css):
    """For each (place, selector, property) the value that wins. Optimising must not change any of them."""
    out = {}

    def walk(nodes, place):
        for kind, head, rest in nodes:
            if kind == "at":
                walk(rest, place + (head,))
            elif kind == "rule":
                for prop, value, _ in rest:
                    out[(place, head, prop)] = value
    walk(assets._parse(assets._strip_comments(css)), ())
    return out


def test_the_served_stylesheet_gives_every_rule_the_same_winning_values():
    src = open(os.path.join(html.STATIC, "style.css"), encoding="utf-8").read()
    lean = assets.optimise_css(src)
    assert lean.count("{") == lean.count("}")
    assert len(lean) < len(src) * 0.85
    assert _winners(lean) == _winners(src)


def test_static_files_are_compressed_cached_and_not_changed_for_clients_without_gzip(aws):
    b = Browser()
    plain = b.request("GET", "/static/style.css?v=abc")
    packed = b.request("GET", "/static/style.css?v=abc", headers={"accept-encoding": "gzip, br"})
    assert plain["statusCode"] == packed["statusCode"] == 200
    assert "content-encoding" not in plain["headers"] and packed["headers"]["content-encoding"] == "gzip"
    assert "immutable" in plain["headers"]["cache-control"]
    assert "immutable" not in b.get("/static/style.css")["headers"]["cache-control"]
    assert "gzip" in plain["headers"]["vary"].lower() or "accept-encoding" in plain["headers"]["vary"].lower()


# ------------------------------------------------------------------ pages

def test_every_page_has_a_skip_link_a_main_landmark_footer_and_a_heading(boss):
    for path in ("/dashboard", "/admin/institutions", "/help"):
        page = body(boss.get(path))
        assert 'class="skip-link" href="#main"' in page and 'id="main"' in page, path
        assert '<footer class="site-foot"' in page and "Build " in page, path
        assert "<h1" in page, path
        assert "by Robomy" not in page


def test_the_help_page_shows_the_supervisor_part_only_to_the_supervisor(boss):
    ids = setup_org()
    assert "For the supervisor" in body(boss.get("/help"))
    t = Browser()
    t.sign_in_local("trainer1@robomy.test")
    page = body(t.get("/help"))
    assert "Your day in three steps" in page and "For the supervisor" not in page
    assert ids


def test_the_error_page_has_a_clear_title_and_ways_out(boss):
    r = boss.get("/no-such-page")
    page = body(r)
    assert r["statusCode"] == 404
    assert "<title>Page not found · SkillOps</title>" in page
    assert 'href="/help"' in page and "Request ID" in page and "404" in page


def test_student_pages_carry_the_platform_name_in_the_title(aws):
    page = body(Browser().get("/register"))
    assert "<title>Register · Live Data Simulator · SkillOps</title>" in page
    assert 'class="skip-link"' in page and 'id="main"' in page and "<footer" in page


def test_the_support_address_shows_only_when_it_is_set(boss, monkeypatch):
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    assert "mailto:" not in body(boss.get("/help"))
    monkeypatch.setenv("SUPPORT_EMAIL", "help@example.test")
    assert "mailto:help@example.test" in body(boss.get("/help"))


def test_the_settings_tab_uses_the_same_name_as_the_page(boss):
    page = body(boss.get("/admin/mcq"))
    assert "MCQ connections" not in page
    assert web.SETTINGS_NAV and any("MCQ tests" in str(g) for g in web.SETTINGS_NAV)
    assert re.search(r"MCQ tests", page)
