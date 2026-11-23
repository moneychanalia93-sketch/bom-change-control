"""Command-line interface: `python -m bomcc <command>`."""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from bomcc import change, render, report as report_mod, validate
from bomcc.bom import CycleError
from bomcc.db import connect, load_bom
from bomcc.loader import LoadError, load_csv

DEFAULT_DB = os.environ.get("BOMCC_DB", "var/bomcc.db")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", default=DEFAULT_DB,
                        help="SQLite file (default: var/bomcc.db, or $BOMCC_DB)")

    parser = argparse.ArgumentParser(
        prog="bomcc", description="BOM validation and engineering change control (a model of PLM).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("load", parents=[common], help="load a BOM CSV (replaces the stored BOM)")
    p.add_argument("csv")
    p = sub.add_parser("validate", parents=[common], help="run data-quality checks; exit 1 if any error")
    p.add_argument("--no-record", action="store_true", help="do not store this run for the report")
    p = sub.add_parser("explode", parents=[common], help="flatten an assembly to total quantity per leaf part")
    p.add_argument("assembly")
    p.add_argument("--tree", action="store_true", help="also print the indented BOM tree")
    p = sub.add_parser("impact", parents=[common], help="where-used: every assembly above a part, at every level")
    p.add_argument("part")
    sub.add_parser("report", parents=[common], help="recurring issues and metrics").add_argument(
        "--csv", metavar="PATH", help="also write the report as CSV")
    sub.add_parser("demo", help="run the full story on fictional data").add_argument(
        "--db", default="var/demo.db", help="SQLite file for the demo (default: var/demo.db)")

    change_cmd = sub.add_parser("change", help="engineering change requests")
    csub = change_cmd.add_subparsers(dest="change_command", required=True)
    p = csub.add_parser("new", parents=[common], help="create a change request (state: Draft)")
    p.add_argument("--title", required=True)
    p.add_argument("--category", required=True, help="e.g. 'Data correction', 'Design change'")
    p.add_argument("--assembly", required=True)
    p.add_argument("--action", required=True, choices=change.ACTIONS)
    p.add_argument("--child", required=True)
    p.add_argument("--qty", type=float, help="new quantity (add, change_qty)")
    p.add_argument("--unit", default="", help="unit for the new quantity (default: the part's unit)")
    p.add_argument("--rev", default="", help="child revision to pin (change_rev; optional for add)")
    p.add_argument("--by", required=True, help="requester")
    p.add_argument("--approvers", required=True, help="comma-separated names; all must approve")
    p.add_argument("--reason", default="")
    for name, text in (("submit", "send a Draft change for approval"),
                       ("approve", "record an approval"),
                       ("release", "apply an Approved change to the BOM and bump the revision"),
                       ("reject", "reject a Submitted or Approved change"),
                       ("reopen", "send a Rejected change back to Draft for rework")):
        p = csub.add_parser(name, parents=[common], help=text)
        p.add_argument("ecr_id")
        p.add_argument("--by", required=True, help="who is doing this")
        if name == "reject":
            p.add_argument("--reason", required=True)
    csub.add_parser("list", parents=[common], help="list all changes")
    csub.add_parser("show", parents=[common], help="show one change, its impact and its history").add_argument("ecr_id")
    return parser


def _cmd_load(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    parts, lines = load_csv(conn, args.csv)
    print(f"Loaded {parts} parts and {lines} BOM lines from {args.csv}")
    return 0


def _cmd_validate(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    bom = load_bom(conn)
    found = validate.run_checks(bom) if args.no_record else validate.run_and_record(conn)
    print(render.format_findings(found, len(bom.parts)))
    return 1 if any(f.severity == validate.ERROR for f in found) else 0


def _cmd_explode(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    bom = load_bom(conn)
    if args.tree:
        print("\n".join(bom.tree_lines(args.assembly)) + "\n")
    print(render.format_explosion(bom, args.assembly, bom.explode(args.assembly)))
    return 0


def _cmd_impact(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    bom = load_bom(conn)
    if bom.part(args.part) is None and args.part not in bom.parents:
        raise ValueError(f"unknown part {args.part}")
    print(f"{args.part} is used by:")
    print(render.format_affected(bom.where_used(args.part)))
    return 0


def _cmd_report(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rep = report_mod.build_report(conn)
    print(report_mod.render_text(rep, datetime.now(timezone.utc).strftime("%Y-%m-%d")), end="")
    if args.csv:
        Path(args.csv).parent.mkdir(parents=True, exist_ok=True)
        Path(args.csv).write_text(report_mod.render_csv(rep), encoding="utf-8", newline="")
        print(f"CSV written to {args.csv}")
    return 0


def _cmd_change(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    cmd = args.change_command
    if cmd == "new":
        ecr = change.create_ecr(
            conn, title=args.title, category=args.category, assembly=args.assembly,
            action=args.action, child=args.child, requester=args.by,
            approvers=args.approvers.split(","), new_qty=args.qty, new_unit=args.unit,
            new_rev=args.rev, reason=args.reason)
        print(render.format_ecr(ecr))
        print("Impact (assemblies affected by this change):")
        print(render.format_affected(change.impact_of_change(load_bom(conn), ecr)))
    elif cmd == "list":
        for ecr in change.list_ecrs(conn):
            print(f"{ecr.ecr_id}  {ecr.state:<9} {ecr.assembly:<11} {ecr.action:<10} {ecr.title}")
    elif cmd == "show":
        ecr = change.get_ecr(conn, args.ecr_id)
        print(render.format_ecr(ecr))
        print("Impact:")
        print(render.format_affected(change.impact_of_change(load_bom(conn), ecr)))
        print("History:")
        print(render.format_history(change.history(conn, ecr.ecr_id)))
    else:
        actions = {"submit": change.submit, "approve": change.approve,
                   "release": change.release, "reopen": change.reopen}
        if cmd == "reject":
            ecr = change.reject(conn, args.ecr_id, args.by, args.reason)
        else:
            ecr = actions[cmd](conn, args.ecr_id, args.by)
        last = change.history(conn, ecr.ecr_id)[-1]
        print(f"{ecr.ecr_id} is now {ecr.state}" + (f" ({last.note})" if last.note else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        from bomcc.demo import run_demo
        return run_demo(args.db)
    handlers = {"load": _cmd_load, "validate": _cmd_validate, "explode": _cmd_explode,
                "impact": _cmd_impact, "report": _cmd_report, "change": _cmd_change}
    conn = connect(args.db)
    try:
        return handlers[args.command](conn, args)
    except (change.ChangeError, LoadError, ValueError, CycleError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()
