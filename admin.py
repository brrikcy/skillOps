"""SkillOps command line for the supervisor. Uses YOUR AWS credentials (aws configure).

Back-up and set-up:
    python admin.py export  --out skillops-backup.zip         save everything to a file on this laptop
    python admin.py restore --file skillops-backup.zip         write an export back (keeps existing items)
    python admin.py import-csv --file setup.csv               the same as Settings > Import, from here
    python admin.py outputs                                   show the stack's addresses and names

SkillOps Live Data (large .db files are loaded from your laptop, not through the web page):
    python admin.py inspect --db uber_live.db
    python admin.py upload --db uber_live.db --mapping datasets.json --feed uber --go-live "2026-11-16 09:00"
    python admin.py activate --dataset uber_trips_live
    python admin.py clock --feed uber          pause / resume / set-clock / list-datasets / replace

Add --stack NAME if your stack is not called "skillops", and --region if it is not ap-south-1.
Run "python admin.py <command> --help" for every option.
"""
import argparse
import json
import os
import sys

import boto3

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))

from skillops.core import audit  # noqa: E402
from skillops.livedata import catalog, clock as clk, feedops, loader, timeutil  # noqa: E402

WHO = "admin.py"
OUTPUT_ENV = {"TableCore": "TABLE_CORE", "TableProgress": "TABLE_PROGRESS", "TableAudit": "TABLE_AUDIT",
              "TableLiveRows": "TABLE_LIVE_ROWS", "TableLiveConfig": "TABLE_LIVE_CONFIG",
              "TableLiveStudents": "TABLE_LIVE_STUDENTS", "TableLiveFetchLog": "TABLE_LIVE_FETCHLOG",
              "TableLiveRateLimit": "TABLE_LIVE_RATELIMIT", "RunbookBucketName": "RUNBOOK_BUCKET",
              "UserPoolId": "USER_POOL_ID", "UserPoolClientId": "USER_POOL_CLIENT_ID",
              "ServerSecretArn": "SERVER_SECRET_ARN"}


def connect(stack, region):
    """Read the stack's outputs and point the code at the real tables."""
    global WHO
    os.environ["AWS_REGION"] = os.environ["AWS_DEFAULT_REGION"] = region
    try:
        resp = boto3.client("cloudformation", region_name=region).describe_stacks(StackName=stack)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Could not read the stack '{stack}' in {region}: {e}. "
                 "Check 'aws configure' and the --stack / --region values.")
    outputs = {o["OutputKey"]: o["OutputValue"] for o in resp["Stacks"][0].get("Outputs", [])}
    for key, env in OUTPUT_ENV.items():
        if key in outputs:
            os.environ[env] = outputs[key]
    try:
        arn = boto3.client("sts", region_name=region).get_caller_identity()["Arn"]
        WHO = f"admin.py ({arn.split('/')[-1]})"
    except Exception:  # noqa: BLE001
        pass
    return outputs


def log(action, target="", batch="", result="ok", detail=""):
    try:
        audit.write(WHO, "superadmin", action, batch, target, result, detail)
    except Exception as e:  # noqa: BLE001
        print(f"(could not write the audit log: {e})")


# ------------------------------------------------------------------ SkillOps Live Data commands

