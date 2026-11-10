"""Supervisor pages: CSV import, audit log, export, health."""
import secrets

from ..core import audit, config, export, health, importer, jobs, store, util  # noqa: F401  (export registers its job)
from ..core.http import csv_download, file_download, q
from ..core.util import NotFound, UserError
from ..web import route


# ------------------------------------------------------------------ CSV import (first set-up)

@route("GET", "/admin/import", access="super")
def import_page(ctx):
    return ctx.render("admin_import.html", title="Import from a CSV file", columns=importer.COLUMNS)


@route("GET", "/admin/import/template.csv", access="super")
def import_template(ctx):
    return file_download("skillops-import-template.csv", importer.TEMPLATE.encode("utf-8"), "text/csv; charset=utf-8")


@route("POST", "/admin/import/check", access="super", name="check import file")
def import_check(ctx):
    f = ctx.req.files().get("file")
    text = f["data"].decode("utf-8-sig", "replace") if f else ctx.form().get("text", "")
    if not text.strip():
        raise UserError("Please choose a CSV file or paste the rows.")
    result = importer.plan(text)
    iid = secrets.token_urlsafe(12)
    store.t("CORE").put_item(Item={"pk": f"IMPORT#{iid}", "sk": "META", "text": text,
                                   "ttl": int(util.now_ts() + 3600)})
    return ctx.render("admin_import_preview.html", title="Check the import", result=result, iid=iid)


@route("POST", "/admin/import/(?P<iid>[A-Za-z0-9_\\-]{1,40})/apply", access="super", name="apply import")
def import_apply(ctx, iid):
    item = store.get("CORE", f"IMPORT#{iid}")
    if not item:
        raise UserError("This import has expired (after one hour).", "Check the file again.")
    result = importer.plan(item["text"])
    if result["errors"]:
        raise UserError("The file still has errors.", "Correct them and check the file again.")
    done = importer.apply(item["text"])
    ctx.audit_detail = f"{len(done)} changes"
    return ctx.redirect("/admin", flash=f"Import finished: {len(done)} change(s) made."
                        if done else "Import finished: nothing needed changing.")


# ------------------------------------------------------------------ audit log

@route("GET", "/admin/audit", access="super")
def audit_page(ctx):
    qy = ctx.req.query
    filters = {k: qy.get(k, "").strip() for k in ("start_date", "end_date", "who", "action", "batch", "result")}
    items = audit.read(**filters, limit=5000 if qy.get("format") == "csv" else 300)
    if qy.get("format") == "csv":
        return csv_download("skillops-audit.csv", audit.CSV_HEADER, audit.csv_rows(items))
    return ctx.render("admin_audit.html", title="Audit log", items=items, f=filters, query=ctx.req.raw_query)


# ------------------------------------------------------------------ export

@route("GET", "/admin/export", access="super")
def export_page(ctx):
    return ctx.render("admin_export.html", title="Export everything", items=jobs.list_jobs("export"))


@route("POST", "/admin/export", access="super", name="start export")
def export_start(ctx):
    jid = jobs.start("export", ctx.staff.email, label="Export everything")
    ctx.audit_target = jid
    return ctx.redirect("/admin/export", flash="The export has started. Refresh this page in a few seconds.")


@route("GET", "/admin/export/(?P<jid>job-[a-z0-9]{6,12})/download", access="super")
def export_download(ctx, jid):
    job = jobs.get(jid)
    if not job or job.get("kind") != "export" or job.get("status") != "done":
        raise NotFound("This export is not ready.", "Go back to the export page and refresh it.")
    key = job["result"]["key"]
    s3 = store.s3()
    if config.local_mode():
        data = s3.get_object(Bucket=config.runbook_bucket(), Key=key)["Body"].read()
        return file_download(key.split("/")[-1], data, "application/zip")
    url = s3.generate_presigned_url("get_object", ExpiresIn=300, Params={
        "Bucket": config.runbook_bucket(), "Key": key,
        "ResponseContentDisposition": f'attachment; filename="{key.split("/")[-1]}"'})
    return ctx.redirect(url)


# ------------------------------------------------------------------ health

@route("GET", "/health", access="super")
def health_page(ctx):
    results = health.run_all()
    ok = all(r["ok"] for r in results)
    return ctx.render("health.html", title="Health", results=results, ok=ok, request_id=ctx.req.request_id,
                      checked_at=util.now_iso(), version=config.version(), q=q)
