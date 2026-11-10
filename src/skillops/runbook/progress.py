"""Ticks and notes, kept per batch x runbook version x item.

PROGRESS table:
  pk "B#<batch>#V#<version>"  sk "I#<item>"   a tick: who, when
  pk "B#<batch>#V#<version>"  sk "N#<item>"   a note for that batch
  pk "EVT#<batch>"            sk "<time>#<n>" history of ticks and un-ticks (newest first when read)

A tick is a conditional write: a second click, a retry or a second trainer at the same moment
all end with the same single tick (the first one), and nobody gets an error.
Numbers on dashboards are always counted from these items, never estimated.
"""
import secrets

from botocore.exceptions import ClientError

from ..core import org, store, util
from ..core.util import Forbidden, NotFound, UserError
from . import service


def _pk(bid, vid):
    return f"B#{bid}#V#{vid}"


def ticks(bid, vid):
    """{item_id: tick item}"""
    return {i["sk"][2:]: i for i in store.by_pk("PROGRESS", _pk(bid, vid), "I#", consistent=True)}


def notes(bid, vid):
    return {i["sk"][2:]: i for i in store.by_pk("PROGRESS", _pk(bid, vid), "N#")}


def _event(bid, vid, item, action, staff, project_id):
    at = util.now_iso()
    store.t("PROGRESS").put_item(Item={"pk": f"EVT#{bid}", "sk": f"{at}#{secrets.token_hex(3)}", "at": at,
                                       "action": action, "item_id": item, "version_id": vid, "project_id": project_id,
                                       "by": staff.email, "by_name": staff.name})


def tick(bid, vid, item_id, staff, project_id=""):
    """Returns (tick, newly_made)."""
    pub = service.published(vid)
    node = pub["index"].get(item_id)
    if not node or not node.get("checkable"):
        raise NotFound("This item cannot be marked complete.", "Refresh the page.")
    item = {"pk": _pk(bid, vid), "sk": f"I#{item_id}", "batch_id": bid, "version_id": vid, "item_id": item_id,
            "project_id": project_id, "by": staff.email, "by_name": staff.name, "at": util.now_iso()}
    try:
        store.t("PROGRESS").put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as e:
        if not store.is_conditional_fail(e):
            raise
        return store.get("PROGRESS", _pk(bid, vid), f"I#{item_id}", consistent=True), False
    _event(bid, vid, item_id, "tick", staff, project_id)
    return item, True


def untick(bid, vid, item_id, staff, project_id=""):
    """A trainer may un-mark only their own tick; the supervisor any tick. Repeating is harmless."""
    existing = store.get("PROGRESS", _pk(bid, vid), f"I#{item_id}", consistent=True)
    if not existing:
        return False
    if not staff.is_super and existing["by"] != staff.email:
        raise Forbidden(f"{existing.get('by_name') or existing['by']} marked this item.",
                        "Only they or your supervisor can un-mark it.")
    kwargs = {}
    if not staff.is_super:
        kwargs = dict(ConditionExpression="#b = :me", ExpressionAttributeNames={"#b": "by"},
                      ExpressionAttributeValues={":me": staff.email})
    try:
        store.t("PROGRESS").delete_item(Key={"pk": _pk(bid, vid), "sk": f"I#{item_id}"}, **kwargs)
    except ClientError as e:
        if store.is_conditional_fail(e):
            raise Forbidden("Someone else has marked this item since.", "Refresh the page.")
        raise
    _event(bid, vid, item_id, "untick", staff, project_id)
    return True


def save_note(bid, vid, item_id, text, staff):
    """The batch's note on an item (empty text clears it). The "needs a repeat" flag on the same item is kept."""
    text = (text or "").strip()
    if len(text) > 1000:
        raise UserError("The note is too long. Use at most 1,000 characters.")
    store.t("PROGRESS").update_item(
        Key={"pk": _pk(bid, vid), "sk": f"N#{item_id}"},
        UpdateExpression="SET #t = :t, #b = :b, by_name = :n, #a = :a, batch_id = :bid, version_id = :vid, item_id = :iid",
        ExpressionAttributeNames={"#t": "text", "#b": "by", "#a": "at"},
        ExpressionAttributeValues={":t": text, ":b": staff.email, ":n": staff.name, ":a": util.now_iso(),
                                   ":bid": bid, ":vid": vid, ":iid": item_id})


