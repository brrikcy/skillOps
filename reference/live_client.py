# Reference client for TRAINERS. Students write their own; do not hand this out before they have tried.
# It fetches new rows of one dataset and stores them in staging_<table> in a SQLite file.
# Usage:  python live_client.py --db uber.db --dataset uber_trips_live --table trips
import argparse  # reads command-line options
import os  # file paths
import sqlite3  # the student's database
import sys  # exit codes
import time  # waiting after a 429
from datetime import datetime, timezone  # time stamps for load_log

import requests  # the only non-standard package we use

LOAD_LOG_SQL = """CREATE TABLE IF NOT EXISTS load_log (
    dataset TEXT, since TEXT, until TEXT, rows INTEGER, pages INTEGER,
    status TEXT, dataset_version INTEGER, fetched_at TEXT, message TEXT)"""  # one row per load attempt


class StopLoad(Exception):  # an error we explain to the user and do not retry
    pass  # no extra behaviour


def read_env(path=".env"):  # a tiny .env reader, so we need no extra package
    values = {}  # collected settings
    if os.path.exists(path):  # .env is optional if the variables are already set
        with open(path, encoding="utf-8") as f:  # open the file
            for line in f:  # one setting per line
                line = line.strip()  # drop spaces and the newline
                if line and not line.startswith("#") and "=" in line:  # skip blanks and comments
                    k, v = line.split("=", 1)  # split at the first =
                    values[k.strip()] = v.strip().strip('"').strip("'")  # remove quotes around the value
    key = os.environ.get("SIMLAB_API_KEY") or values.get("SIMLAB_API_KEY")  # environment wins over .env
    url = os.environ.get("SIMLAB_API_URL") or values.get("SIMLAB_API_URL")  # same for the URL
    if not key or not url:  # both are required
        raise StopLoad("SIMLAB_API_KEY and SIMLAB_API_URL must be in .env")  # clear message
    return key, url.rstrip("/")  # no trailing slash, so we can add paths


def call(url, key, path, params=None):  # one GET request with friendly error handling
    for attempt in range(4):  # try up to 4 times if rate limited
        r = requests.get(url + path, headers={"x-api-key": key}, params=params, timeout=60)  # the HTTP call
        if r.status_code == 429:  # too many requests
            wait = r.json().get("error", {}).get("retry_after_seconds", 10)  # the server says how long to wait
            print(f"Rate limited. Waiting {wait} s...")  # tell the user
            time.sleep(wait)  # wait, then try again
            continue  # next attempt
        body = r.json()  # every response is JSON
        if r.status_code != 200:  # any other error
            err = body.get("error", {})  # the standard error shape
            raise StopLoad(f"{r.status_code} {err.get('code')}: {err.get('message')} {err.get('hint', '')}")  # explain
        return body  # success
    raise StopLoad("Still rate limited after 4 tries. Try again later.")  # give up politely


def connect(db_path):  # open the database so Streamlit can read while we write
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)  # we manage transactions ourselves
    conn.execute("PRAGMA journal_mode=WAL")  # readers do not block the writer
    conn.execute("PRAGMA busy_timeout=30000")  # wait up to 30 s if the database is busy
    conn.execute(LOAD_LOG_SQL)  # make sure load_log exists
    return conn  # ready to use


def last_ok(conn, dataset):  # the last successful load of this dataset
    return conn.execute(  # query load_log
        "SELECT until, dataset_version FROM load_log WHERE dataset=? AND status='ok' "
        "ORDER BY fetched_at DESC, rowid DESC LIMIT 1", (dataset,)).fetchone()  # newest 'ok' row, or None


def table_columns(conn, table):  # column names of a table, in order
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]  # second field is the name


def write_log(conn, dataset, since, until, rows, pages, status, version, message=""):  # add one load_log row
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")  # when this happened (UTC)
    conn.execute("INSERT INTO load_log VALUES (?,?,?,?,?,?,?,?,?)",  # nine columns
                 (dataset, since, until, rows, pages, status, version, now, message))  # the values


def reset(db_path, dataset, table):  # start again after dataset_replaced
    conn = connect(db_path)  # open the database
    conn.execute("BEGIN IMMEDIATE")  # one transaction
    if table_columns(conn, f"staging_{table}"):  # the staging table exists
        conn.execute(f'DELETE FROM "staging_{table}"')  # empty it
    conn.execute("DELETE FROM load_log WHERE dataset=?", (dataset,))  # forget earlier loads
    conn.execute("COMMIT")  # save
    conn.close()  # done


