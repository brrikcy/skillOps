"""Turn the blocks and the confirmed mapping into a tree of items.

An item starts at a heading block (a block whose pattern is mapped to Level 1-4) and runs until the
next heading of the same or a higher level. Edits from the preview screen (rename, merge, move a
boundary) are stored as overrides keyed by block number, so they can be applied again to the
same file at any time.
"""
import re

from .detect import ID_RE

LEVEL_NAMES = ["Step", "Part", "Action", "Sub-step"]
DASH_SPLIT = re.compile(r"\s+[—–-]\s+|:\s+")


def block_level(b, mapping, overrides):
    lv = (overrides.get("levels") or {}).get(str(b["i"]))
    if lv is not None:
        return int(lv)
    return int(mapping.get(b.get("sig") or "", 0) or 0)


def _title(b):
    if b["kind"] == "tbl":
        right = (b.get("right") or "").strip().split("\n")[0]
        return right or (b.get("left") or "").strip()
    return b["text"].split("\n")[0]


def _nice(s):
    s = s.strip()
    return s.title() if s.upper() == s else s


def short_label(node):
    """'Step 2', 'Part 3', '2.9' - used for positions like 'Step 2 · Part 3 · 2.9'."""
    if node.get("code"):
        return node["code"]
    if node.get("label") and not node.get("is_action"):
        return _nice(node["label"])
    first = DASH_SPLIT.split(node["title"], maxsplit=1)[0]
    first = _nice(first)
    return first if len(first) <= 28 else first[:27] + "…"


def build(blocks, mapping, overrides=None):
    """Returns (nodes, preamble_end). nodes are in document order:
    {id, level, start, end, own_end, title, label, code, fill, parent, children}"""
    overrides = overrides or {}
    titles = overrides.get("titles") or {}
    nodes, stack = [], []
    for b in blocks:
        lvl = block_level(b, mapping, overrides)
        if not lvl:
            continue
        is_band = b["kind"] == "tbl"
        code = None
        if is_band:
            m = ID_RE.match((b.get("right") or "").strip())
            code = m.group(1) if m else None
        node = {"id": f"n{b['i']}", "level": lvl, "start": b["i"], "title": titles.get(str(b["i"])) or _title(b),
                "label": (b.get("left") or "").strip() if is_band else "", "code": code,
                "fill": b.get("fill") if is_band else None, "is_action": bool(code and is_band),
                "children": [], "parent": None}
        while stack and stack[-1]["level"] >= lvl:
            stack.pop()
        if stack:
            node["parent"] = stack[-1]["id"]
            stack[-1]["children"].append(node["id"])
        stack.append(node)
        nodes.append(node)
    n_blocks = len(blocks)
    for k, node in enumerate(nodes):
        node["own_end"] = nodes[k + 1]["start"] if k + 1 < len(nodes) else n_blocks
        node["end"] = n_blocks
        for later in nodes[k + 1:]:
            if later["level"] <= node["level"]:
                node["end"] = later["start"]
                break
        node["short"] = short_label(node)
    preamble_end = nodes[0]["start"] if nodes else n_blocks
    return nodes, preamble_end


def counts(nodes):
    out = {}
    for n in nodes:
        out[n["level"]] = out.get(n["level"], 0) + 1
    return out


def by_id(nodes):
    return {n["id"]: n for n in nodes}


def path_of(node, index):
    """Ancestors from the top down, then the node."""
    chain = [node]
    while chain[0].get("parent"):
        chain.insert(0, index[chain[0]["parent"]])
    return chain


def apply_edit(overrides, nodes, blocks, action, node_id, value=""):
    """Change the overrides for one edit from the preview screen. Returns the new overrides."""
    ov = {"levels": dict(overrides.get("levels") or {}), "titles": dict(overrides.get("titles") or {})}
    index = by_id(nodes)
    node = index.get(node_id)
    if not node:
        raise ValueError("That item no longer exists. Refresh the page.")
    start = str(node["start"])
    if action == "rename":
        value = " ".join(value.split())[:200]
        if not value:
            raise ValueError("Please type the new name.")
        ov["titles"][start] = value
    elif action == "merge":
        # The item's heading becomes ordinary content of the item before it.
        ov["levels"][start] = 0
    elif action in ("earlier", "later"):
        # Move the boundary: the item starts one block earlier or later.
        target = node["start"] + (-1 if action == "earlier" else 1)
        if target < 0 or target >= len(blocks) or target >= node["own_end"] and action == "later":
            raise ValueError("The boundary cannot move further that way.")
        prev = [n for n in nodes if n["start"] < node["start"]]
        if action == "earlier" and prev and prev[-1]["start"] >= target:
            raise ValueError("The boundary cannot move past the item before it.")
        ov["levels"][start] = 0
        ov["levels"][str(target)] = node["level"]
        ov["titles"][str(target)] = ov["titles"].pop(start, None) or node["title"]
    else:
        raise ValueError("Unknown change.")
    return ov