def set_repeat(bid, vid, item_id, on, staff):
    """Flag an item as "needs a repeat" for this batch (or clear the flag). Setting it twice is harmless."""
    pub = service.published(vid)
    if item_id not in pub["index"] or not pub["index"][item_id].get("checkable"):
        raise NotFound("This item cannot be flagged.", "Refresh the page.")
    store.t("PROGRESS").update_item(
        Key={"pk": _pk(bid, vid), "sk": f"N#{item_id}"},
        UpdateExpression="SET #r = :r, repeat_by = :b, repeat_by_name = :n, repeat_at = :a, "
                         "batch_id = :bid, version_id = :vid, item_id = :iid",
        ExpressionAttributeNames={"#r": "repeat"},
        ExpressionAttributeValues={":r": bool(on), ":b": staff.email, ":n": staff.name, ":a": util.now_iso(),
                                   ":bid": bid, ":vid": vid, ":iid": item_id})


def events(bid, limit=50):
    resp = store.t("PROGRESS").query(KeyConditionExpression=store.Key("pk").eq(f"EVT#{bid}"),
                                     ScanIndexForward=False, Limit=limit)
    return [store.plain(i) for i in resp.get("Items", [])]


# ================================================================== which version a batch uses

def batch_version(batch, project, pin=True):
    """The runbook version this batch uses for this project. The first time, the batch is pinned to
    the newest published version; after that it stays there until the supervisor moves it."""
    rid = project.get("runbook_id")
    if not rid:
        return None
    pinned = (batch.get("versions") or {}).get(project["id"])
    if pinned:
        return pinned
    rb = service.get_runbook(rid, required=False)
    if not rb or not rb.get("latest"):
        return None
    if pin:
        try:
            store.t("CORE").update_item(
                Key={"pk": f"BATCH#{batch['id']}", "sk": "META"},
                UpdateExpression="SET versions.#p = :v", ConditionExpression="attribute_not_exists(versions.#p)",
                ExpressionAttributeNames={"#p": project["id"]}, ExpressionAttributeValues={":v": rb["latest"]})
        except ClientError as e:
            if not store.is_conditional_fail(e):
                raise
            return org.get_batch(batch["id"])["versions"][project["id"]]
    return rb["latest"]


def move_batch(batch, project, new_vid, staff):
    """Move a batch to another version: carry matching ticks across (old ticks are kept)."""
    old_vid = batch_version(batch, project, pin=False)
    if old_vid == new_vid:
        return {"copied": 0, "unmatched": []}
    new_pub = service.published(new_vid)
    copied, unmatched = 0, []
    if old_vid:
        old_pub = service.published(old_vid)
        mapping, _ = service.match_items(old_pub, new_pub)
        for item_id, t in ticks(batch["id"], old_vid).items():
            target = mapping.get(item_id)
            if not target:
                n = old_pub["index"].get(item_id)
                unmatched.append(n["title"] if n else item_id)
                continue
            try:
                store.t("PROGRESS").put_item(Item={
                    "pk": _pk(batch["id"], new_vid), "sk": f"I#{target}", "batch_id": batch["id"],
                    "version_id": new_vid, "item_id": target, "project_id": project["id"], "by": t["by"],
                    "by_name": t.get("by_name", ""), "at": t["at"], "carried_from": old_vid},
                    ConditionExpression="attribute_not_exists(pk)")
                copied += 1
            except ClientError as e:
                if not store.is_conditional_fail(e):
                    raise
        for item_id, n in notes(batch["id"], old_vid).items():
            target = mapping.get(item_id)
            if target and n.get("text"):
                store.t("PROGRESS").put_item(Item=dict(n, pk=_pk(batch["id"], new_vid), sk=f"N#{target}",
                                                       version_id=new_vid, item_id=target))
    store.t("CORE").update_item(Key={"pk": f"BATCH#{batch['id']}", "sk": "META"},
                                UpdateExpression="SET versions.#p = :v", ExpressionAttributeNames={"#p": project["id"]},
                                ExpressionAttributeValues={":v": new_vid})
    return {"copied": copied, "unmatched": unmatched}


