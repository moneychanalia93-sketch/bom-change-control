"""Recurring-issue report and metrics, as plain text and CSV.

All numbers come from what is stored in SQLite: validation runs, their findings,
and the append-only change history. Cycle time is the number of days from the
last Submitted event to the Released event of a change.
"""

from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from statistics import mean, median


@dataclass(frozen=True)
class TrendRow:
    week: str                       # ISO week, e.g. 2026-W10
    runs: int                       # validation runs that week
    errors: int | None              # errors in the last run that week
    error_rate: float | None        # errors per 1,000 parts in that run
    released: int                   # changes released that week
    mean_cycle_days: float | None   # mean cycle time of those changes


@dataclass(frozen=True)
class Issue:
    kind: str    # "validation" (a check) or "change" (an ECR category that was rejected/reworked)
    name: str
    count: int


@dataclass(frozen=True)
class Report:
    parts: int
    open_changes: int
    released: int
    rejected: int
    reworked: int
    cycle_days: list[float]
    errors: int | None
    error_rate: float | None
    trend: list[TrendRow]
    top_issues: list[Issue]
    findings_by_category: list[tuple[str, int]]
    findings_by_owner: list[tuple[str, int]]
    changes_by_category: list[tuple[str, int]]
    changes_by_owner: list[tuple[str, int]]

    @property
    def mean_cycle(self) -> float | None:
        return mean(self.cycle_days) if self.cycle_days else None

    @property
    def median_cycle(self) -> float | None:
        return median(self.cycle_days) if self.cycle_days else None


def _week(ts: str) -> str:
    return datetime.fromisoformat(ts).strftime("%G-W%V")


def cycle_times(conn: sqlite3.Connection) -> list[tuple[str, str, float]]:
    """(ecr_id, released_at, days from last submit to release) for every Released change."""
    last_submit: dict[str, str] = {}
    out = []
    for r in conn.execute("SELECT ecr_id, ts, action FROM change_history ORDER BY id"):
        if r["action"] == "submit":
            last_submit[r["ecr_id"]] = r["ts"]
        elif r["action"] == "release" and r["ecr_id"] in last_submit:
            days = (datetime.fromisoformat(r["ts"])
                    - datetime.fromisoformat(last_submit[r["ecr_id"]])).total_seconds() / 86400
            out.append((r["ecr_id"], r["ts"], days))
    return out


def _counts(conn: sqlite3.Connection, sql: str) -> list[tuple[str, int]]:
    rows = [(r[0], r[1]) for r in conn.execute(sql)]
    return sorted(rows, key=lambda x: (-x[1], x[0]))


def build_report(conn: sqlite3.Connection) -> Report:
    runs = conn.execute("SELECT * FROM validation_runs ORDER BY id").fetchall()
    cycles = cycle_times(conn)

    by_week: dict[str, list[sqlite3.Row]] = {}
    for run in runs:
        by_week.setdefault(_week(run["ts"]), []).append(run)
    released_by_week: dict[str, list[float]] = {}
    for _, ts, days in cycles:
        released_by_week.setdefault(_week(ts), []).append(days)
    trend = []
    for week in sorted(set(by_week) | set(released_by_week)):
        last = by_week[week][-1] if week in by_week else None
        trend.append(TrendRow(
            week, len(by_week.get(week, [])),
            last["errors"] if last else None,
            last["errors"] * 1000 / last["parts_count"] if last and last["parts_count"] else None,
            len(released_by_week.get(week, [])),
            mean(released_by_week[week]) if week in released_by_week else None))

    latest = runs[-1] if runs else None
    # "Problem changes" = changes that were rejected or sent back for rework at least once.
    problem = ("SELECT {col}, COUNT(*) FROM ecr WHERE ecr_id IN "
               "(SELECT ecr_id FROM change_history WHERE action IN ('reject', 'reopen')) GROUP BY {col}")
    findings_by_category = _counts(conn, "SELECT check_name, COUNT(*) FROM findings GROUP BY check_name")
    changes_by_category = _counts(conn, problem.format(col="category"))
    issues = [Issue("validation", n, c) for n, c in findings_by_category]
    issues += [Issue("change", n, c) for n, c in changes_by_category]
    issues.sort(key=lambda i: (-i.count, i.kind, i.name))

    def distinct(action: str) -> int:
        return conn.execute("SELECT COUNT(DISTINCT ecr_id) FROM change_history WHERE action = ?",
                            (action,)).fetchone()[0]

    return Report(
        parts=latest["parts_count"] if latest else conn.execute("SELECT COUNT(*) FROM parts").fetchone()[0],
        open_changes=conn.execute("SELECT COUNT(*) FROM ecr WHERE state IN ('Draft','Submitted','Approved')").fetchone()[0],
        released=conn.execute("SELECT COUNT(*) FROM ecr WHERE state = 'Released'").fetchone()[0],
        rejected=distinct("reject"),
        reworked=distinct("reopen"),
        cycle_days=[d for _, _, d in cycles],
        errors=latest["errors"] if latest else None,
        error_rate=(latest["errors"] * 1000 / latest["parts_count"]) if latest and latest["parts_count"] else None,
        trend=trend,
        top_issues=issues[:5],
        findings_by_category=findings_by_category,
        findings_by_owner=_counts(conn, "SELECT owner, COUNT(*) FROM findings GROUP BY owner"),
        changes_by_category=changes_by_category,
        changes_by_owner=_counts(conn, problem.format(col="requester")),
    )


