"""Leaner copies of the stylesheet for the browser.

src/static/style.css is written in layers (a base, then finishing layers that restyle it). That is easy to change but
heavy to send. This module makes the copy that is served:

  1. a declaration is dropped when a LATER rule with exactly the same selector, in the same place (top level or the same
     @media block), sets the same property. The later one always wins at equal specificity, so the page does not change;
  2. comments and extra white space are removed.

Nothing else is rewritten: selectors, values and the order of rules stay as written. It runs once per process.
"""
import gzip
import re

_WS = re.compile(r"\s+")


def _scan(text, i, stops):
    """Index of the first character in `stops` at or after i that is outside quotes, comments and (...)."""
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and text[i] != q:
                i += 2 if text[i] == "\\" else 1
        elif c == "/" and text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 1
        elif c == "(":
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and c in stops:
            return i
        i += 1
    return n


def _strip_comments(text):
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _squash(value):
    """Collapse white space, but never inside quotes."""
    out, i, n, buf = [], 0, len(value), []
    while i < n:
        c = value[i]
        if c in "\"'":
            if buf:
                out.append(_WS.sub(" ", "".join(buf)))
                buf = []
            j = i + 1
            while j < n and value[j] != c:
                j += 2 if value[j] == "\\" else 1
            out.append(value[i:j + 1])
            i = j + 1
        else:
            buf.append(c)
            i += 1
    if buf:
        out.append(_WS.sub(" ", "".join(buf)))
    return "".join(out).strip()


def _parse(text):
    """-> list of ('rule', selector, [decl, ...]) and ('at', prelude, children-or-raw-text)."""
    nodes, i, n = [], 0, len(text)
    while i < n:
        j = _scan(text, i, "{;}")
        if j >= n:
            break
        if text[j] == ";":                                   # @import / @charset
            nodes.append(("raw", _squash(text[i:j + 1]), None))
            i = j + 1
            continue
        if text[j] == "}":
            i = j + 1
            continue
        head = _squash(text[i:j])
        k = j + 1                                            # find the matching close brace
        depth = 1
        while k < n and depth:
            k = _scan(text, k, "{}")
            if k >= n:
                break
            depth += 1 if text[k] == "{" else -1
            k += 1
        body = text[j + 1:k - 1]
        if head.startswith("@"):
            name = head.split()[0].lower()
            if name in ("@media", "@supports", "@layer", "@container"):
                nodes.append(("at", head, _parse(body)))
            else:
                nodes.append(("raw", head + "{" + _squash(body) + "}", None))
        else:
            nodes.append(("rule", head, _decls(body)))
        i = k
    return nodes


def _decls(body):
    out, i, n = [], 0, len(body)
    while i < n:
        j = _scan(body, i, ";")
        piece = body[i:j].strip()
        i = j + 1
        if not piece or ":" not in piece:
            continue
        prop, _, value = piece.partition(":")
        value = _squash(value)
        important = bool(re.search(r"!\s*important\s*$", value, re.I))
        out.append([prop.strip().lower() if not prop.strip().startswith("--") else prop.strip(), value, important])
    return out


def _risky(value):
    """Values an older browser may not understand: the earlier declaration stays as a fallback."""
    return "color-mix(" in value or "env(" in value


def _dedupe(nodes):
    """Drop declarations that a later identical-selector rule overrides; recurse into @media and friends."""
    seen = {}                                                # selector -> {prop: (value, important)} set by LATER rules
    for node in reversed(nodes):
        kind = node[0]
        if kind == "at":
            _dedupe(node[2])
        elif kind == "rule":
            later = seen.setdefault(node[1], {})
            keep = []
            for d in node[2]:                                # judged against later rules only; a rule may repeat a
                prop, value, important = d                   # property itself on purpose (a fallback value first)
                prev = later.get(prop)
                if prev and not (important and not prev[1]) and not _risky(prev[0]):
                    continue
                keep.append(d)
            for prop, value, important in keep:
                later[prop] = (value, important)
            node[2][:] = keep
    return nodes


def _emit(nodes):
    out = []
    for kind, head, body in nodes:
        if kind == "raw":
            out.append(head)
        elif kind == "at":
            out.append(head + "{" + _emit(body) + "}")
        elif body:
            out.append(head + "{" + ";".join(f"{p}:{v}" for p, v, _ in body) + "}")
    return "".join(out)


def optimise_css(text):
    return _emit(_dedupe(_parse(_strip_comments(text))))


def gzip_bytes(data):
    return gzip.compress(data, compresslevel=9, mtime=0)