# ================================================================== summaries (counted from the ticks)

def summary(bid, vid):
    """Completion for one batch on one version: counts per item, position, last tick."""
    pub = service.published(vid)
    done = ticks(bid, vid)
    checkables = pub["checkables"]
    total = len(checkables)
    done_ids = {i for i in done if i in pub["index"]}
    per = {}
    for n in checkables:
        k = n["id"]
        is_done = k in done_ids
        node = n
        while node:
            c = per.setdefault(node["id"], [0, 0])
            c[1] += 1
            if is_done:
                c[0] += 1
            node = pub["index"].get(node.get("parent")) if node.get("parent") else None
    furthest = None
    for n in checkables:
        if n["id"] in done_ids:
            furthest = n
    skipped = []
    if furthest:
        for n in checkables:
            if n["id"] == furthest["id"]:
                break
            if n["id"] not in done_ids:
                skipped.append(n)
    last = max(done.values(), key=lambda t: t["at"]) if done else None
    first_open = next((n for n in checkables if n["id"] not in done_ids), None)
    # Where the class really is: the first open item AFTER the furthest tick (a skipped item is a gap,
    # listed separately, not the place to continue from).
    next_up = first_open
    if furthest:
        k = next(i for i, n in enumerate(checkables) if n["id"] == furthest["id"])
        next_up = next((n for n in checkables[k + 1:] if n["id"] not in done_ids), None) or first_open or furthest
    # When each top-level part (step) was finished: the time of its last tick, if every item is ticked.
    step_done_at = {}
    for top in (n for n in pub["nodes"] if n["level"] == 1):
        c = per.get(top["id"], [0, 0])
        if c[1] and c[0] >= c[1]:
            ats = [done[x["id"]]["at"] for x in checkables if x["id"] in done and _under(pub, x, top["id"])]
            step_done_at[top["id"]] = max(ats) if ats else ""
    flags = notes(bid, vid)
    repeats = [pub["index"][i] for i, n in flags.items() if n.get("repeat") and i in pub["index"]]
    repeats.sort(key=lambda n: pub["order"][n["id"]])
    return {"total": total, "done": len(done_ids), "per": per, "done_ids": done_ids, "ticks": done,
            "position": service.path_label(pub, furthest) if furthest else "",
            "furthest": furthest, "skipped": skipped, "last": last, "first_open": first_open, "pub": pub,
            "next_up": next_up, "step_done_at": step_done_at, "repeats": repeats, "flags": flags,
            "repeat_ids": {n["id"] for n in repeats},
            "steps": [(n, per.get(n["id"], [0, 0])) for n in pub["nodes"] if n["level"] == 1]}


def _under(pub, node, ancestor_id):
    p = node.get("parent")
    while p:
        if p == ancestor_id:
            return True
        p = pub["index"][p].get("parent")
    return False


def ticked_on(bid, vid, day_ist):
    """Ticks made on a day (India time, 'YYYY-MM-DD'), oldest first."""
    from ..core.util import IST, parse_iso
    out = []
    for item_id, t in ticks(bid, vid).items():
        d = parse_iso(t.get("at", ""))
        if d and d.astimezone(IST).strftime("%Y-%m-%d") == day_ist:
            out.append(t)
    return sorted(out, key=lambda t: t["at"])


def sections(pub):
    """The parts a runbook is taught in: the parents of the ticked items, in document order.
    (If items have no parent, each item is its own section.)"""
    seen, out = set(), []
    for n in pub["checkables"]:
        sid = n.get("parent") or n["id"]
        if sid not in seen:
            seen.add(sid)
            out.append(pub["index"][sid])
    return out


def section_items(pub, section):
    if section.get("checkable"):
        return [section]
    return [pub["index"][c] for c in section.get("children", []) if pub["index"][c].get("checkable")]
