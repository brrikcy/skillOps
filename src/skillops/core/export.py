"""Export everything (and restore it).

The export is one .zip file:
  manifest.json           when, which app, how many items per table
  tables/<TABLE>.jsonl    every item, one per line, in DynamoDB's own JSON form (exact types kept)
  csv/*.csv               the same data in plain tables for Excel: institutions, programmes, projects,
                          batches, trainers, associations, progress, notes, audit
  README.txt              what the files are and how to restore

Left out on purpose: sign-in sessions, idempotency keys and other short-lived items.
Restore (admin.py restore) writes the items back. It never overwrites an existing item unless
you ask for it.
"""
import csv
import io
import json
import zipfile

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from . import config, jobs, store, util

SKIP_PREFIXES = ("SESSION#", "IDEM#", "PENDING#", "IMPORT#", "MCQCACHE#", "MCQSECRET#", "MCQDOWN#", "RBUP#")
EXPORT_TABLES = ["CORE", "PROGRESS", "AUDIT"]     # live data adds its tables in Phase 3

_ser, _de = TypeSerializer(), TypeDeserializer()


def _keep(table, item):
    return not (table == "CORE" and str(item.get("pk", "")).startswith(SKIP_PREFIXES))


def _csv(header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + buf.getvalue()


def build_zip():
    data = {name: [i for i in store.raw_scan(name) if _keep(name, i)] for name in EXPORT_TABLES}
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        manifest = {"app": config.platform_name(), "exported_at": util.now_iso(), "format": 1,
                    "tables": {k: len(v) for k, v in data.items()}}
        for name, items in data.items():
            lines = [json.dumps({k: _ser.serialize(v) for k, v in it.items()}, ensure_ascii=False) for it in items]
            z.writestr(f"tables/{name}.jsonl", "\n".join(lines) + ("\n" if lines else ""))
        for fname, text in _csv_files(data).items():
            z.writestr(f"csv/{fname}", text)
        # The uploaded Word files of published runbook versions: a restore publishes them again.
        files = 0
        for item in data["CORE"]:
            v = store.plain(item)
            if v.get("type") == "rbversion" and v.get("status") == "published":
                try:
                    body = store.s3().get_object(Bucket=config.runbook_bucket(), Key=f"uploads/{v['id']}.docx")["Body"].read()
                except Exception:  # noqa: BLE001  (a missing file is noted, never stops the export)
                    continue
                z.writestr(f"files/uploads/{v['id']}.docx", body)
                files += 1
        manifest["runbook_files"] = files
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("README.txt", README)
    return out.getvalue(), manifest


def _csv_files(data):
    core = [store.plain(i) for i in data["CORE"]]
    by_type = {}
    for i in core:
        by_type.setdefault(i.get("type", ""), []).append(i)
    names = {i["id"]: i["name"] for i in core if i.get("type") in ("institution", "programme", "batch")}
    files = {
        "institutions.csv": _csv(["id", "name", "code", "archived", "created_at"],
                                 [[i["id"], i["name"], i["code"], i.get("archived"), i["created_at"]]
                                  for i in by_type.get("institution", [])]),
        "programmes.csv": _csv(["id", "institution", "name", "archived", "created_at"],
                               [[p["id"], names.get(p["inst_id"], p["inst_id"]), p["name"], p.get("archived"),
                                 p["created_at"]] for p in by_type.get("programme", [])]),
        "projects.csv": _csv(["programme", "order", "project id", "name", "runbook id", "archived"],
                             [[names.get(p["prog_id"], p["prog_id"]), p.get("order"), p["id"], p["name"],
                               p.get("runbook_id", ""), p.get("archived")] for p in by_type.get("project", [])]),
        "batches.csv": _csv(["id", "institution", "programme", "name", "MCQ batch name", "archived"],
                            [[b["id"], names.get(b["inst_id"], ""), names.get(b["prog_id"], ""), b["name"],
                              b.get("mcq_batch", ""), b.get("archived")] for b in by_type.get("batch", [])]),
        "trainers.csv": _csv(["email", "name", "enabled", "created_at"],
                             [[t["email"], t["name"], t.get("enabled"), t["created_at"]]
                              for t in by_type.get("trainer", [])]),
        "associations.csv": _csv(["trainer email", "batch id", "batch", "active", "since", "until"],
                                 [[a["email"], a["batch_id"], names.get(a["batch_id"], ""), a.get("active"),
                                   a.get("since", ""), a.get("until", "")] for a in by_type.get("assoc", [])]),
    }
    prog = [store.plain(i) for i in data["PROGRESS"]]
    files["progress.csv"] = _csv(["batch", "runbook version", "item", "ticked by", "ticked at"],
                                 [[i.get("batch_id", ""), i.get("version_id", ""), i.get("item_id", ""),
                                   i.get("by", ""), i.get("at", "")] for i in prog if str(i["sk"]).startswith("I#")])
    files["notes.csv"] = _csv(["batch", "runbook version", "item", "note", "by", "at"],
                              [[i.get("batch_id", ""), i.get("version_id", ""), i.get("item_id", ""),
                                i.get("text", ""), i.get("by", ""), i.get("at", "")]
                               for i in prog if str(i["sk"]).startswith("N#")])
    aud = sorted((store.plain(i) for i in data["AUDIT"]), key=lambda a: a["at"])
    files["audit.csv"] = _csv(["time (UTC)", "who", "role", "action", "batch", "target", "result", "detail"],
                              [[a["at"], a["who"], a["role"], a["action"], a.get("batch", ""), a.get("target", ""),
                                a["result"], a.get("detail", "")] for a in aud])
    return files


def restore_zip(blob, overwrite=False, say=print):
    """Write every item from an export back. Returns {table: (written, skipped)}."""
    counts = {}
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        manifest = json.loads(z.read("manifest.json"))
        uploads = [n for n in z.namelist() if n.startswith("files/uploads/") and n.endswith(".docx")]
        for n in uploads:
            store.s3().put_object(Bucket=config.runbook_bucket(), Key=n[len("files/"):], Body=z.read(n),
                                  ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        if uploads:
            say(f"  runbook files: {len(uploads)} put back")
        for name in manifest["tables"]:
            if name not in store.all_table_definitions():
                say(f"  skipping {name}: this app has no such table")
                continue
            text = z.read(f"tables/{name}.jsonl").decode("utf-8")
            written = skipped = 0
            table = store.t(name)
            for line in text.splitlines():
                if not line.strip():
                    continue
                item = {k: _de.deserialize(v) for k, v in json.loads(line).items()}
                if overwrite:
                    table.put_item(Item=item)
                    written += 1
                    continue
                try:
                    table.put_item(Item=item, ConditionExpression="attribute_not_exists(pk)")
                    written += 1
                except ClientError as e:
                    if not store.is_conditional_fail(e):
                        raise
                    skipped += 1
            counts[name] = (written, skipped)
            say(f"  {name}: {written} written, {skipped} already there (kept)")
    return counts


@jobs.register("export")
def run_export(job):
    blob, manifest = build_zip()
    key = f"exports/skillops-export-{job['id']}.zip"
    store.s3().put_object(Bucket=config.runbook_bucket(), Key=key, Body=blob, ContentType="application/zip")
    total = sum(manifest["tables"].values())
    return {"message": f"Export ready: {total} items, {len(blob) // 1024 + 1} KB.", "key": key,
            "size": len(blob), "tables": manifest["tables"]}


README = """SkillOps export
================

tables/*.jsonl  Every database item, one per line, in DynamoDB JSON (types kept exactly).
                Use these to restore: python admin.py restore --file this.zip
csv/*.csv       The same data as plain tables you can open in Excel.

files/uploads    The Word files of published runbook versions (restore publishes them again).

Left out on purpose: sign-in sessions, MCQ secrets and other short-lived items.
Live-data rows are not in this file; keep the .db files you uploaded (they can be uploaded again).
"""
