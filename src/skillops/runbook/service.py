"""Runbooks: upload, structure mapping, templates, preview, publish, versions.

Data:
  CORE  RB#<rid>  META     a runbook in Robomy's shared library (not tied to one institution)
        RBV#<vid> META     one uploaded version: status, patterns, mapping, edits, counts
        TPL#<tid> META     a saved mapping template
  S3    uploads/<vid>.docx                 the uploaded file (kept; the bucket keeps old versions)
        runbooks/<vid>/blocks.json         block summaries (for the mapping and preview screens)
        runbooks/<vid>/tree.json           the published tree (titles, levels, codes, search text)
        runbooks/<vid>/items/<node>.html   one rendered HTML page per item
        media/rb/<vid>/<image>             images, served by CloudFront
Version status: uploaded -> reading -> mapping -> published (or failed).
"""
import gzip
import hashlib
import json
import posixpath
import time

from boto3.dynamodb.conditions import Key

from ..core import config, jobs, store, util
from ..core.util import NotFound, UserError
from . import detect, docxread, render, tree

LEVEL_NAMES = tree.LEVEL_NAMES
_cache = {}           # vid -> (loaded_at, published tree)  (a Lambda keeps it between requests)


def _s3():
    return store.s3()


def _put_json(key, obj):
    body = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    _s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=body, ContentType="application/json",
                     ContentEncoding="gzip")


def _get_json(key):
    obj = _s3().get_object(Bucket=config.runbook_bucket(), Key=key)
    raw = obj["Body"].read()
    if obj.get("ContentEncoding") == "gzip" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


# ================================================================== runbooks and versions

def list_runbooks():
    return sorted(store.by_gsi1("TYPE#RB"), key=lambda r: r["name"].lower())


def get_runbook(rid, required=True):
    item = store.get("CORE", f"RB#{rid}") if rid else None
    if not item and required:
        raise NotFound("This runbook does not exist.", "Go back to the list of runbooks.")
    return item


def create_runbook(name):
    name = util.clean_text(name, "the runbook name", 100)
    if any(r["name"].lower() == name.lower() for r in list_runbooks()):
        raise UserError(f'A runbook called "{name}" already exists.', "Open it and upload a new version instead.")
    rid = util.new_id("r")
    store.put_new("CORE", {"pk": f"RB#{rid}", "sk": "META", "type": "runbook", "id": rid, "name": name,
                           "latest": "", "latest_number": 0, "created_at": util.now_iso(),
                           "gsi1pk": "TYPE#RB", "gsi1sk": name.lower()})
    return rid


def list_versions(rid):
    return sorted(store.by_gsi1(f"RBVERS#{rid}"), key=lambda v: v["number"], reverse=True)


def get_version(vid, required=True):
    item = store.get("CORE", f"RBV#{vid}", consistent=True) if vid else None
    if not item and required:
        raise NotFound("This runbook version does not exist.")
    return item


def _next_number(rid):
    """Version numbers come from an atomic counter, so two uploads at once never share a number."""
    r = store.t("CORE").update_item(Key={"pk": f"RB#{rid}", "sk": "META"}, UpdateExpression="ADD version_counter :one",
                                    ExpressionAttributeValues={":one": 1}, ReturnValues="UPDATED_NEW")
    return int(r["Attributes"]["version_counter"])


def add_version_once(rid, upload_key, filename, data, who):
    """For the browser's direct upload to S3: loading the return page twice makes one version."""
    from botocore.exceptions import ClientError
    marker = {"pk": f"RBUP#{upload_key}", "sk": "META", "rid": rid, "vid": "", "at": util.now_iso()}
    try:
        store.t("CORE").put_item(Item=marker, ConditionExpression="attribute_not_exists(pk)")
    except ClientError as e:
        if not store.is_conditional_fail(e):
            raise
        return (store.get("CORE", f"RBUP#{upload_key}", consistent=True) or {}).get("vid", "")
    try:
        vid = add_version(rid, filename, data, who)
    except Exception:
        store.t("CORE").delete_item(Key={"pk": f"RBUP#{upload_key}", "sk": "META"})
        raise
    store.update("CORE", f"RBUP#{upload_key}", "META", {"vid": vid})
    return vid


