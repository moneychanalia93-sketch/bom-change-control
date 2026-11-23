"""Load a BOM from CSV into SQLite.

One CSV holds both kinds of record, told apart by the `record` column:

    PART rows: part_number, description, revision, status, uom, owner
    BOM  rows: part_number (the parent), child_part, quantity, unit, child_rev

Zero or negative quantities and unknown children are loaded as they are, on
purpose: finding them is the validator's job, not the loader's.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from bomcc.bom import STATUSES

COLUMNS = (
    "record", "part_number", "description", "revision", "status",
    "uom", "owner", "child_part", "quantity", "unit", "child_rev",
)


class LoadError(ValueError):
    """The CSV file is malformed (wrong columns, bad number, unknown status...)."""


def _need(row: dict[str, str], names: tuple[str, ...], line_no: int) -> None:
    for name in names:
        if not (row.get(name) or "").strip():
            raise LoadError(f"line {line_no}: '{name}' is required for a {row['record']} row")


def load_csv(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int]:
    """Replace the stored BOM with the contents of `path`. Returns (parts, lines)."""
    parts: list[tuple[str, ...]] = []
    lines: list[tuple[object, ...]] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise LoadError(f"missing column(s): {', '.join(missing)}")
        for line_no, raw in enumerate(reader, start=2):
            row = {k: (v or "").strip() for k, v in raw.items() if k}
            row["record"] = row["record"].upper()
            if row["record"] == "PART":
                _need(row, ("part_number", "revision", "status", "uom"), line_no)
                status = row["status"].capitalize()
                if status not in STATUSES:
                    raise LoadError(f"line {line_no}: status must be one of {', '.join(STATUSES)}")
                parts.append((row["part_number"], row["description"], row["revision"],
                              status, row["uom"].upper(), row["owner"]))
            elif row["record"] == "BOM":
                _need(row, ("part_number", "child_part", "quantity", "unit"), line_no)
                try:
                    quantity = float(row["quantity"])
                except ValueError:
                    raise LoadError(f"line {line_no}: quantity {row['quantity']!r} is not a number") from None
                lines.append((row["part_number"], row["child_part"], quantity,
                              row["unit"].upper(), row["child_rev"]))
            else:
                raise LoadError(f"line {line_no}: record must be PART or BOM, got {row['record']!r}")
    with conn:
        conn.execute("DELETE FROM parts")
        conn.execute("DELETE FROM bom_lines")
        conn.executemany(
            "INSERT INTO parts (part_number, description, revision, status, uom, owner) "
            "VALUES (?, ?, ?, ?, ?, ?)", parts)
        conn.executemany(
            "INSERT INTO bom_lines (parent, child, quantity, unit, child_rev) "
            "VALUES (?, ?, ?, ?, ?)", lines)
    return len(parts), len(lines)
