"""Trainer example (not the student's dashboard): a minimal Streamlit page with a "Load live data" button.

Run (Windows Command Prompt), from the folder that has your .env and database:
    streamlit run live_page_example.py

What the button does:
1. calls live_client.load_live() to fetch new rows into staging_<table>,
2. runs every .sql file in the SQL folder, in name order (01_..., 02_...),
3. shows rows added, the load_log, and one chart.
"""
import argparse
import glob
import os
import sqlite3
import sys

import pandas as pd
import streamlit as st

import live_client  # the reference client in the same folder

st.set_page_config(page_title="Live data (trainer example)", layout="wide")
st.title("Live data - trainer example")

# Defaults can be given after "--", e.g.
#   streamlit run live_page_example.py -- --db practice.db --dataset practice_orders --table orders
_args = argparse.ArgumentParser()
for _name, _default in (("db", "uber.db"), ("dataset", "uber_trips_live"), ("table", "trips"),
                        ("sql", "sql"), ("env", ".env")):
    _args.add_argument("--" + _name, default=_default)
defaults = _args.parse_known_args(sys.argv[1:])[0]

with st.sidebar:
    st.header("Settings")
    db_path = st.text_input("SQLite database", defaults.db)
    dataset = st.text_input("Dataset", defaults.dataset)
    table = st.text_input("Your table", defaults.table)
    sql_dir = st.text_input("Folder of .sql files", defaults.sql)
    env_path = st.text_input(".env file", defaults.env)


def run_sql_files(path, folder):
    files = sorted(glob.glob(os.path.join(folder, "*.sql")))
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        for f in files:
            with open(f, encoding="utf-8") as fh:
                conn.executescript(fh.read())
    finally:
        conn.close()
    return files


def read(path, sql, params=()):
    conn = sqlite3.connect(path, timeout=30)
    try:
        return pd.read_sql_query(sql, conn, params=params)
    finally:
        conn.close()


if st.button("Load live data", type="primary"):
    with st.spinner("Fetching new rows..."):
        try:
            result = live_client.load_live(db_path, dataset, table, env_path, verbose=False)
            st.success(f"{result['rows']} new rows in {result['pages']} page(s). Data up to {result['until']}.")
            files = run_sql_files(db_path, sql_dir)
            if files:
                st.info("Ran SQL files: " + ", ".join(os.path.basename(f) for f in files))
        except live_client.StopLoad as e:
            st.error(f"Stopped: {e}")
        except Exception as e:  # noqa: BLE001
            st.error(f"Load failed: {e}. Nothing was saved; the next load starts from the same point.")

if os.path.exists(db_path):
    try:
        log = read(db_path, "SELECT * FROM load_log WHERE dataset = ? ORDER BY fetched_at DESC", (dataset,))
    except Exception:  # load_log does not exist before the first load
        log = pd.DataFrame()
    st.subheader("load_log")
    st.dataframe(log, use_container_width=True)
    ok = log[log["status"] == "ok"] if not log.empty else log
    if not ok.empty:
        st.subheader("Rows added per load")
        chart = ok.sort_values("fetched_at").set_index("fetched_at")["rows"]
        st.bar_chart(chart)
else:
    st.warning(f"{db_path} not found. Check the path in the sidebar.")