def add_version(rid, filename, data, who):
    """Store an uploaded .docx and start reading it in the background."""
    rb = get_runbook(rid)
    if not filename.lower().endswith(".docx"):
        raise UserError("Please upload a Word .docx file.", "In Word: File > Save As > Word Document (.docx).")
    if len(data) > 40 * 1024 * 1024:
        raise UserError("The file is larger than 40 MB.", "Make the images smaller in Word and try again.")
    if data[:2] != b"PK":
        raise UserError("This file is not a Word .docx document (or it is damaged).")
    number = _next_number(rid)
    vid = util.new_id("v")
    key = f"uploads/{vid}.docx"
    _s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=data,
                     ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    store.put_new("CORE", {"pk": f"RBV#{vid}", "sk": "META", "type": "rbversion", "id": vid, "rid": rid,
                           "number": number, "filename": filename[:200], "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest(), "status": "uploaded", "message": "",
                           "uploaded_by": who, "uploaded_at": util.now_iso(), "patterns": [], "mapping": {},
                           "overrides": {}, "checkable": 3, "level_names": LEVEL_NAMES, "counts": {},
                           "template_id": "", "gsi1pk": f"RBVERS#{rid}", "gsi1sk": f"{number:05d}"})
    jobs.start("runbook_read", who, {"vid": vid}, label=f"Read {rb['name']} v{number}")
    return vid


def set_version(vid, values):
    store.update("CORE", f"RBV#{vid}", "META", values)


def _docx(vid):
    obj = _s3().get_object(Bucket=config.runbook_bucket(), Key=f"uploads/{vid}.docx")
    return docxread.Docx(obj["Body"].read())


@jobs.register("runbook_read")
def job_read(job):
    vid = job["params"]["vid"]
    v = get_version(vid)
    set_version(vid, {"status": "reading", "message": "Reading the document..."})
    try:
        return _read(vid, v)
    except docxread.DocxError as e:
        set_version(vid, {"status": "failed", "message": str(e)})
        raise UserError(str(e))
    except Exception:
        set_version(vid, {"status": "failed",
                          "message": "This file could not be read (an error on our side). Upload it again; if it "
                                     "happens again, send the version number to Robomy support."})
        raise


def _read(vid, v):
    d = _docx(vid)
    blocks = d.blocks()
    patterns = detect.find_patterns(blocks)
    _put_json(f"runbooks/{vid}/blocks.json", blocks)
    mapping, checkable = detect.suggest(patterns)
    template_id = ""
    # A saved template that fits well wins over the suggestion; the previous version's mapping too.
    candidates = []
    prev = [x for x in list_versions(v["rid"]) if x["id"] != vid and x.get("mapping")]
    if prev:
        candidates.append(("", prev[0]["mapping"], prev[0].get("checkable", checkable), prev[0].get("level_names")))
    for t in list_templates():
        candidates.append((t["id"], t["mapping"], t.get("checkable", checkable), t.get("level_names")))
    best = max(candidates, key=lambda c: detect.match_score(patterns, c[1]), default=None)
    names = LEVEL_NAMES
    if best and detect.match_score(patterns, best[1]) >= 80:
        mapping = {p["sig"]: int(best[1].get(p["sig"], 0)) for p in patterns}
        checkable, names, template_id = best[2], best[3] or LEVEL_NAMES, best[0]
    nodes, _ = tree.build(blocks, mapping)
    set_version(vid, {"status": "mapping", "patterns": patterns, "mapping": mapping, "checkable": checkable,
                      "level_names": names, "template_id": template_id, "counts": _str_keys(tree.counts(nodes)),
                      "blocks": len(blocks), "message": f"Found {len(patterns)} heading patterns in {len(blocks)} blocks."})
    return {"message": f"Read {len(blocks)} blocks; found {len(patterns)} heading patterns."}


def _str_keys(d):
    return {str(k): v for k, v in d.items()}


