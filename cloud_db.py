"""SQLite locally, PostgreSQL on Streamlit Cloud when DATABASE_URL is set."""

import os
import sqlite3
from pathlib import Path

DB_PATH = Path(
    os.environ.get(
        "NBA_ANALYTICS_DB_PATH",
        str(Path(__file__).with_name("nba_analytics.sqlite3"))
    )
)


def database_url():
    url = os.environ.get("DATABASE_URL", "").strip()

    if not url:
        try:
            import streamlit as st
            url = str(st.secrets.get("DATABASE_URL", "")).strip()
        except Exception:
            pass

    return url


class PGConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        # Convert SQLite placeholders to PostgreSQL placeholders.
        sql = sql.replace("?", "%s")
        return self.conn.execute(sql, params or ())

    def cursor(self):
        from psycopg.rows import tuple_row
        return self.conn.cursor(row_factory=tuple_row)

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *args):
        return self.conn.__exit__(*args)

    def close(self):
        self.conn.close()


def connect():
    url = database_url()

    if url:
        import psycopg
        return PGConnection(
            psycopg.connect(url, connect_timeout=15)
        )

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH)


def label():
    return (
        "Cloud PostgreSQL"
        if database_url()
        else DB_PATH.name
    )