def _num(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    if not rows:
        return ["  (none)"]
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]
    fmt = "  " + "  ".join(f"{{:<{w}}}" for w in widths)
    return [fmt.format(*headers).rstrip(), fmt.format(*("-" * w for w in widths)).rstrip()] + [
        fmt.format(*r).rstrip() for r in rows]


def render_text(report: Report, generated: str) -> str:
    """The report as plain text."""
    r = report
    summary = [
        ("Parts checked (latest run)", str(r.parts)),
        ("Open changes (Draft, Submitted, Approved)", str(r.open_changes)),
        ("Released changes", str(r.released)),
        ("Changes rejected at least once", str(r.rejected)),
        ("Changes reworked (reopened)", str(r.reworked)),
        ("Cycle time in days (Submitted to Released)",
         f"mean {_num(r.mean_cycle)}, median {_num(r.median_cycle)}, n={len(r.cycle_days)}"),
        ("Data-quality errors (latest run)", _num(r.errors, 0)),
        ("Error rate per 1,000 parts (latest run)", _num(r.error_rate, 1)),
    ]
    out = [f"BOM CHANGE CONTROL REPORT  ({generated})", "", "Summary"]
    out += [f"  {label:<44}: {value}" for label, value in summary]
    out += ["", "Trend by week"]
    out += _table(["week", "runs", "errors", "errors/1000 parts", "released", "mean cycle days"],
                  [[t.week, str(t.runs), _num(t.errors, 0), _num(t.error_rate, 1),
                    str(t.released), _num(t.mean_cycle_days)] for t in r.trend])
    out += ["", "Top recurring issues (validation findings across all runs; rejected/reworked changes)"]
    out += _table(["rank", "kind", "issue", "count"],
                  [[str(i), x.kind, x.name, str(x.count)] for i, x in enumerate(r.top_issues, 1)])
    for title, rows in (("Validation findings by category", r.findings_by_category),
                        ("Validation findings by owner", r.findings_by_owner),
                        ("Rejected or reworked changes by category", r.changes_by_category),
                        ("Rejected or reworked changes by owner (requester)", r.changes_by_owner)):
        out += ["", title] + _table(["name", "count"], [[n, str(c)] for n, c in rows])
    return "\n".join(out) + "\n"


def render_csv(report: Report) -> str:
    """The same numbers as tidy CSV rows: section, key, detail, value."""
    r = report
    rows: list[list[object]] = [
        ["summary", "parts_checked", "", r.parts],
        ["summary", "open_changes", "", r.open_changes],
        ["summary", "released_changes", "", r.released],
        ["summary", "rejected_changes", "", r.rejected],
        ["summary", "reworked_changes", "", r.reworked],
        ["summary", "cycle_time_mean_days", "", _csv(r.mean_cycle)],
        ["summary", "cycle_time_median_days", "", _csv(r.median_cycle)],
        ["summary", "errors_latest_run", "", "" if r.errors is None else r.errors],
        ["summary", "errors_per_1000_parts", "", _csv(r.error_rate)],
    ]
    for t in r.trend:
        rows += [["trend", t.week, "validation_runs", t.runs],
                 ["trend", t.week, "errors", "" if t.errors is None else t.errors],
                 ["trend", t.week, "errors_per_1000_parts", _csv(t.error_rate)],
                 ["trend", t.week, "changes_released", t.released],
                 ["trend", t.week, "mean_cycle_days", _csv(t.mean_cycle_days)]]
    rows += [["top_issue", f"{i}", f"{x.kind}:{x.name}", x.count] for i, x in enumerate(r.top_issues, 1)]
    for section, items in (("findings_by_category", r.findings_by_category),
                           ("findings_by_owner", r.findings_by_owner),
                           ("changes_by_category", r.changes_by_category),
                           ("changes_by_owner", r.changes_by_owner)):
        rows += [[section, n, "", c] for n, c in items]
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["section", "key", "detail", "value"])
    writer.writerows(rows)
    return buf.getvalue()


def _csv(value: float | None) -> str:
    return "" if value is None else f"{value:.2f}"