def blocks_of(vid):
    return _get_json(f"runbooks/{vid}/blocks.json")


def preview(vid):
    v = get_version(vid)
    blocks = blocks_of(vid)
    for b in blocks:
        b["sig"] = detect.signature(b)[0]
    nodes, pre = tree.build(blocks, v["mapping"], v.get("overrides"))
    return v, blocks, nodes


def batches_on(vid):
    """Batches pinned to this version."""
    from ..core import org
    return [b for b in org.list_batches(include_archived=True) if vid in (b.get("versions") or {}).values()]


# ================================================================== deleting what nobody uses

def _projects_using(rid):
    from ..core import org
    return [f"{j['name']} ({c['name']})" for c, js in org.all_projects() for j in js if j.get("runbook_id") == rid]


def _batches_with_marks(vid):
    """Batches that have ticks or notes on this version (also after moving to a newer one: those are history)."""
    from ..core import org
    out = []
    for b in org.list_batches(include_archived=True):
        r = store.t("PROGRESS").query(KeyConditionExpression=Key("pk").eq(f"B#{b['id']}#V#{vid}"), Limit=1)
        if r.get("Items"):
            out.append(b["name"])
    return out


def version_blockers(vid):
    """Plain reasons why a version cannot be deleted ([] = it can)."""
    why = []
    pinned = [b["name"] for b in batches_on(vid)]
    if pinned:
        why.append("batches are on it: " + ", ".join(pinned[:5]))
    marked = [n for n in _batches_with_marks(vid) if n not in pinned]
    if marked:
        why.append("batches have ticks or notes on it: " + ", ".join(marked[:5]))
    return why


def runbook_blockers(rid):
    why = []
    used = _projects_using(rid)
    if used:
        why.append("projects use it: " + ", ".join(used[:5]))
    for v in list_versions(rid):
        for w in version_blockers(v["id"]):
            why.append(f"version {v['number']}: {w}")
    return why


def _delete_files(vid):
    bucket = config.runbook_bucket()
    keys = [f"uploads/{vid}.docx"]
    for prefix in (f"runbooks/{vid}/", f"media/rb/{vid}/"):
        for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
            keys += [o["Key"] for o in page.get("Contents", [])]
    for i in range(0, len(keys), 1000):
        _s3().delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]], "Quiet": True})


def delete_version(vid):
    """Delete one uploaded version that no batch uses or has marked. Its files go too (the bucket keeps
    S3's own older copies for a while, as with every file there)."""
    v = get_version(vid)
    why = version_blockers(vid)
    if why:
        raise UserError(f"Version {v['number']} cannot be deleted: " + "; ".join(why) + ".",
                        "Move the batches to another version first. Versions with ticks stay as their history.")
    if v["status"] in ("reading", "publishing") and not _stuck(v):
        raise UserError(f"Version {v['number']} is still being {v['status'][:-3]}ed.", "Wait a minute and try again.")
    _delete_files(vid)
    store.t("CORE").delete_item(Key={"pk": f"RBV#{vid}", "sk": "META"})
    _cache.pop(vid, None)
    rb = get_runbook(v["rid"], required=False)
    if rb and rb.get("latest") == vid:      # "current" falls back to the newest published version left
        rest = [x for x in list_versions(v["rid"]) if x["status"] == "published"]
        store.update("CORE", f"RB#{v['rid']}", "META",
                     {"latest": rest[0]["id"] if rest else "", "latest_number": rest[0]["number"] if rest else 0})
    return v


def delete_runbook(rid):
    """Delete a runbook with all its versions, when no project uses it and no batch has marked it."""
    rb = get_runbook(rid)
    why = runbook_blockers(rid)
    if why:
        raise UserError(f'"{rb["name"]}" cannot be deleted: ' + "; ".join(why) + ".",
                        "Choose another runbook for those projects first. Runbooks with ticks stay as history.")
    for v in list_versions(rid):
        _delete_files(v["id"])
        store.t("CORE").delete_item(Key={"pk": f"RBV#{v['id']}", "sk": "META"})
        _cache.pop(v["id"], None)
    store.t("CORE").delete_item(Key={"pk": f"RB#{rid}", "sk": "META"})
    return rb


