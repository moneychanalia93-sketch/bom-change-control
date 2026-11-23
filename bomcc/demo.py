"""`python -m bomcc demo`: the whole story on fictional data, in a few seconds.

The demo moves a simulated clock forward day by day so that cycle times and the
weekly trend have something to show. Those timestamps are simulated; nothing
else in the demo is.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from bomcc import change, render, report, validate
from bomcc.bom import fmt_qty
from bomcc.db import connect, load_bom
from bomcc.loader import load_csv

SAMPLE_CSV = Path(__file__).resolve().parent.parent / "data" / "sample_bom.csv"


def _heading(text: str) -> None:
    print(f"\n=== {text} ===")


def run_demo(db_path: str) -> int:
    if not SAMPLE_CSV.exists():
        print(f"error: sample data not found at {SAMPLE_CSV}; run the demo from a full checkout")
        return 2
    db = Path(db_path)
    if db.exists():
        db.unlink()
    conn = connect(db)
    today = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=21)

    def at(day: int) -> datetime:
        return start + timedelta(days=day)

    print("bomcc demo: fictional data, simulated timestamps (21 days compressed into seconds).")

    _heading("1. Load a BOM (rack-mount server chassis + cooling loop controller)")
    parts, lines = load_csv(conn, SAMPLE_CSV)
    print(f"Loaded {parts} parts and {lines} BOM lines.")

    _heading("2. Validate: two defects were planted in the data")
    print(render.format_findings(validate.run_and_record(conn, at(0)), parts))

    _heading("3. Explode the fan tray assembly CFG-13000 (note the unknown part)")
    bom = load_bom(conn)
    print(render.format_explosion(bom, "CFG-13000", bom.explode("CFG-13000")))

    _heading("4. Raise a change to remove the dangling reference")
    ecr = change.create_ecr(
        conn, title="Remove dangling reference CFG-13500 from fan tray", category="Data correction",
        assembly="CFG-13000", action="remove", child="CFG-13500", requester="j.okafor",
        approvers=["m.lindqvist", "s.tanaka"], reason="CFG-13500 was never created as a part", now=at(7))
    print(render.format_ecr(ecr))
    print("Impact (every assembly affected, at every level):")
    print(render.format_affected(change.impact_of_change(bom, ecr)))
    change.submit(conn, ecr.ecr_id, "j.okafor", at(7))

    _heading("5. Approve it (the requester cannot approve their own change)")
    try:
        change.approve(conn, ecr.ecr_id, "j.okafor", at(8))
    except change.ChangeError as exc:
        print(f"Blocked: {exc}")
    for approver, day in (("m.lindqvist", 8), ("s.tanaka", 9)):
        print(f"{approver}: {change.approve(conn, ecr.ecr_id, approver, at(day)).state}")

    _heading("6. Release it: the BOM changes and the assembly revision goes up")
    change.release(conn, ecr.ecr_id, "m.lindqvist", at(10))
    print(render.format_history(change.history(conn, ecr.ecr_id)))
    print("\nValidate again after the release:")
    print(render.format_findings(validate.run_and_record(conn, at(10)), parts))

    _heading("7. A second change is rejected, reworked and released; a third is left open")
    second = change.create_ecr(
        conn, title="Raise screw count on front panel", category="Design change", assembly="CFG-15000",
        action="change_qty", child="CFG-30001", new_qty=6, requester="a.rivera",
        approvers=["j.okafor", "s.tanaka"], now=at(12))
    change.submit(conn, second.ecr_id, "a.rivera", at(12))
    change.reject(conn, second.ecr_id, "s.tanaka", "needs a fastener torque review", at(13))
    change.reopen(conn, second.ecr_id, "a.rivera", at(14))
    change.submit(conn, second.ecr_id, "a.rivera", at(15))
    change.approve(conn, second.ecr_id, "j.okafor", at(16))
    change.approve(conn, second.ecr_id, "s.tanaka", at(16))
    change.release(conn, second.ecr_id, "j.okafor", at(17))
    print(render.format_ecr(change.get_ecr(conn, second.ecr_id)))
    print(render.format_history(change.history(conn, second.ecr_id)))
    third = change.create_ecr(
        conn, title="Add serial label to enclosure", category="Documentation", assembly="CFG-24000",
        action="add", child="CFG-30006", new_qty=1, requester="j.okafor",
        approvers=["a.rivera", "m.lindqvist"], now=at(20))
    change.submit(conn, third.ecr_id, "j.okafor", at(20))
    print(f"\n{third.ecr_id} is now Submitted and waiting for approval.")
    final = load_bom(conn)
    panel, screws = final.part("CFG-15000"), final.line("CFG-15000", "CFG-30001")
    if panel and screws:
        print(f"Revision of CFG-15000 is now {panel.revision}; "
              f"quantity of CFG-30001 in it: {fmt_qty(screws.quantity)}")

    _heading("8. Final validation and report")
    validate.run_and_record(conn, at(21))
    print(report.render_text(report.build_report(conn), today.strftime("%Y-%m-%d")), end="")
    conn.close()
    return 0
