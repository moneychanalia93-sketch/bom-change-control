"""Data-quality checks for a BOM.

Each check takes a `Bom` and returns a list of `Finding`s (empty = pass).
`run_checks` runs all of them; `run_and_record` also stores the run so the
report can show recurring issues and a trend over time.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from bomcc.bom import Bom, fmt_qty
from bomcc.db import load_bom, to_iso

ERROR, WARNING, INFO = "error", "warning", "info"


@dataclass(frozen=True)
class Finding:
    """One data-quality problem. `part_number` is the part that must be fixed."""

    check: str
    severity: str
    part_number: str
    message: str
    owner: str


def _owner(bom: Bom, part_number: str) -> str:
    part = bom.part(part_number)
    return (part.owner if part and part.owner else "unassigned")


def check_missing_children(bom: Bom) -> list[Finding]:
    """A BOM line points at a part number that does not exist."""
    out = []
    for line in bom.lines:
        if bom.part(line.child) is None:
            out.append(Finding("missing_children", ERROR, line.parent,
                               f"{line.parent} lists child {line.child}, which is not a known part",
                               _owner(bom, line.parent)))
        if bom.part(line.parent) is None:
            out.append(Finding("missing_children", ERROR, line.parent,
                               f"BOM line belongs to {line.parent}, which is not a known part",
                               "unassigned"))
    return out


def check_duplicate_part_numbers(bom: Bom) -> list[Finding]:
    """The same part number is defined more than once."""
    counts = Counter(p.part_number for p in bom.parts)
    return [Finding("duplicate_part_numbers", ERROR, pn,
                    f"part number {pn} is defined {n} times", _owner(bom, pn))
            for pn, n in counts.items() if n > 1]


def check_bad_quantities(bom: Bom) -> list[Finding]:
    """A BOM line has a quantity of zero or less."""
    return [Finding("bad_quantities", ERROR, line.parent,
                    f"{line.parent} uses {fmt_qty(line.quantity)} {line.unit} of {line.child}; "
                    "quantity must be greater than zero", _owner(bom, line.parent))
            for line in bom.lines if not line.quantity > 0]


def check_released_with_draft_child(bom: Bom) -> list[Finding]:
    """A Released assembly contains a Draft part (a release-control error)."""
    out = []
    for line in bom.lines:
        parent, child = bom.part(line.parent), bom.part(line.child)
        if parent and child and parent.status == "Released" and child.status == "Draft":
            out.append(Finding("released_with_draft_child", ERROR, parent.part_number,
                               f"Released assembly {parent.part_number} contains Draft part "
                               f"{child.part_number}", parent.owner or "unassigned"))
    return out


def check_orphaned_parts(bom: Bom) -> list[Finding]:
    """A part is not used by any assembly and has no children of its own (info only)."""
    used = {line.child for line in bom.lines}
    out, seen = [], set()
    for part in bom.parts:
        if part.part_number in seen:
            continue
        seen.add(part.part_number)
        if part.part_number not in used and part.part_number not in bom.children:
            out.append(Finding("orphaned_parts", INFO, part.part_number,
                               f"{part.part_number} is not used by any assembly",
                               part.owner or "unassigned"))
    return out


def check_circular_structures(bom: Bom) -> list[Finding]:
    """An assembly contains itself, directly or through other assemblies."""
    out: list[Finding] = []
    seen_cycles: set[frozenset[str]] = set()
    state: dict[str, int] = {}  # 1 = on the current path, 2 = fully explored

    def visit(part_number: str, stack: list[str]) -> None:
        state[part_number] = 1
        stack.append(part_number)
        for line in bom.children.get(part_number, []):
            if state.get(line.child) == 1:
                cycle = stack[stack.index(line.child):]
                key = frozenset(cycle)
                if key not in seen_cycles:
                    seen_cycles.add(key)
                    start = cycle.index(min(cycle))
                    cycle = cycle[start:] + cycle[:start]
                    out.append(Finding("circular_structures", ERROR, cycle[0],
                                       "circular structure: " + " -> ".join(cycle + [cycle[0]]),
                                       _owner(bom, cycle[0])))
            elif line.child not in state:
                visit(line.child, stack)
        stack.pop()
        state[part_number] = 2

    for part_number in list(bom.children):
        if part_number not in state:
            visit(part_number, [])
    return out


def check_inconsistent_units(bom: Bom) -> list[Finding]:
    """A BOM line's unit differs from the child part's own unit of measure."""
    out = []
    for line in bom.lines:
        child = bom.part(line.child)
        if child and child.uom != line.unit:
            out.append(Finding("inconsistent_units", WARNING, line.parent,
                               f"{line.parent} uses {line.child} in {line.unit}, "
                               f"but the part is defined in {child.uom}",
                               _owner(bom, line.parent)))
    return out


CHECKS: dict[str, Callable[[Bom], list[Finding]]] = {
    "missing_children": check_missing_children,
    "duplicate_part_numbers": check_duplicate_part_numbers,
    "bad_quantities": check_bad_quantities,
    "released_with_draft_child": check_released_with_draft_child,
    "orphaned_parts": check_orphaned_parts,
    "circular_structures": check_circular_structures,
    "inconsistent_units": check_inconsistent_units,
}


def run_checks(bom: Bom) -> list[Finding]:
    """Run every check and return all findings, worst severity first."""
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    found = [f for check in CHECKS.values() for f in check(bom)]
    return sorted(found, key=lambda f: (order[f.severity], f.check, f.part_number))


def run_and_record(conn: sqlite3.Connection, now: datetime | None = None) -> list[Finding]:
    """Validate the stored BOM and save the run (counts and findings) for reporting."""
    bom = load_bom(conn)
    found = run_checks(bom)
    counts = Counter(f.severity for f in found)
    with conn:
        run_id = conn.execute(
            "INSERT INTO validation_runs (ts, parts_count, errors, warnings, infos) "
            "VALUES (?, ?, ?, ?, ?)",
            (to_iso(now), len(bom.parts), counts[ERROR], counts[WARNING], counts[INFO]),
        ).lastrowid
        conn.executemany(
            "INSERT INTO findings (run_id, check_name, severity, part_number, owner, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, f.check, f.severity, f.part_number, f.owner, f.message) for f in found])
    return found