def _refuse_if_in_use(v):
    if v["status"] == "published" and batches_on(v["id"]):
        names = ", ".join(b["name"] for b in batches_on(v["id"])[:5])
        raise UserError(f"Batches are using version {v['number']} ({names}), so its structure cannot change now: "
                        "their ticks belong to these items.",
                        "Upload the file again as a new version, map it, publish it, then move the batches to it.")


def save_mapping(vid, levels_by_sig, checkable, level_names, template_name=""):
    v = get_version(vid)
    _refuse_if_in_use(v)
    if v["status"] not in ("mapping", "published"):
        raise UserError("This version is not ready for mapping yet.", "Wait until it has been read, then refresh.")
    known = {p["sig"] for p in v["patterns"]}
    mapping = {}
    for sig in known:
        lvl = int(levels_by_sig.get(sig, 0) or 0)
        if not 0 <= lvl <= 4:
            raise UserError("Levels must be 1 to 4, or content.")
        mapping[sig] = lvl
    used = sorted({x for x in mapping.values() if x})
    if not used or used[0] != 1:
        raise UserError("At least one pattern must be Level 1.", "Choose the pattern that marks the biggest sections.")
    if used != list(range(1, len(used) + 1)):
        raise UserError("Levels must follow on without a gap (1, 2, 3 ...).")
    checkable = int(checkable or used[-1])
    if checkable not in used:
        raise UserError("The level that trainers tick must be one of the levels in use.")
    names = [util.clean_text(n, "the level name", 30, required=False) or LEVEL_NAMES[k]
             for k, n in enumerate((level_names + LEVEL_NAMES)[:4])]
    blocks = blocks_of(vid)
    for b in blocks:
        b["sig"] = detect.signature(b)[0]
    nodes, _ = tree.build(blocks, mapping, v.get("overrides"))
    tid = v.get("template_id", "")
    if template_name:
        tid = save_template(template_name, mapping, checkable, names)
    set_version(vid, {"mapping": mapping, "checkable": checkable, "level_names": names,
                      "counts": _str_keys(tree.counts(nodes)), "template_id": tid})
    return nodes


def edit_tree(vid, action, node_id, value=""):
    _refuse_if_in_use(get_version(vid))
    v, blocks, nodes = preview(vid)
    try:
        ov = tree.apply_edit(v.get("overrides") or {}, nodes, blocks, action, node_id, value)
    except ValueError as e:
        raise UserError(str(e))
    nodes2, _ = tree.build(blocks, v["mapping"], ov)
    set_version(vid, {"overrides": ov, "counts": _str_keys(tree.counts(nodes2))})


# ================================================================== templates

def list_templates():
    return sorted(store.by_gsi1("TYPE#TPL"), key=lambda t: t["name"].lower())


def save_template(name, mapping, checkable, names):
    name = util.clean_text(name, "the template name", 60)
    existing = next((t for t in list_templates() if t["name"].lower() == name.lower()), None)
    tid = existing["id"] if existing else util.new_id("t")
    store.t("CORE").put_item(Item={"pk": f"TPL#{tid}", "sk": "META", "type": "template", "id": tid, "name": name,
                                   "mapping": mapping, "checkable": checkable, "level_names": names,
                                   "updated_at": util.now_iso(), "gsi1pk": "TYPE#TPL", "gsi1sk": name.lower()})
    return tid


def template_scores(patterns):
    return sorted(({"id": t["id"], "name": t["name"], "score": detect.match_score(patterns, t["mapping"])}
                   for t in list_templates()), key=lambda x: -x["score"])


def apply_template(vid, tid):
    t = store.get("CORE", f"TPL#{tid}")
    if not t:
        raise NotFound("This template does not exist.")
    v = get_version(vid)
    _refuse_if_in_use(v)
    mapping = {p["sig"]: int(t["mapping"].get(p["sig"], 0)) for p in v["patterns"]}
    blocks = blocks_of(vid)
    for b in blocks:
        b["sig"] = detect.signature(b)[0]
    nodes, _ = tree.build(blocks, mapping, v.get("overrides"))
    set_version(vid, {"mapping": mapping, "checkable": t.get("checkable", 3), "template_id": tid,
                      "level_names": t.get("level_names") or LEVEL_NAMES, "counts": _str_keys(tree.counts(nodes))})


