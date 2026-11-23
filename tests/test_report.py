import csv
import io
import sqlite3
import unittest

from bomcc import change, report, validate
from tests.helpers import T, memory_db, small_store


def build_fixture() -> sqlite3.Connection:
    """Six parts, two validation runs and four changes, all with fixed timestamps.

    Hand-computed expectations (used in the tests below):
      ECR-0001 submitted Mar 3 09:00, released Mar 6 09:00        -> 3.0 days  (ISO week 10)
      ECR-0002 resubmitted Mar 11 09:00, released Mar 12 21:00    -> 1.5 days  (ISO week 11)
      ECR-0003 is still a Draft; ECR-0004 was rejected and stays Rejected.
      Cycle times: mean (3.0 + 1.5) / 2 = 2.25, median 2.25.
      Both validation runs see one error in six parts: 1 * 1000 / 6 = 166.67 per 1,000 parts.
    """
    conn = small_store()
    with conn:
        conn.execute("INSERT INTO bom_lines (parent, child, quantity, unit) VALUES ('SUB1','GHOST',1,'EA')")
    validate.run_and_record(conn, T(2))      # week 10
    validate.run_and_record(conn, T(10))     # week 11, same defect still there

    def make(title: str, who: str, category: str, assembly: str, child: str, qty: float) -> str:
        return change.create_ecr(conn, title=title, category=category, assembly=assembly,
                                 action="change_qty", child=child, new_qty=qty, requester=who,
                                 approvers=["a1"], now=T(1)).ecr_id

    e1 = make("one", "r1", "Design change", "SUB1", "SCREW", 5)
    change.submit(conn, e1, "r1", T(3))
    change.approve(conn, e1, "a1", T(4))
    change.release(conn, e1, "a1", T(6))

    e2 = make("two", "r2", "Data correction", "SUB2", "SCREW", 3)
    change.submit(conn, e2, "r2", T(9))
    change.reject(conn, e2, "a1", "needs rework", T(10))
    change.reopen(conn, e2, "r2", T(10))
    change.submit(conn, e2, "r2", T(11))
    change.approve(conn, e2, "a1", T(12, 8))
    change.release(conn, e2, "a1", T(12, 21))

    make("three", "r1", "Design change", "TOP", "SCREW", 6)  # stays Draft

    e4 = make("four", "r2", "Design change", "SUB1", "PLATE", 2)
    change.submit(conn, e4, "r2", T(13))
    change.reject(conn, e4, "a1", "not now", T(13, 10))
    return conn


class ReportNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.conn = build_fixture()
        cls.rep = report.build_report(cls.conn)

    def test_cycle_times_are_computed_from_history_timestamps(self) -> None:
        self.assertEqual([(e, round(d, 2)) for e, _, d in report.cycle_times(self.conn)],
                         [("ECR-0001", 3.0), ("ECR-0002", 1.5)])

    def test_mean_and_median_cycle_time(self) -> None:
        self.assertAlmostEqual(self.rep.mean_cycle, 2.25)
        self.assertAlmostEqual(self.rep.median_cycle, 2.25)

    def test_change_counts(self) -> None:
        self.assertEqual((self.rep.open_changes, self.rep.released, self.rep.rejected, self.rep.reworked),
                         (1, 2, 2, 1))

    def test_error_rate_per_thousand_parts(self) -> None:
        self.assertEqual((self.rep.parts, self.rep.errors), (6, 1))
        self.assertAlmostEqual(self.rep.error_rate, 1000 / 6)

    def test_weekly_trend(self) -> None:
        rows = [(t.week, t.runs, t.errors, t.released, t.mean_cycle_days) for t in self.rep.trend]
        self.assertEqual(rows, [("2026-W10", 1, 1, 1, 3.0), ("2026-W11", 1, 1, 1, 1.5)])

    def test_recurring_issues_are_ranked_by_count(self) -> None:
        self.assertEqual([(i.kind, i.name, i.count) for i in self.rep.top_issues],
                         [("validation", "missing_children", 2),
                          ("change", "Data correction", 1),
                          ("change", "Design change", 1)])

    def test_grouping_by_category_and_owner(self) -> None:
        self.assertEqual(self.rep.findings_by_category, [("missing_children", 2)])
        self.assertEqual(self.rep.findings_by_owner, [("owner1", 2)])
        self.assertEqual(self.rep.changes_by_category, [("Data correction", 1), ("Design change", 1)])
        self.assertEqual(self.rep.changes_by_owner, [("r2", 2)])


class ReportOutputTests(unittest.TestCase):
    def test_text_report_contains_the_numbers(self) -> None:
        text = report.render_text(report.build_report(build_fixture()), "2026-03-14")
        for expected in ("mean 2.25, median 2.25, n=2", "166.7", "2026-W10", "missing_children"):
            self.assertIn(expected, text)

    def test_csv_report_has_the_same_numbers(self) -> None:
        rows = list(csv.reader(io.StringIO(report.render_csv(report.build_report(build_fixture())))))
        self.assertEqual(rows[0], ["section", "key", "detail", "value"])
        self.assertIn(["summary", "cycle_time_mean_days", "", "2.25"], rows)
        self.assertIn(["summary", "errors_per_1000_parts", "", "166.67"], rows)
        self.assertIn(["trend", "2026-W11", "changes_released", "1"], rows)
        self.assertIn(["top_issue", "1", "validation:missing_children", "2"], rows)

    def test_empty_database_reports_n_a_instead_of_failing(self) -> None:
        rep = report.build_report(memory_db())
        self.assertIsNone(rep.mean_cycle)
        self.assertIn("n/a", report.render_text(rep, "2026-03-14"))


if __name__ == "__main__":
    unittest.main()
