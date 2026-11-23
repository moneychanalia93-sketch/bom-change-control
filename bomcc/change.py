"""Engineering change requests (ECRs): the change-management part.

An ECR proposes one edit to one assembly's BOM. It moves through a state
machine with enforced transitions:

    Draft -> Submitted -> Approved -> Released
                 |            |
                 +--> Rejected <-+      (Rejected -> Draft is "reopen", for rework)

Rules enforced here, not left to the caller:
  * only listed transitions are allowed (`TRANSITIONS`);
  * the requester can never approve (or be listed as an approver of) their own change;
  * every listed approver must approve before the ECR becomes Approved;
  * every state change and approval is written to an append-only history table;
  * Release applies the edit to the BOM and bumps the assembly's revision.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from bomcc.bom import Affected, Bom, bump_revision
from bomcc.db import load_bom, to_iso

ACTIONS = ("add", "remove", "change_qty", "change_rev")
STATES = ("Draft", "Submitted", "Approved", "Released", "Rejected")
TRANSITIONS: dict[str, set[str]] = {
    "Draft": {"Submitted"},
    "Submitted": {"Approved", "Rejected"},
    "Approved": {"Released", "Rejected"},
    "Rejected": {"Draft"},
    "Released": set(),
}
OPEN_STATES = ("Draft", "Submitted", "Approved")


class ChangeError(Exception):
    """A change request or action breaks a rule."""


class InvalidTransition(ChangeError):
    """The requested state change is not allowed from the current state."""


@dataclass(frozen=True)
class Ecr:
    ecr_id: str
    title: str
    category: str
    assembly: str
    action: str
    child: str
    new_qty: float | None
    new_unit: str
    new_rev: str
    requester: str
    approvers: tuple[str, ...]
    reason: str
    state: str
    created_at: str


@dataclass(frozen=True)
class HistoryEvent:
    ts: str
    actor: str
    action: str
    from_state: str
    to_state: str
    note: str


def _to_ecr(row: sqlite3.Row) -> Ecr:
    return Ecr(row["ecr_id"], row["title"], row["category"], row["assembly"], row["action"],
               row["child"], row["new_qty"], row["new_unit"], row["new_rev"], row["requester"],
               tuple(a for a in row["approvers"].split(",") if a), row["reason"],
               row["state"], row["created_at"])


def get_ecr(conn: sqlite3.Connection, ecr_id: str) -> Ecr:
    row = conn.execute("SELECT * FROM ecr WHERE ecr_id = ?", (ecr_id,)).fetchone()
    if row is None:
        raise ChangeError(f"no such change request: {ecr_id}")
    return _to_ecr(row)


def list_ecrs(conn: sqlite3.Connection) -> list[Ecr]:
    return [_to_ecr(r) for r in conn.execute("SELECT * FROM ecr ORDER BY ecr_id")]


def history(conn: sqlite3.Connection, ecr_id: str) -> list[HistoryEvent]:
    rows = conn.execute("SELECT * FROM change_history WHERE ecr_id = ? ORDER BY id", (ecr_id,))
    return [HistoryEvent(r["ts"], r["actor"], r["action"], r["from_state"], r["to_state"], r["note"])
            for r in rows]


def _log(conn: sqlite3.Connection, ecr_id: str, actor: str, action: str,
         from_state: str, to_state: str, note: str, now: datetime | None) -> None:
    conn.execute(
        "INSERT INTO change_history (ecr_id, ts, actor, action, from_state, to_state, note) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ecr_id, to_iso(now), actor, action, from_state, to_state, note))


def _require_transition(ecr: Ecr, to_state: str) -> None:
    if to_state not in TRANSITIONS[ecr.state]:
        raise InvalidTransition(
            f"{ecr.ecr_id} is {ecr.state}; it cannot move to {to_state} "
            f"(allowed from {ecr.state}: {', '.join(sorted(TRANSITIONS[ecr.state])) or 'none'})")


def _move(conn: sqlite3.Connection, ecr: Ecr, to_state: str, actor: str, action: str,
          note: str, now: datetime | None) -> None:
    """The only place an ECR's state changes. Checks the rule, then writes state + history."""
    _require_transition(ecr, to_state)
    conn.execute("UPDATE ecr SET state = ? WHERE ecr_id = ?", (to_state, ecr.ecr_id))
    _log(conn, ecr.ecr_id, actor, action, ecr.state, to_state, note, now)


