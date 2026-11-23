import unittest

from bomcc.bom import Bom, CycleError, bump_revision
from tests.helpers import L, P, small_bom


class ExplosionTests(unittest.TestCase):
    def test_totals_match_hand_calculation(self) -> None:
        # SCREW: 4 direct + 2 SUB1 x 3 + 1 SUB2 x 2 = 12.  PLATE: 2 x 1 = 2.  WIRE: 1 x 1.5 = 1.5
        totals = {(x.part_number, x.unit): x.quantity for x in small_bom().explode("TOP")}
        self.assertEqual(totals, {("SCREW", "EA"): 12, ("PLATE", "EA"): 2, ("WIRE", "M"): 1.5})

    def test_sub_assembly_explodes_on_its_own(self) -> None:
        totals = {x.part_number: x.quantity for x in small_bom().explode("SUB1")}
        self.assertEqual(totals, {"SCREW": 3, "PLATE": 1})

    def test_multiplier_scales_every_line(self) -> None:
        totals = {x.part_number: x.quantity for x in small_bom().explode("TOP", multiplier=10)}
        self.assertEqual(totals["SCREW"], 120)

    def test_same_part_in_two_units_stays_separate(self) -> None:
        bom = Bom([P("A"), P("X")], [L("A", "X", 2, "EA"), L("A", "X", 1, "M")])
        self.assertEqual([(x.unit, x.quantity) for x in bom.explode("A")], [("EA", 2), ("M", 1)])

    def test_unknown_child_is_reported_as_a_leaf(self) -> None:
        bom = Bom([P("A")], [L("A", "GHOST", 3)])
        self.assertEqual([(x.part_number, x.quantity) for x in bom.explode("A")], [("GHOST", 3)])

    def test_unknown_root_raises(self) -> None:
        with self.assertRaises(ValueError):
            small_bom().explode("NOPE")

    def test_cycle_is_detected_with_its_path(self) -> None:
        bom = Bom([P("A"), P("B"), P("C")], [L("A", "B"), L("B", "C"), L("C", "A")])
        with self.assertRaises(CycleError) as ctx:
            bom.explode("A")
        self.assertEqual(ctx.exception.path, ["A", "B", "C", "A"])

    def test_self_reference_is_a_cycle(self) -> None:
        bom = Bom([P("A")], [L("A", "A")])
        with self.assertRaises(CycleError):
            bom.explode("A")


class WhereUsedTests(unittest.TestCase):
    def test_every_level_is_listed_nearest_first(self) -> None:
        found = [(a.part_number, a.level) for a in small_bom().where_used("SCREW")]
        self.assertEqual(found, [("SUB1", 1), ("SUB2", 1), ("TOP", 1)])

    def test_multi_level_paths(self) -> None:
        bom = Bom([P("TOP"), P("MID"), P("LOW"), P("LEAF")],
                  [L("TOP", "MID"), L("MID", "LOW"), L("LOW", "LEAF")])
        found = {a.part_number: (a.level, a.path) for a in bom.where_used("LEAF")}
        self.assertEqual(found, {"LOW": (1, ("LOW", "LEAF")),
                                 "MID": (2, ("MID", "LOW", "LEAF")),
                                 "TOP": (3, ("TOP", "MID", "LOW", "LEAF"))})

    def test_part_with_no_parent_has_empty_result(self) -> None:
        self.assertEqual(small_bom().where_used("TOP"), [])

    def test_where_used_survives_a_cycle(self) -> None:
        bom = Bom([P("A"), P("B")], [L("A", "B"), L("B", "A")])
        self.assertEqual([a.part_number for a in bom.where_used("A")], ["B"])


class MiscTests(unittest.TestCase):
    def test_bump_revision(self) -> None:
        self.assertEqual([bump_revision(r) for r in ("A", "B", "Z", "AZ", "7")],
                         ["B", "C", "AA", "BA", "8"])
        with self.assertRaises(ValueError):
            bump_revision("rev-1")

    def test_descendants(self) -> None:
        self.assertEqual(small_bom().descendants("SUB2"), {"SCREW", "WIRE"})

    def test_tree_lines_show_quantities_and_stop_at_cycles(self) -> None:
        tree = small_bom().tree_lines("TOP")
        self.assertIn("2 EA x SUB1", tree[1])
        looped = Bom([P("A"), P("B")], [L("A", "B"), L("B", "A")]).tree_lines("A")
        self.assertTrue(any("circular" in line for line in looped))


if __name__ == "__main__":
    unittest.main()