# ================================================================== publish

STUCK_SECONDS = 15 * 60


def _stuck(v):
    started = util.parse_iso(v.get("job_started_at", ""))
    return started is None or util.now_ts() - started.timestamp() > STUCK_SECONDS


def publish(vid, who):
    v = get_version(vid)
    if v["status"] == "publishing" and not _stuck(v):
        raise UserError("This version is being published now.", "Refresh in a minute.")
    if v["status"] not in ("mapping", "published", "publishing"):
        raise UserError("This version cannot be published yet.", "Map its structure first.")
    _refuse_if_in_use(v)
    if not v.get("counts"):
        raise UserError("No items were found with this mapping.", "Map at least one pattern to Level 1.")
    rb = get_runbook(v["rid"])
    return jobs.start("runbook_publish", who, {"vid": vid}, label=f"Publish {rb['name']} v{v['number']}")


@jobs.register("runbook_publish")
def job_publish(job):
    vid = job["params"]["vid"]
    was = get_version(vid)["status"]
    set_version(vid, {"status": "publishing", "message": "Making the pages...", "job_started_at": util.now_iso()})
    try:
        result = _publish(get_version(vid))
    except Exception:
        set_version(vid, {"status": "published" if was == "published" else "mapping",
                          "message": "Publishing stopped because of an error. Try again."})
        raise
    return result


def _publish(v):
    vid = v["id"]
    d = _docx(vid)
    blocks = d.blocks()
    for b in blocks:
        b["sig"] = detect.signature(b)[0]
    nodes, pre_end = tree.build(blocks, v["mapping"], v.get("overrides"))
    checkable = int(v["checkable"])
    stored = {}

    def image_url(path, data):
        name = posixpath.basename(path)
        key = f"media/rb/{vid}/{name}"
        if key not in stored:
            ext = posixpath.splitext(name)[1].lower()
            _s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=data,
                             ContentType=render.SAFE_IMG.get(ext) or "application/octet-stream",
                             CacheControl="public, max-age=31536000")
            stored[key] = True
        return "/" + key

    heading_levels = {n["start"]: n["level"] for n in nodes}
    sigs = {b["i"]: b["sig"] for b in blocks if b.get("sig")}
    r = render.Renderer(d, image_url, heading_levels, sigs)
    bucket = config.runbook_bucket()
    out_nodes = []
    for n in nodes:
        is_check = n["level"] == checkable
        if n["level"] > checkable:
            # Below the ticked level (sub-steps): shown inside their item's page, not as pages.
            out_nodes.append({k: n[k] for k in ("id", "level", "title", "label", "code", "fill", "parent", "short",
                                                 "start")} | {"children": [], "checkable": False, "page": False})
            continue
        end = n["end"] if is_check else n["own_end"]
        html_text = r.render(n["start"], end, skip_first=True)
        _s3().put_object(Bucket=bucket, Key=f"runbooks/{vid}/items/{n['id']}.html", Body=html_text.encode("utf-8"),
                         ContentType="text/html; charset=utf-8")
        text = " ".join((blocks[i].get("text") or "") for i in range(n["start"], end))
        out_nodes.append({"id": n["id"], "level": n["level"], "title": n["title"], "label": n["label"],
                          "code": n["code"], "fill": n["fill"], "parent": n["parent"], "short": n["short"],
                          "start": n["start"], "checkable": is_check, "page": True,
                          "children": [c for c in n["children"]
                                       if next(x for x in nodes if x["id"] == c)["level"] <= checkable],
                          "text": text[:6000]})
    pre_html = r.render(0, pre_end)
    _s3().put_object(Bucket=bucket, Key=f"runbooks/{vid}/items/preamble.html", Body=pre_html.encode("utf-8"),
                     ContentType="text/html; charset=utf-8")
    published_at = util.now_iso()
    published = {"vid": vid, "rid": v["rid"], "number": v["number"], "checkable": checkable,
                 "level_names": v.get("level_names") or LEVEL_NAMES, "nodes": out_nodes,
                 "published_at": published_at, "missing": sorted(r.missing)}
    _put_json(f"runbooks/{vid}/tree.json", published)
    counts = tree.counts(nodes)
    set_version(vid, {"status": "published", "published_at": published_at, "counts": _str_keys(counts),
                      "missing": sorted(r.missing),
                      "message": f"Published: {counts.get(checkable, 0)} items to tick, {len(stored)} images."})
    rb = get_runbook(v["rid"])
    if v["number"] >= int(rb.get("latest_number") or 0):      # republishing an old version never moves "current" back
        store.update("CORE", f"RB#{v['rid']}", "META", {"latest": vid, "latest_number": v["number"]})
    _cache.pop(vid, None)
    if v.get("pdf_name"):                                      # the runbook's PDF: find where each part starts in it
        try:
            index_pdf(vid)
        except Exception:  # noqa: BLE001  (the pages are published either way)
            set_version(vid, {"pdf_message": "The PDF could not be matched to the pages. Attach it again."})
    return {"message": f"Published {len(out_nodes)} items and {len(stored)} images."}


