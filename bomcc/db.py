"""SQLite storage: schema, connection, and conversion to the in-memory `Bom`."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from bomcc.bom import Bom, BomLine, Part

# part_number is deliberately NOT unique: the validator must be able to see and
# report duplicates, so the database cannot refuse them on load.
SCHEMA = """
CREATE TABLE IF NOT EXISTS parts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_number TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    revision TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('Draft', 'Released', 'Obsolete')),
    uom TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS bom_lines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent TEXT NOT NULL,
    child TEXT NOT NULL,
    quantity REAL NOT NULL,
    unit TEXT NOT NULL,
    child_rev TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ecr (
    ecr_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    assembly TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('add', 'remove', 'change_qty', 'change_rev')),
    child TEXT NOT NULL,
    new_qty REAL,
    new_unit TEXT NOT NULL DEFAULT '',
    new_rev TEXT NOT NULL DEFAULT '',
    requester TEXT NOT NULL,
    approvers TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL CHECK (state IN ('Draft', 'Submitted', 'Approved', 'Released', 'Rejected')),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS change_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ecr_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS change_history_no_update
BEFORE UPDATE ON change_history
BEGIN SELECT RAISE(ABORT, 'change_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS change_history_no_delete
BEFORE DELETE ON change_history
BEGIN SELECT RAISE(ABORT, 'change_history is append-only'); END;
CREATE TABLE IF NOT EXISTS validation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    parts_count INTEGER NOT NULL,
    errors INTEGER NOT NULL,
    warnings INTEGER NOT NULL,
    infos INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES validation_runs(id),
    check_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    part_number TEXT NOT NULL,
    owner TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (and create if needed) the database at `path`; ':memory:' works too."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def to_iso(now: datetime | None = None) -> str:
    """UTC timestamp as an ISO string. Pass `now` to control the clock in tests."""
    moment = now or datetime.now(timezone.utc)
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def load_bom(conn: sqlite3.Connection) -> Bom:
    """Read every part and BOM line into a `Bom`."""
    parts = [
        Part(r["part_number"], r["description"], r["revision"], r["status"], r["uom"], r["owner"])
        for r in conn.execute("SELECT * FROM parts ORDER BY id")
    ]
    lines = [
        BomLine(r["parent"], r["child"], r["quantity"], r["unit"], r["child_rev"])
        for r in conn.execute("SELECT * FROM bom_lines ORDER BY id")
    ]
    return Bom(parts, lines)
