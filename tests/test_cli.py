import contextlib
import io
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bomcc.cli import main

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = str(ROOT / "data" / "sample_bom.csv")


class CliTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.db = str(self.dir / "test.db")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def loaded(self) -> None:
        self.assertEqual(self.run_cli("load", SAMPLE, "--db", self.db)[0], 0)

    def test_load_reports_counts(self) -> None:
        code, out, _ = self.run_cli("load", SAMPLE, "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("Loaded 60 parts and 69 BOM lines", out)

    def test_validate_finds_the_two_seeded_defects_and_exits_1(self) -> None:
        self.loaded()
        code, out, _ = self.run_cli("validate", "--db", self.db)
        self.assertEqual(code, 1)
        self.assertIn("2 finding(s), 2 error(s)", out)
        self.assertIn("CFG-13500", out)
        self.assertIn("CFG-14300", out)

    def test_explode_totals_match_hand_calculation(self) -> None:
        # M3x6 screws in the chassis: 8x2 (power bays) + 6x3 (fan trays) + 12 (drive cage) + 4 (front panel) = 50
        self.loaded()
        code, out, _ = self.run_cli("explode", "CFG-10001", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"CFG-30001\s+50 EA")
        self.assertRegex(out, r"CFG-13100\s+6 EA")  # 2 fans per tray x 3 trays

    def test_explode_tree_option_prints_the_hierarchy(self) -> None:
        self.loaded()
        _, out, _ = self.run_cli("explode", "CFG-13000", "--tree", "--db", self.db)
        self.assertIn("2 EA x CFG-13100", out)

    def test_impact_lists_every_level(self) -> None:
        self.loaded()
        code, out, _ = self.run_cli("impact", "CFG-13100", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertRegex(out, r"level 1\s+CFG-13000")
        self.assertRegex(out, r"level 2\s+CFG-10001")

    def test_unknown_part_is_a_clean_error(self) -> None:
        self.loaded()
        code, _, err = self.run_cli("explode", "NOPE", "--db", self.db)
        self.assertEqual(code, 2)
        self.assertIn("error: unknown part NOPE", err)

    def test_full_change_lifecycle_and_report(self) -> None:
        self.loaded()
        self.run_cli("validate", "--db", self.db)
        code, out, _ = self.run_cli(
            "change", "new", "--title", "Drop dangling ref", "--category", "Data correction",
            "--assembly", "CFG-13000", "--action", "remove", "--child", "CFG-13500",
            "--by", "j.okafor", "--approvers", "m.lindqvist,s.tanaka", "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("ECR-0001", out)
        self.assertIn("level 1  CFG-10001", out)

        def step(*args: str) -> tuple[int, str, str]:
            return self.run_cli("change", *args, "--db", self.db)

        self.assertEqual(step("submit", "ECR-0001", "--by", "j.okafor")[0], 0)
        code, _, err = step("approve", "ECR-0001", "--by", "j.okafor")
        self.assertEqual(code, 2)
        self.assertIn("cannot approve their own change", err)
        code, _, err = step("release", "ECR-0001", "--by", "j.okafor")
        self.assertEqual(code, 2)
        self.assertIn("cannot move to Released", err)
        self.assertEqual(step("approve", "ECR-0001", "--by", "m.lindqvist")[0], 0)
        _, out, _ = step("approve", "ECR-0001", "--by", "s.tanaka")
        self.assertIn("is now Approved", out)
        _, out, _ = step("release", "ECR-0001", "--by", "m.lindqvist")
        self.assertIn("CFG-13000 revision A -> B", out)

        _, out, _ = self.run_cli("validate", "--db", self.db)
        self.assertIn("1 finding(s), 1 error(s)", out)
        _, out, _ = step("show", "ECR-0001")
        self.assertIn("release", out)

        csv_path = self.dir / "out" / "report.csv"
        code, out, _ = self.run_cli("report", "--csv", str(csv_path), "--db", self.db)
        self.assertEqual(code, 0)
        self.assertIn("BOM CHANGE CONTROL REPORT", out)
        self.assertTrue(csv_path.read_text().startswith("section,key,detail,value"))

    def test_reject_requires_a_reason_argument(self) -> None:
        self.loaded()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(["change", "reject", "ECR-0001", "--by", "x", "--db", self.db])


class DemoTests(unittest.TestCase):
    def test_demo_runs_under_thirty_seconds_via_python_dash_m(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            result = subprocess.run([sys.executable, "-m", "bomcc", "demo", "--db", str(Path(tmp) / "demo.db")],
                                    cwd=ROOT, capture_output=True, text=True, timeout=60)
            elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(elapsed, 30)
        self.assertIn("Blocked: the requester cannot approve their own change", result.stdout)
        self.assertRegex(result.stdout, r"mean 2\.50, median 2\.50, n=2")
        self.assertIsNone(re.search(r"Traceback", result.stdout + result.stderr))


if __name__ == "__main__":
    unittest.main()