def _check_applicable(bom: Bom, action: str, assembly: str, child: str,
                      new_qty: float | None, new_rev: str) -> None:
    """Raise ChangeError unless the edit could be applied to `bom` right now."""
    parent = bom.part(assembly)
    if parent is None:
        raise ChangeError(f"assembly {assembly} not found")
    if parent.status == "Obsolete":
        raise ChangeError(f"{assembly} is Obsolete and cannot be changed")
    line = bom.line(assembly, child)
    if action == "add":
        kid = bom.part(child)
        if line is not None:
            raise ChangeError(f"{assembly} already contains {child}")
        if kid is None:
            raise ChangeError(f"part {child} not found")
        if new_qty is None or not new_qty > 0:
            raise ChangeError("adding a child needs a quantity greater than zero")
        if parent.status == "Released" and kid.status == "Draft":
            raise ChangeError(f"release control: cannot add Draft part {child} to Released assembly {assembly}")
        if child == assembly or assembly in bom.descendants(child):
            raise ChangeError(f"adding {child} to {assembly} would create a circular structure")
        return
    if line is None:
        raise ChangeError(f"{assembly} does not contain {child}")
    if action == "change_qty" and (new_qty is None or not new_qty > 0):
        raise ChangeError("change_qty needs a quantity greater than zero")
    if action == "change_rev" and not new_rev:
        raise ChangeError("change_rev needs a revision")


def create_ecr(conn: sqlite3.Connection, *, title: str, category: str, assembly: str,
               action: str, child: str, requester: str, approvers: list[str],
               new_qty: float | None = None, new_unit: str = "", new_rev: str = "",
               reason: str = "", now: datetime | None = None) -> Ecr:
    """Create an ECR in state Draft after checking the proposed edit makes sense."""
    approvers = [a.strip() for a in approvers if a.strip()]
    if not title.strip() or not category.strip() or not requester.strip():
        raise ChangeError("title, category and requester are required")
    if action not in ACTIONS:
        raise ChangeError(f"action must be one of {', '.join(ACTIONS)}")
    if not approvers:
        raise ChangeError("at least one approver is required")
    if len(set(approvers)) != len(approvers):
        raise ChangeError("approver list contains the same person twice")
    if requester in approvers:
        raise ChangeError("the requester cannot be on the approver list")
    _check_applicable(load_bom(conn), action, assembly, child, new_qty, new_rev)
    with conn:
        count = conn.execute("SELECT COUNT(*) FROM ecr").fetchone()[0]
        ecr_id = f"ECR-{count + 1:04d}"
        conn.execute(
            "INSERT INTO ecr (ecr_id, title, category, assembly, action, child, new_qty, new_unit, "
            "new_rev, requester, approvers, reason, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Draft', ?)",
            (ecr_id, title.strip(), category.strip(), assembly, action, child, new_qty,
             new_unit.upper(), new_rev, requester, ",".join(approvers), reason, to_iso(now)))
        _log(conn, ecr_id, requester, "create", "Draft", "Draft", title.strip(), now)
    return get_ecr(conn, ecr_id)


def submit(conn: sqlite3.Connection, ecr_id: str, actor: str, now: datetime | None = None) -> Ecr:
    ecr = get_ecr(conn, ecr_id)
    if actor != ecr.requester:
        raise ChangeError("only the requester can submit a change")
    with conn:
        _move(conn, ecr, "Submitted", actor, "submit", "", now)
    return get_ecr(conn, ecr_id)


def _approvals(conn: sqlite3.Connection, ecr_id: str) -> set[str]:
    """People who approved since the most recent submit (a resubmit starts fresh)."""
    rows = conn.execute(
        "SELECT actor FROM change_history WHERE ecr_id = ? AND action = 'approve' AND id > "
        "COALESCE((SELECT MAX(id) FROM change_history WHERE ecr_id = ? AND action = 'submit'), 0)",
        (ecr_id, ecr_id))
    return {r["actor"] for r in rows}


