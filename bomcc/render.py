"""Plain-text formatting shared by the CLI and the demo."""

from __future__ import annotations

from bomcc.bom import Affected, Bom, ExplodedLine, fmt_qty
from bomcc.change import Ecr, HistoryEvent
from bomcc.validate import Finding


def format_findings(findings: list[Finding], parts_count: int) -> str:
    if not findings:
        return f"Validation passed: {parts_count} parts, no findings."
    lines = [f"{f.severity.upper():<7} {f.check:<26} {f.message}  [owner: {f.owner}]" for f in findings]
    errors = sum(f.severity == "error" for f in findings)
    lines.append(f"{len(findings)} finding(s), {errors} error(s), across {parts_count} parts.")
    return "\n".join(lines)


def format_explosion(bom: Bom, root: str, exploded: list[ExplodedLine]) -> str:
    part = bom.part(root)
    title = f"{root}  {part.description}" if part else root
    if not exploded:
        return f"{title}\n  (no components)"
    def describe(part_number: str) -> str:
        found = bom.part(part_number)
        return found.description if found else "(unknown part)"

    rows = [f"  {x.part_number:<12} {fmt_qty(x.quantity):>8} {x.unit:<3}  {describe(x.part_number)}"
            for x in exploded]
    return f"Total leaf parts for one {title}\n" + "\n".join(rows)


def format_affected(affected: list[Affected]) -> str:
    if not affected:
        return "  (nothing uses this part)"
    return "\n".join(
        f"  level {a.level}  {a.part_number:<12} via {' > '.join(a.path)}" for a in affected)


def format_ecr(ecr: Ecr) -> str:
    detail = {
        "add": f"add {ecr.child} x {fmt_qty(ecr.new_qty or 0)} to {ecr.assembly}",
        "remove": f"remove {ecr.child} from {ecr.assembly}",
        "change_qty": f"set quantity of {ecr.child} in {ecr.assembly} to {fmt_qty(ecr.new_qty or 0)}",
        "change_rev": f"pin {ecr.child} in {ecr.assembly} to revision {ecr.new_rev}",
    }[ecr.action]
    return (f"{ecr.ecr_id}  [{ecr.state}]  {ecr.title}\n"
            f"  change   : {detail}\n"
            f"  category : {ecr.category}   requester: {ecr.requester}   "
            f"approvers: {', '.join(ecr.approvers)}")


def format_history(events: list[HistoryEvent]) -> str:
    return "\n".join(
        f"  {e.ts}  {e.actor:<12} {e.action:<8} {e.from_state} -> {e.to_state}"
        + (f"  ({e.note})" if e.note else "") for e in events)