def load_live(db_path, dataset, table, env_path=".env", verbose=True):  # the whole job; returns a summary dict
    say = print if verbose else (lambda *a: None)  # optional printing
    key, url = read_env(env_path)  # step 1: settings from .env
    me = call(url, key, "/v1/whoami")["student"]  # step 2: who am I?
    say(f"Hello {me['name']} ({me['roll_number']}, batch {me['batch']}): {me['message']}")  # greet
    if me["status"] != "active":  # pending or disabled students cannot fetch
        raise StopLoad(me["message"])  # stop with the server's message
    conn = connect(db_path)  # open the student's database
    existing = table_columns(conn, table)  # columns of the student's own table
    if not existing:  # the table must exist (it comes from the project)
        raise StopLoad(f"Table '{table}' is not in {db_path}.")  # explain
    prev = last_ok(conn, dataset)  # step 3: where did we stop last time?
    since = prev[0] if prev else ""  # empty since = from the beginning
    say(f"Fetching {dataset} since {since or 'the beginning'}...")  # progress
    rows, pages, version, until = [], 0, None, None  # collected results
    params = {"dataset": dataset, "since": since}  # first page parameters
    try:  # any failure below is logged as 'failed'
        while True:  # step 4: fetch every page
            body = call(url, key, "/v1/fetch", params)  # one page
            pages += 1  # count pages
            if body["columns"] != existing:  # step 6: columns must match the student's table
                raise StopLoad(f"Columns differ. Feed: {body['columns']}. Your table: {existing}.")  # stop
            if prev and prev[1] is not None and body["dataset_version"] != prev[1]:  # step 6: version changed
                raise StopLoad("dataset_version changed. Run with --reset, then load again.")  # stop
            version, until = body["dataset_version"], body["until"]  # same until on every page
            rows.extend(body["rows"])  # keep the rows
            if not body["next_page_token"]:  # no more pages
                break  # done fetching
            params = {"dataset": dataset, "page_token": body["next_page_token"]}  # ask for the next page
        staging = f"staging_{table}"  # where new rows go
        conn.execute("BEGIN IMMEDIATE")  # step 5: one transaction for rows + log
        conn.execute(f'CREATE TABLE IF NOT EXISTS "{staging}" AS SELECT * FROM "{table}" WHERE 0')  # same columns
        cols = ", ".join(f'"{c}"' for c in existing)  # quoted column list
        marks = ", ".join("?" * len(existing))  # one ? per column
        conn.executemany(f'INSERT INTO "{staging}" ({cols}) VALUES ({marks})', rows)  # insert all rows
        write_log(conn, dataset, since, until, len(rows), pages, "ok", version)  # the success row
        conn.execute("COMMIT")  # rows and log are saved together
        say(f"Loaded {len(rows)} new rows in {pages} page(s). Next since = {until}")  # report
        return {"status": "ok", "rows": len(rows), "pages": pages, "since": since, "until": until,
                "dataset_version": version}  # summary
    except Exception as e:  # anything went wrong
        if conn.in_transaction:  # we were half-way through writing
            conn.execute("ROLLBACK")  # undo everything; since does not move
        write_log(conn, dataset, since, until, 0, pages, "failed", version, str(e)[:500])  # record the failure
        raise  # let the caller show the error
    finally:  # always
        conn.close()  # close the database


def main():  # command-line entry point
    p = argparse.ArgumentParser(description="Load live rows into staging_<table>.")  # options
    p.add_argument("--db", required=True)  # the SQLite file
    p.add_argument("--dataset", required=True)  # e.g. uber_trips_live
    p.add_argument("--table", required=True)  # e.g. trips
    p.add_argument("--env", default=".env")  # where the key is
    p.add_argument("--reset", action="store_true")  # start again from the beginning
    a = p.parse_args()  # read them
    try:  # friendly errors
        if a.reset:  # the user asked to start again
            reset(a.db, a.dataset, a.table)  # clear staging rows and load_log
            print("Reset done. The next load starts from the beginning.")  # confirm
            return 0  # success
        load_live(a.db, a.dataset, a.table, a.env)  # do the work
        return 0  # success
    except StopLoad as e:  # an error we understand
        print(f"Stopped: {e}")  # explain
        return 2  # non-zero exit code
    except requests.RequestException as e:  # network problem
        print(f"Network problem: {e}")  # explain
        return 3  # non-zero exit code


if __name__ == "__main__":  # run only when started directly
    sys.exit(main())  # pass the exit code to the shell