# ================================================================== reading a published version

def published(vid):
    """The published tree, with handy indexes. Cached in memory."""
    hit = _cache.get(vid)
    if hit and time.time() - hit[0] < 3600:
        v = get_version(vid, required=False) or {}
        if v.get("published_at") == hit[1].get("published_at"):
            return hit[1]
    try:
        data = _get_json(f"runbooks/{vid}/tree.json")
    except Exception:  # noqa: BLE001
        raise NotFound("This runbook version is not published.", "Ask your supervisor to publish it.")
    data["index"] = {n["id"]: n for n in data["nodes"]}
    data["checkables"] = [n for n in data["nodes"] if n.get("checkable")]
    data["order"] = {n["id"]: k for k, n in enumerate(data["nodes"])}
    _cache[vid] = (time.time(), data)
    return data


# ================================================================== the runbook's own PDF (for the session PDF)

PDF_MAX = 80 * 1024 * 1024
_pdf_cache = {}


def _pdf_key(vid):
    return f"runbooks/{vid}/source.pdf"


def _pdf_map_key(vid):
    return f"runbooks/{vid}/pdfmap.json"


def attach_pdf(vid, filename, data, who):
    """Keep the runbook as a PDF beside its Word file. A session PDF is cut out of it, in the runbook's own look."""
    v = get_version(vid)
    if not (filename or "").lower().endswith(".pdf") or data[:5] != b"%PDF-":
        raise UserError("Please upload a PDF file.", "In Word: File > Save As > PDF. Keep \"Create bookmarks using Headings\" on.")
    if len(data) > PDF_MAX:
        raise UserError("The PDF is larger than 80 MB.", "Save it again with smaller pictures and try again.")
    from pypdf import PdfReader
    import io
    try:
        r = PdfReader(io.BytesIO(data))
        if r.is_encrypted:
            raise UserError("This PDF is password-protected.", "Save an unprotected copy and upload that.")
        pages = len(r.pages)
    except UserError:
        raise
    except Exception:  # noqa: BLE001
        raise UserError("This PDF could not be read.", "Save it again from Word and upload the new file.")
    if not pages:
        raise UserError("This PDF has no pages.")
    bucket = config.runbook_bucket()
    _s3().put_object(Bucket=bucket, Key=_pdf_key(vid), Body=data, ContentType="application/pdf")
    try:
        _s3().delete_object(Bucket=bucket, Key=_pdf_map_key(vid))
    except Exception:  # noqa: BLE001
        pass
    _pdf_cache.pop(vid, None)
    _pdf_map_cache.pop(vid, None)
    published_now = v["status"] == "published"
    set_version(vid, {"pdf_name": filename[:200], "pdf_size": len(data), "pdf_pages": pages, "pdf_at": util.now_iso(),
                      "pdf_found": 0, "pdf_total": 0,
                      "pdf_message": "Matching the PDF to the pages..." if published_now
                      else "It is matched to the pages when this version is published."})
    if published_now:
        jobs.start("runbook_pdf_index", who, {"vid": vid}, label=f"Match PDF of {get_runbook(v['rid'])['name']} v{v['number']}")
    return pages


