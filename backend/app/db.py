"""SQLite（WAL）存取。每次呼叫開一條短連線，避免跨執行緒共用。"""

import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("AI_MOVIE_DATA", Path(__file__).resolve().parents[2] / "data"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    target_s    REAL NOT NULL,
    budget_cap  REAL NOT NULL,
    settings    TEXT NOT NULL DEFAULT '{}',
    gates       TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS shots (
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    shot_id     TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    spec        TEXT NOT NULL,
    PRIMARY KEY (project_id, shot_id)
);
CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    amount_nt   REAL NOT NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    status      TEXT NOT NULL,
    progress    REAL NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',
    verdict     TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db_path() -> Path:
    return DATA_DIR / "app.db"


@contextmanager
def connect():
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def row_to_dict(row: sqlite3.Row, json_fields: tuple[str, ...] = ()) -> dict:
    d = dict(row)
    for f in json_fields:
        if d.get(f) is not None:
            d[f] = json.loads(d[f])
    return d
