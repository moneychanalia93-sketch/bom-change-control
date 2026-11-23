import tempfile
import unittest
from pathlib import Path

from bomcc.db import load_bom
from bomcc.loader import LoadError, load_csv
from tests.helpers import memory_db

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample_bom.csv"
HEADER = "record,part_number,description,revision,status,uom,owner,child_part,quantity,unit,child_rev\n"


class LoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = memory_db()

    def write(self, body: str) -> Path:
        path = Path(self.tmp.name) / "bom.csv"
        path.write_text(HEADER + body, encoding="utf-8")
        return path

    def test_sample_file_loads_sixty_parts(self) -> None:
        parts, lines = load_csv(self.conn, SAMPLE)
        self.assertEqual((parts, lines), (60, 69))
        self.assertEqual(len(load_bom(self.conn).parts), 60)

    def test_zero_quantity_loads_so_the_validator_can_flag_it(self) -> None:
        path = self.write("PART,A,,A,Released,ea,o,,,,\nPART,B,,A,Released,ea,o,,,,\nBOM,A,,,,,,B,0,ea,A\n")
        load_csv(self.conn, path)
        self.assertEqual(load_bom(self.conn).lines[0].quantity, 0)

    def test_units_and_status_are_normalised(self) -> None:
        load_csv(self.conn, self.write("PART,A,,A,released,ea,o,,,,\n"))
        part = load_bom(self.conn).parts[0]
        self.assertEqual((part.status, part.uom), ("Released", "EA"))

    def test_non_numeric_quantity_names_the_line(self) -> None:
        with self.assertRaisesRegex(LoadError, "line 2.*not a number"):
            load_csv(self.conn, self.write("BOM,A,,,,,,B,lots,EA,\n"))

    def test_unknown_status_is_rejected(self) -> None:
        with self.assertRaisesRegex(LoadError, "status"):
            load_csv(self.conn, self.write("PART,A,,A,Pending,EA,o,,,,\n"))

    def test_missing_column_is_rejected(self) -> None:
        path = Path(self.tmp.name) / "bad.csv"
        path.write_text("record,part_number\nPART,A\n", encoding="utf-8")
        with self.assertRaisesRegex(LoadError, "missing column"):
            load_csv(self.conn, path)

    def test_loading_again_replaces_the_old_bom(self) -> None:
        load_csv(self.conn, SAMPLE)
        load_csv(self.conn, self.write("PART,A,,A,Released,EA,o,,,,\n"))
        self.assertEqual(len(load_bom(self.conn).parts), 1)


if __name__ == "__main__":
    unittest.main()