def table(rows, headers):
    rows = [[("" if v is None else str(v)) for v in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h) for i, h in enumerate(headers)]
    print("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("  ".join("-" * w for w in widths))
    for r in rows:
        print("  ".join(v.ljust(w) for v, w in zip(r, widths)))


# ------------------------------------------------------------------ commands

def cmd_inspect(a):
    src = loader.open_source(a.db or a.csv, a.table)
    for t in loader.inspect(src):
        print(f"\nTable {t['table']}: {t['rows']:,} rows")
        table([(c, typ or "(none)") for c, typ in t["columns"]], ["column", "declared type"])
        for d in t["dates"]:
            print(f"  date-like {d['column']}: earliest {d['min']}, latest {d['max']}, "
                  f"NULL {d['nulls']:,}, empty '' {d['empty']:,}")


def _mapping_from_args(a):
    if a.mapping:
        m = loader.read_mapping(a.mapping)
    else:
        if not (a.dataset and a.data_start):
            sys.exit("Give --mapping datasets.json, or --dataset and --data-start (and the time columns).")
        entry = {"table": a.table or loader.open_source(a.db or a.csv).tables()[0], "dataset": a.dataset}
        for k in ("time_column", "date_column", "hour_column", "release", "key_column"):
            if getattr(a, k):
                entry[k] = getattr(a, k)
        if a.assign_release:
            entry["release"] = "assign"
        m = {"data_start": a.data_start, "datasets": [entry]}
    if a.table:
        m["datasets"] = [d for d in m["datasets"] if d["table"] == a.table]
        if not m["datasets"]:
            sys.exit(f"Table '{a.table}' is not in the mapping.")
    return m


def cmd_upload(a, replace=False):
    path = a.db or a.csv
    if not path:
        sys.exit("Give --db file.db or --csv file.csv")
    m = _mapping_from_args(a)
    feed = a.feed or m.get("feed") or os.path.splitext(os.path.basename(path))[0]
    tz = m.get("timezone") or a.tz
    now = timeutil.now_us()
    if replace:
        for d in m["datasets"]:
            if not catalog.get_dataset(d["dataset"]):
                sys.exit(f"Dataset {d['dataset']} does not exist yet. Use 'upload' for a new dataset.")
    go_live = timeutil.parse_local(a.go_live, a.tz) if a.go_live else None
    try:
        f = feedops.ensure_feed(feed, m["data_start"], tz, go_live, a.speed, now, force=a.force)
    except feedops.FeedError as e:
        sys.exit(str(e))
    assign = None
    if a.assign_release:
        start = timeutil.parse_local(a.assign_release[0], a.tz)
        end = timeutil.parse_local(a.assign_release[1], a.tz) if len(a.assign_release) > 1 else None
        every = timeutil.parse_duration(a.every) if a.every else None
        if not end and not every:
            sys.exit("With --assign-release give START END, or START with --every 30s.")
        assign = (start, end, every, a.rows_per_step)
    if assign and len(m["datasets"]) > 1:
        sys.exit("--assign-release works on one table at a time. Add --table <name>.")
    src = loader.open_source(path, a.table if a.csv else None)
    for entry in m["datasets"]:
        if entry.get("timezone") and entry["timezone"] != f["tz"]:
            print(f"Note: {entry['dataset']} data times are in {entry['timezone']}.")
        summary = loader.upload_dataset(src, entry, f, assign=assign, activate=True if a.activate else None,
                                        keep_old=a.keep_old, workers=a.workers)
        log("replace" if replace else "upload", entry["dataset"], detail=json.dumps(summary))
    src.close()
    print()
    show_clock(feed)
    if not a.activate:
        names = " ".join(d["dataset"] for d in m["datasets"] if not catalog.get_dataset(d["dataset"]).get("active"))
        if names:
            print(f"\nThe datasets are not visible to students yet. When ready:\n  python admin.py activate --dataset {names}")


def cmd_list(a):
    now = timeutil.now_us()
    feeds = {f["feed"]: f for f in catalog.list_feeds()}
    rows = []
    for d in catalog.list_datasets():
        f = feeds.get(d["feed"])
        rel = catalog.released_count(d, f, now) if f else 0
        rows.append((d["dataset"], d["feed"], d["table"], "yes" if d.get("active") else "no", d["version"],
                     f"{d.get('row_count', 0):,}", f"{rel:,}", d.get("rule")))
    table(rows, ["dataset", "feed", "table", "active", "version", "rows", "released now", "rule"])


def cmd_activate(a, on=True):
    for name in a.dataset:
        try:
            feedops.set_active(name, on, timeutil.now_us())
            log("activate" if on else "deactivate", name)
        except feedops.FeedError as e:
            print(e)


def show_clock(feed):
    now = timeutil.now_us()
    r = feedops.feed_report(feed, now)
    print(f"Feed {r['feed']}  ({r['status']})")
    print(f"  data_start      {r['data_start']} ({r['timezone']})")
    print(f"  go-live         {r['go_live_local']} ({r['timezone']})  = {r['go_live']}")
    print(f"  speed           {r['speed']} data hours per real hour")
    if r["paused_at"]:
        print(f"  paused since    {r['paused_at']}")
    print(f"  data time now   {r['data_time_now'] or 'not started yet'}")
    table([(d["dataset"], "yes" if d["active"] else "no", d["version"], f"{d['rows_released']:,}",
            f"{d['rows_total']:,}") for d in r["datasets"]], ["dataset", "active", "version", "released", "total"])


def cmd_clock(a):
    feeds = [a.feed] if a.feed else [f["feed"] for f in catalog.list_feeds()]
    for f in feeds:
        try:
            show_clock(f)
        except feedops.FeedError as e:
            print(e)
        print()


def cmd_set_clock(a):
    f = catalog.get_feed(a.feed)
    if not f:
        sys.exit(f"There is no feed called '{a.feed}'.")
    tz = a.tz or f["tz"]
    go = timeutil.parse_local(a.go_live, tz) if a.go_live else clk.effective_go_live(f["clock"])
    speed = a.speed or f["clock"]["speed"]
    try:
        r = feedops.set_clock(a.feed, go, speed, timeutil.now_us(), force=a.force, from_now=a.from_now)
    except (feedops.FeedError, ValueError) as e:
        log("set-clock", a.feed, result="failed", detail=str(e))
        sys.exit(str(e))
    log("set-clock", a.feed, detail=json.dumps({"go_live": a.go_live, "speed": str(speed), "force": a.force,
                                                 "from_now": a.from_now, **r}))
    show_clock(a.feed)


def cmd_pause(a, resume=False):
    try:
        (feedops.resume if resume else feedops.pause)(a.feed, timeutil.now_us())
        log("resume" if resume else "pause", a.feed)
    except feedops.FeedError as e:
        sys.exit(str(e))
    show_clock(a.feed)



# ------------------------------------------------------------------ back-up and set-up commands

def cmd_outputs(a):
    for k, v in sorted(a.outputs.items()):
        print(f"{k:22} {v}")


def cmd_export(a):
    from skillops.core import export
    blob, manifest = export.build_zip()
    with open(a.out, "wb") as f:
        f.write(blob)
    print(f"Saved {a.out}: " + ", ".join(f"{k} {v}" for k, v in manifest["tables"].items()))


def cmd_restore(a):
    from skillops.core import export
    with open(a.file, "rb") as f:
        blob = f.read()
    print(f"Restoring {a.file}" + (" (overwriting)" if a.overwrite else ""))
    export.restore_zip(blob, overwrite=a.overwrite)
    republish_runbooks()
    sync_accounts()
    log("restore", a.file, detail="overwrite" if a.overwrite else "")
    print("Done. Paste each institution's MCQ secret again (MCQ tests page); secrets are never exported.")


def republish_runbooks():
    """Make the pages of every published runbook version again (their pages are not in an export)."""
    from skillops.core import jobs, store
    from skillops.runbook import service
    jobs.INLINE = True
    for rb in service.list_runbooks():
        for v in service.list_versions(rb["id"]):
            if v["status"] != "published":
                continue
            try:
                store.s3().head_object(Bucket=os.environ.get("RUNBOOK_BUCKET", ""), Key=f"runbooks/{v['id']}/tree.json")
                continue                      # its pages are there already
            except Exception:  # noqa: BLE001
                pass
            print(f"  publishing {rb['name']} v{v['number']} again ...")
            service._publish(v)


def sync_accounts():
    """Make sure every trainer record has a sign-in account (after a restore into a new stack)."""
    from skillops.core import org
    for t in org.list_trainers():
        if org.ensure_account(t):
            print(f"  sign-in account made for {t['email']} (a temporary password is emailed)")


def cmd_sync_accounts(a):
    sync_accounts()
    print("Done.")


def cmd_import_csv(a):
    from skillops.core import importer
    with open(a.file, encoding="utf-8-sig") as f:
        text = f.read()
    plan = importer.plan(text)
    for err in plan["errors"]:
        print("ERROR", err)
    if plan["errors"]:
        sys.exit("Nothing changed. Correct the lines above and run again.")
    for x in plan["actions"]:
        print(" -", x)
    if not plan["actions"]:
        print("Nothing needs changing.")
        return
    if not a.yes and input(f"Apply these {len(plan['actions'])} change(s)? Type yes: ").strip().lower() != "yes":
        print("Nothing changed.")
        return
    for line in importer.apply(text):
        print("   ", line)
    log("import csv", a.file)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--stack", default=os.environ.get("SKILLOPS_STACK", "skillops"), help="CloudFormation stack name")
    p.add_argument("--region", default=os.environ.get("SKILLOPS_REGION", "ap-south-1"))
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("outputs", help="Show the stack outputs")
    s.set_defaults(fn=cmd_outputs)
    s = sub.add_parser("export", help="Save everything to a .zip file on this laptop")
    s.add_argument("--out", required=True)
    s.set_defaults(fn=cmd_export)
    s = sub.add_parser("restore", help="Write an export back into the tables")
    s.add_argument("--file", required=True)
    s.add_argument("--overwrite", action="store_true", help="Replace items that already exist")
    s.set_defaults(fn=cmd_restore)
    s = sub.add_parser("sync-accounts", help="Make sign-in accounts for trainer records that have none (after a restore)")
    s.set_defaults(fn=cmd_sync_accounts)
    s = sub.add_parser("import-csv", help="Create institutions, programmes, batches, trainers from a CSV file")
    s.add_argument("--file", required=True)
    s.add_argument("--yes", action="store_true", help="Apply without asking")
    s.set_defaults(fn=cmd_import_csv)

    s = sub.add_parser("inspect", help="Show tables, columns, types and date ranges of a .db or .csv")
    s.add_argument("--db")
    s.add_argument("--csv")
    s.add_argument("--table")
    s.set_defaults(fn=cmd_inspect, offline=True)

    for name in ("upload", "replace"):
        s = sub.add_parser(name, help=("Load" if name == "upload" else "Replace") + " datasets from a .db or .csv")
        s.add_argument("--db")
        s.add_argument("--csv")
        s.add_argument("--mapping", help="datasets.json")
        s.add_argument("--table", help="Only this table")
        s.add_argument("--feed", help="Feed name (default: mapping 'feed' or the file name)")
        s.add_argument("--go-live", help='Real time at which data_start is released, e.g. "2026-11-16 09:00"')
        s.add_argument("--tz", default="Asia/Kolkata", help="Time zone of --go-live and the data (default Asia/Kolkata)")
        s.add_argument("--speed", help="Data hours per real hour (default 1)")
        s.add_argument("--force", action="store_true", help="Allow a clock change that resets students")
        s.add_argument("--activate", action="store_true", help="Make the datasets visible to students now")
        s.add_argument("--keep-old", action="store_true", help="Do not delete the rows of the previous upload")
        s.add_argument("--workers", type=int, default=8, help="Parallel writers (default 8)")
        s.add_argument("--assign-release", nargs="+", metavar="TIME",
                       help='For tables without a time column: START END, or START with --every')
        s.add_argument("--every", help="With --assign-release START: release a step every 30s / 5m / 1h")
        s.add_argument("--rows-per-step", type=int, default=1, help="Rows released at each --every step")
        s.add_argument("--dataset", help="Without --mapping: dataset name")
        s.add_argument("--data-start", help="Without --mapping: data_start, e.g. 2025-07-01")
        s.add_argument("--time-column")
        s.add_argument("--date-column")
        s.add_argument("--hour-column")
        s.add_argument("--release", choices=["end_of_hour", "start_of_day", "end_of_day", "static"])
        s.add_argument("--key-column")
        s.set_defaults(fn=(lambda a: cmd_upload(a, replace=True)) if name == "replace" else cmd_upload)

    s = sub.add_parser("list-datasets", help="All datasets with rows and rows released now")
    s.set_defaults(fn=cmd_list)
    for name, on in (("activate", True), ("deactivate", False)):
        s = sub.add_parser(name, help=f"{name.title()} datasets")
        s.add_argument("--dataset", nargs="+", required=True)
        s.set_defaults(fn=lambda a, on=on: cmd_activate(a, on))

    s = sub.add_parser("clock", help="Show go-live, speed, current data time and rows released")
    s.add_argument("--feed")
    s.set_defaults(fn=cmd_clock)
    s = sub.add_parser("set-clock", help="Change go-live and/or speed of a feed")
    s.add_argument("--feed", required=True)
    s.add_argument("--go-live")
    s.add_argument("--speed")
    s.add_argument("--tz")
    s.add_argument("--force", action="store_true", help="Allow changes that reset students (bumps dataset_version)")
    s.add_argument("--from-now", action="store_true", help="Change only the speed, from this moment (no reset)")
    s.set_defaults(fn=cmd_set_clock)
    for name in ("pause", "resume"):
        s = sub.add_parser(name, help=f"{name.title()} a feed")
        s.add_argument("--feed", required=True)
        s.set_defaults(fn=lambda a, r=(name == "resume"): cmd_pause(a, r))

    a = p.parse_args(argv)
    a.outputs = {}
    if not getattr(a, "offline", False):
        a.outputs = connect(a.stack, a.region) or {}
    a.fn(a)


if __name__ == "__main__":
    main()
