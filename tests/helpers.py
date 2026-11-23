"""Small builders shared by the tests."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone

from bomcc.bom import Bom, BomLine, Part
from bomcc.db import connect


def P(pn: str, status: str = "Released", rev: str = "A", uom: str = "EA", owner: str = "owner1") -> Part:
    return Part(pn, f"desc {pn}", rev, status, uom, owner)


def L(parent: str, child: str, qty: float = 1, unit: str = "EA", rev: str = "A") -> BomLine:
    return BomLine(parent, child, qty, unit, rev)


def T(day: int, hour: int = 9) -> datetime:
    """A fixed UTC moment in March 2026 (2026-03-02 is a Monday)."""
    return datetime(2026, 3, day, hour, tzinfo=timezone.utc)


def memory_db() -> sqlite3.Connection:
    """An empty in-memory database, closed when the current test module finishes."""
    conn = connect(":memory:")
    unittest.addModuleCleanup(conn.close)
    return conn


def store(parts: list[Part], lines: list[BomLine]) -> sqlite3.Connection:
    """An in-memory database holding the given parts and lines."""
    conn = memory_db()
    with conn:
        conn.executemany(
            "INSERT INTO parts (part_number, description, revision, status, uom, owner) VALUES (?,?,?,?,?,?)",
            [(p.part_number, p.description, p.revision, p.status, p.uom, p.owner) for p in parts])
        conn.executemany(
            "INSERT INTO bom_lines (parent, child, quantity, unit, child_rev) VALUES (?,?,?,?,?)",
            [(x.parent, x.child, x.quantity, x.unit, x.child_rev) for x in lines])
    return conn


def small_parts() -> list[Part]:
    """TOP -> SUB1 x2, SUB2 x1, SCREW x4;  SUB1 -> SCREW x3, PLATE;  SUB2 -> SCREW x2, WIRE 1.5 M."""
    return [P("TOP"), P("SUB1"), P("SUB2"), P("SCREW"), P("PLATE"), P("WIRE", uom="M")]


def small_lines() -> list[BomLine]:
    return [L("TOP", "SUB1", 2), L("TOP", "SUB2", 1), L("TOP", "SCREW", 4),
            L("SUB1", "SCREW", 3), L("SUB1", "PLATE", 1),
            L("SUB2", "SCREW", 2), L("SUB2", "WIRE", 1.5, "M")]


def small_bom() -> Bom:
    return Bom(small_parts(), small_lines())


def small_store() -> sqlite3.Connection:
    return store(small_parts(), small_lines())