def approve(conn: sqlite3.Connection, ecr_id: str, actor: str, now: datetime | None = None) -> Ecr:
    """Record one approval. The ECR becomes Approved when every listed approver has approved."""
    ecr = get_ecr(conn, ecr_id)
    if actor == ecr.requester:
        raise ChangeError("the requester cannot approve their own change")
    if ecr.state != "Submitted":
        raise InvalidTransition(f"{ecr.ecr_id} is {ecr.state}; only a Submitted change can be approved")
    if actor not in ecr.approvers:
        raise ChangeError(f"{actor} is not on the approver list for {ecr.ecr_id}")
    done = _approvals(conn, ecr_id)
    if actor in done:
        raise ChangeError(f"{actor} has already approved {ecr.ecr_id}")
    done.add(actor)
    note = f"{len(done)} of {len(ecr.approvers)} approvals"
    with conn:
        if done >= set(ecr.approvers):
            _move(conn, ecr, "Approved", actor, "approve", note, now)
        else:  # still waiting on other approvers: state stays Submitted
            _log(conn, ecr_id, actor, "approve", "Submitted", "Submitted", note, now)
    return get_ecr(conn, ecr_id)


def reject(conn: sqlite3.Connection, ecr_id: str, actor: str, reason: str,
           now: datetime | None = None) -> Ecr:
    ecr = get_ecr(conn, ecr_id)
    if actor not in ecr.approvers:
        raise ChangeError(f"{actor} is not on the approver list for {ecr.ecr_id}")
    if not reason.strip():
        raise ChangeError("a rejection needs a reason")
    with conn:
        _move(conn, ecr, "Rejected", actor, "reject", reason.strip(), now)
    return get_ecr(conn, ecr_id)


def reopen(conn: sqlite3.Connection, ecr_id: str, actor: str, now: datetime | None = None) -> Ecr:
    """Send a Rejected change back to Draft so it can be reworked and resubmitted."""
    ecr = get_ecr(conn, ecr_id)
    if actor != ecr.requester:
        raise ChangeError("only the requester can reopen a change")
    with conn:
        _move(conn, ecr, "Draft", actor, "reopen", "", now)
    return get_ecr(conn, ecr_id)


def release(conn: sqlite3.Connection, ecr_id: str, actor: str, now: datetime | None = None) -> Ecr:
    """Apply an Approved change to the BOM, bump the assembly revision, mark it Released."""
    ecr = get_ecr(conn, ecr_id)
    _require_transition(ecr, "Released")
    # The BOM may have changed since the ECR was written, so re-check against today's BOM.
    bom = load_bom(conn)
    _check_applicable(bom, ecr.action, ecr.assembly, ecr.child, ecr.new_qty, ecr.new_rev)
    parent, kid = bom.part(ecr.assembly), bom.part(ecr.child)
    assert parent is not None  # _check_applicable guarantees the assembly exists
    old_rev = parent.revision
    new_rev = bump_revision(old_rev)
    with conn:
        if ecr.action == "add":
            assert kid is not None  # guaranteed for "add" by _check_applicable
            conn.execute(
                "INSERT INTO bom_lines (parent, child, quantity, unit, child_rev) VALUES (?, ?, ?, ?, ?)",
                (ecr.assembly, ecr.child, ecr.new_qty, ecr.new_unit or kid.uom,
                 ecr.new_rev or kid.revision))
        elif ecr.action == "remove":
            conn.execute("DELETE FROM bom_lines WHERE parent = ? AND child = ?", (ecr.assembly, ecr.child))
        elif ecr.action == "change_qty":
            conn.execute("UPDATE bom_lines SET quantity = ?, unit = COALESCE(NULLIF(?, ''), unit) "
                         "WHERE parent = ? AND child = ?",
                         (ecr.new_qty, ecr.new_unit, ecr.assembly, ecr.child))
        else:  # change_rev
            conn.execute("UPDATE bom_lines SET child_rev = ? WHERE parent = ? AND child = ?",
                         (ecr.new_rev, ecr.assembly, ecr.child))
        conn.execute("UPDATE parts SET revision = ? WHERE id = "
                     "(SELECT MIN(id) FROM parts WHERE part_number = ?)", (new_rev, ecr.assembly))
        _move(conn, ecr, "Released", actor, "release",
              f"{ecr.assembly} revision {old_rev} -> {new_rev}", now)
    return get_ecr(conn, ecr_id)


def impact_of_change(bom: Bom, ecr: Ecr) -> list[Affected]:
    """The edited assembly (level 0) plus every assembly above it, at every level."""
    return [Affected(ecr.assembly, 0, (ecr.assembly,))] + bom.where_used(ecr.assembly)