@jobs.register("runbook_pdf_index")
def job_pdf_index(job):
    try:
        return index_pdf(job["params"]["vid"])
    except Exception:
        set_version(job["params"]["vid"], {"pdf_message": "The PDF could not be matched to the pages. Attach it again."})
        raise


def index_pdf(vid):
    """Find where each published page of the version starts in its PDF and keep that (a small file beside the PDF)."""
    from . import pdfslice
    pub = published(vid)
    data = _s3().get_object(Bucket=config.runbook_bucket(), Key=_pdf_key(vid))["Body"].read()
    m = pdfslice.index(data, pub)
    _put_json(_pdf_map_key(vid), m)
    _pdf_cache.pop(vid, None)
    _pdf_map_cache.pop(vid, None)
    found, total = m["found"], m["total"]
    set_version(vid, {"pdf_found": found, "pdf_total": total,
                      "pdf_message": f"{found} of {total} pages found in the PDF" + (" (by its bookmarks)." if m["mode"] == "bookmarks" else " (by their headings).")})
    return {"message": f"{found} of {total} pages found in the PDF."}


_pdf_map_cache = {}


def pdf_map(vid):
    """Where each page of the version starts in its PDF (the small index), or None when it has no matched PDF."""
    hit = _pdf_map_cache.get(vid)
    if hit and time.time() - hit[0] < 300:
        return hit[1]
    try:
        m = _get_json(_pdf_map_key(vid))
    except Exception:  # noqa: BLE001
        return None
    _pdf_map_cache[vid] = (time.time(), m)
    return m


def runbook_pdf(vid):
    """(pdf bytes, index) of a version's PDF, or None when it has none (or it is not matched yet)."""
    hit = _pdf_cache.get(vid)
    if hit and time.time() - hit[0] < 600:
        return hit[1]
    try:
        m = _get_json(_pdf_map_key(vid))
        data = _s3().get_object(Bucket=config.runbook_bucket(), Key=_pdf_key(vid))["Body"].read()
    except Exception:  # noqa: BLE001
        return None
    if len(_pdf_cache) >= 3:
        _pdf_cache.pop(next(iter(_pdf_cache)))
    _pdf_cache[vid] = (time.time(), (data, m))
    return data, m


def item_html(vid, node_id):
    key = f"runbooks/{vid}/items/{node_id}.html"
    try:
        return _s3().get_object(Bucket=config.runbook_bucket(), Key=key)["Body"].read().decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def path_label(pub, node):
    chain = [node]
    while chain[0].get("parent"):
        chain.insert(0, pub["index"][chain[0]["parent"]])
    return " · ".join(n["short"] for n in chain)


# ================================================================== versions: carry ticks across

def _norm_title(t):
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in (t or "")).split())


def match_items(old_pub, new_pub):
    """Old checkable item id -> new item id. By action ID first, then by title. Returns (map, unmatched_old)."""
    new_by_code = {}
    new_by_title = {}
    for n in new_pub["checkables"]:
        if n.get("code"):
            new_by_code.setdefault(n["code"], n["id"])
        new_by_title.setdefault(_norm_title(n["title"]), n["id"])
    mapping, unmatched, used = {}, [], set()
    for o in old_pub["checkables"]:
        target = None
        if o.get("code") and o["code"] in new_by_code and new_by_code[o["code"]] not in used:
            target = new_by_code[o["code"]]
        elif _norm_title(o["title"]) in new_by_title and new_by_title[_norm_title(o["title"])] not in used:
            target = new_by_title[_norm_title(o["title"])]
        if target:
            mapping[o["id"]] = target
            used.add(target)
        else:
            unmatched.append(o)
    return mapping, unmatched
