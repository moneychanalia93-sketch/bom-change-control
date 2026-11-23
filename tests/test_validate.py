import unittest
from pathlib import Path

from bomcc import validate
from bomcc.bom import Bom
from bomcc.db import load_bom
from bomcc.loader import load_csv
from tests.helpers import L, P, T, memory_db, small_bom, small_lines, small_parts, small_store

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_bom.csv"


def with_lines(*extra) -> Bom:
    return Bom(small_parts(), small_lines() + list(extra))


class CleanFixtureTests(unittest.TestCase):
    def test_every_check_passes_on_the_clean_fixture(self) -> None:
        for name, check in validate.CHECKS.items():
            with self.subTest(check=name):
                self.assertEqual(check(small_bom()), [])


class MissingChildrenTests(unittest.TestCase):
    def test_unknown_child_is_an_error_on_the_parent(self) -> None:
        found = validate.check_missing_children(with_lines(L("SUB1", "GHOST")))
        self.assertEqual([(f.severity, f.part_number) for f in found], [("error", "SUB1")])
        self.assertIn("GHOST", found[0].message)

    def test_line_with_unknown_parent_is_an_error(self) -> None:
        found = validate.check_missing_children(with_lines(L("NOBODY", "SCREW")))
        self.assertEqual(len(found), 1)
        self.assertIn("NOBODY", found[0].message)


class DuplicatePartTests(unittest.TestCase):
    def test_duplicate_part_number_is_an_error(self) -> None:
        bom = Bom(small_parts() + [P("SCREW")], small_lines())
        found = validate.check_duplicate_part_numbers(bom)
        self.assertEqual([(f.severity, f.part_number) for f in found], [("error", "SCREW")])
        self.assertIn("2 times", found[0].message)


class BadQuantityTests(unittest.TestCase):
    def test_zero_and_negative_quantities_are_errors(self) -> None:
        found = validate.check_bad_quantities(with_lines(L("SUB1", "WIRE", 0, "M"), L("SUB2", "PLATE", -2)))
        self.assertEqual(len(found), 2)
        self.assertTrue(all(f.severity == "error" for f in found))

    def test_small_positive_quantity_passes(self) -> None:
        self.assertEqual(validate.check_bad_quantities(with_lines(L("SUB1", "WIRE", 0.01, "M"))), [])


class ReleaseControlTests(unittest.TestCase):
    def test_released_assembly_with_draft_child_is_an_error(self) -> None:
        parts = [p if p.part_number != "PLATE" else P("PLATE", status="Draft") for p in small_parts()]
        found = validate.check_released_with_draft_child(Bom(parts, small_lines()))
        self.assertEqual([(f.severity, f.part_number) for f in found], [("error", "SUB1")])
        self.assertIn("PLATE", found[0].message)

    def test_draft_assembly_may_contain_draft_children(self) -> None:
        parts = [P("D", status="Draft"), P("X", status="Draft")]
        self.assertEqual(validate.check_released_with_draft_child(Bom(parts, [L("D", "X")])), [])


class OrphanTests(unittest.TestCase):
    def test_unused_part_is_info(self) -> None:
        found = validate.check_orphaned_parts(Bom(small_parts() + [P("LOOSE")], small_lines()))
        self.assertEqual([(f.severity, f.part_number) for f in found], [("info", "LOOSE")])

    def test_top_level_assembly_is_not_an_orphan(self) -> None:
        self.assertEqual(validate.check_orphaned_parts(small_bom()), [])


class CircularTests(unittest.TestCase):
    def test_loop_is_one_error_listing_the_path(self) -> None:
        found = validate.check_circular_structures(with_lines(L("SUB1", "TOP")))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].severity, "error")
        self.assertIn("SUB1 -> TOP -> SUB1", found[0].message)

    def test_self_reference_is_found(self) -> None:
        found = validate.check_circular_structures(with_lines(L("SUB2", "SUB2")))
        self.assertIn("SUB2 -> SUB2", found[0].message)

    def test_shared_part_in_a_diamond_is_not_a_cycle(self) -> None:
        # SCREW is reachable from TOP by three routes; that is sharing, not looping.
        self.assertEqual(validate.check_circular_structures(small_bom()), [])


class UnitTests(unittest.TestCase):
    def test_line_unit_that_differs_from_part_unit_is_a_warning(self) -> None:
        found = validate.check_inconsistent_units(with_lines(L("SUB1", "PLATE", 1, "M")))
        self.assertEqual([(f.severity, f.part_number) for f in found], [("warning", "SUB1")])


class RunTests(unittest.TestCase):
    def test_sample_data_has_exactly_the_two_seeded_defects(self) -> None:
        conn = memory_db()
        load_csv(conn, SAMPLE)
        found = validate.run_checks(load_bom(conn))
        self.assertEqual(sorted((f.check, f.part_number) for f in found),
                         [("missing_children", "CFG-13000"), ("released_with_draft_child", "CFG-14000")])

    def test_run_checks_lists_errors_before_info(self) -> None:
        found = validate.run_checks(Bom(small_parts() + [P("LOOSE")], small_lines() + [L("SUB1", "GHOST")]))
        self.assertEqual([f.severity for f in found], ["error", "info"])

    def test_run_and_record_stores_run_and_findings(self) -> None:
        conn = small_store()
        with conn:
            conn.execute("INSERT INTO bom_lines (parent, child, quantity, unit) VALUES ('SUB1','GHOST',1,'EA')")
        found = validate.run_and_record(conn, T(2))
        run = conn.execute("SELECT * FROM validation_runs").fetchone()
        self.assertEqual((run["parts_count"], run["errors"], run["ts"]),
                         (6, 1, "2026-03-02T09:00:00+00:00"))
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0], len(found))


if __name__ == "__main__":
    unittest.main()
