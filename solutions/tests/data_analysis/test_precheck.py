# -*- coding: utf-8 -*-
"""Тесты solutions/data_analysis/precheck.py — чистые функции, tmpdir, без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.data_analysis.test_precheck -v
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.data_analysis.precheck import (
    DETECTORS, csv_file_missing, declared_column_absent, find_csv_path,
    mentions_column, parse_declared_columns, precheck, profile_csv,
    read_csv_text, read_header,
)

INSTR = (
    "The file sales.csv has columns order_id,region,product,month,units,price "
    "(20 rows). How many units of product 'widget' were sold in months 7 "
    "through 8 inclusive? Use bash with python3 to compute it. Work only "
    "inside the current directory. When you are done, write ONLY the final "
    "answer to the file answer.txt in the current directory (for example "
    "with bash: echo 42 > answer.txt).")

CSV = ("order_id,region,product,month,units,price\n"
       "1,west,widget,5,5,33\n"
       "2,north,sprocket,6,28,28\n"
       "3,north,gizmo,1,39,17\n")


class Env(unittest.TestCase):
    """tmpdir-задача с workspace и одним CSV."""

    def make_workspace(self, instruction=INSTR, csv_text=CSV,
                       name="sales.csv") -> Path:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "data_analysis_8888"
        ws = task_dir / "workspace"
        ws.mkdir(parents=True)
        if csv_text is not None:
            (ws / name).write_text(csv_text, encoding="utf-8")
        return ws


class ParseDeclarationTest(Env):
    def test_canonical(self):
        self.assertEqual(
            parse_declared_columns(INSTR),
            ["order_id", "region", "product", "month", "units", "price"])

    def test_does_not_swallow_question(self):
        # без скобок конец декларации — конец предложения, а не строка
        instr = ("The file data.csv has columns a,b,c. How many a rows are "
                 "there? Write the answer to answer.txt.")
        self.assertEqual(parse_declared_columns(instr), ["a", "b", "c"])

    def test_and_separator(self):
        instr = "The file data.csv has columns: units, price and region."
        self.assertEqual(parse_declared_columns(instr), ["units", "price", "region"])

    def test_no_declaration(self):
        self.assertIsNone(parse_declared_columns("How many rows are there?"))

    def test_garbage_declaration_is_none(self):
        self.assertIsNone(parse_declared_columns(
            "The file has columns ??? (3 rows)."))


class FindCsvTest(Env):
    def test_named_file(self):
        ws = self.make_workspace()
        self.assertEqual(find_csv_path(INSTR, [ws]), ws / "sales.csv")

    def test_case_insensitive_name(self):
        ws = self.make_workspace(name="Sales.CSV")
        self.assertEqual(find_csv_path(INSTR, [ws]), ws / "Sales.CSV")

    def test_wrong_name_falls_back_to_single_csv(self):
        # отказ за неточное имя потерял бы решаемую задачу
        ws = self.make_workspace(name="data.csv")
        self.assertEqual(find_csv_path(INSTR, [ws]), ws / "data.csv")

    def test_no_instruction_name_uses_single_csv(self):
        ws = self.make_workspace(name="table.csv")
        self.assertEqual(find_csv_path("How many rows?", [ws]), ws / "table.csv")

    def test_no_file(self):
        ws = self.make_workspace(csv_text=None)
        self.assertIsNone(find_csv_path(INSTR, [ws]))

    def test_missing_dir_ignored(self):
        self.assertIsNone(find_csv_path(INSTR, [Path("/nonexistent/x"), None]))


class HeaderTest(Env):
    def test_plain(self):
        ws = self.make_workspace()
        self.assertEqual(read_header(ws / "sales.csv"),
                         (["order_id", "region", "product", "month",
                           "units", "price"], ","))

    def test_bom_stripped(self):
        ws = self.make_workspace(csv_text="\ufeffa,b\n1,2\n")
        self.assertEqual(read_header(ws / "sales.csv")[0], ["a", "b"])

    def test_semicolon_sniffed(self):
        ws = self.make_workspace(csv_text="a;b;c\n1;2;3\n")
        header, delimiter = read_header(ws / "sales.csv")
        self.assertEqual(header, ["a", "b", "c"])
        self.assertEqual(delimiter, ";")


class ProfileTest(Env):
    def test_kinds_nulls_range(self):
        csv_text = ("name,amount,note\n"
                    "a,10,x\n"
                    "b,,y\n"
                    "c,30,\n")
        ws = self.make_workspace(csv_text=csv_text)
        header, delim = read_header(ws / "sales.csv")
        stats, rows = profile_csv(ws / "sales.csv", header, delim)
        self.assertEqual(rows, 3)
        self.assertEqual(stats["amount"].kind, "int")
        self.assertEqual(stats["amount"].nulls, 1)
        self.assertEqual((stats["amount"].min, stats["amount"].max), (10.0, 30.0))
        self.assertEqual(stats["note"].nulls, 1)
        self.assertEqual(stats["name"].kind, "text")
        self.assertEqual(stats["name"].uniq_count, 3)
        self.assertIn("nulls 1/3", stats["amount"].describe())
        self.assertIn("range 10..30", stats["amount"].describe())
        self.assertIn("3 unique: a, b, c", stats["name"].describe())

    def test_nan_variants_counted_as_nulls(self):
        csv_text = "v\n\nnan\nNA\n5\n"
        ws = self.make_workspace(csv_text=csv_text)
        header, delim = read_header(ws / "sales.csv")
        stats, _ = profile_csv(ws / "sales.csv", header, delim)
        # пустая строка не считается строкой данных, nan/NA — nulls
        self.assertEqual(stats["v"].nulls, 2)
        self.assertEqual(stats["v"].kind, "int")

    def test_mixed_kind_promotes_to_text(self):
        ws = self.make_workspace(csv_text="v\n1\nabc\n2\n")
        header, delim = read_header(ws / "sales.csv")
        stats, _ = profile_csv(ws / "sales.csv", header, delim)
        self.assertEqual(stats["v"].kind, "text")

    def test_ragged_rows_do_not_crash(self):
        ws = self.make_workspace(csv_text="a,b,c\n1,2\n3\n")
        header, delim = read_header(ws / "sales.csv")
        stats, rows = profile_csv(ws / "sales.csv", header, delim)
        self.assertEqual(rows, 2)
        self.assertEqual(stats["c"].nulls, 2)          # нет ячейки -> null

    def test_uniq_overflow_reported_compactly(self):
        rows = "\n".join(f"id{i}" for i in range(600))
        ws = self.make_workspace(csv_text="v\n" + rows + "\n")
        header, delim = read_header(ws / "sales.csv")
        stats, _ = profile_csv(ws / "sales.csv", header, delim)
        self.assertIsNone(stats["v"].uniq_count)
        self.assertIn("+ unique", stats["v"].describe())

    def test_all_null_column(self):
        ws = self.make_workspace(csv_text="v,w\n,1\n,2\n")
        header, delim = read_header(ws / "sales.csv")
        stats, _ = profile_csv(ws / "sales.csv", header, delim)
        self.assertEqual(stats["v"].kind, "unknown")
        self.assertIn("empty; nulls 2/2", stats["v"].describe())


class ReadCsvTextTest(Env):
    def test_row_cap(self):
        ws = self.make_workspace()
        text, shown = read_csv_text(ws / "sales.csv", max_rows=2)
        self.assertEqual(shown, 2)
        lines = text.splitlines()
        self.assertEqual(len(lines), 3)                # заголовок + 2 строки
        self.assertTrue(lines[0].startswith("order_id"))

    def test_char_cap(self):
        ws = self.make_workspace()
        text, shown = read_csv_text(ws / "sales.csv", max_chars=10)
        self.assertEqual(shown, 0)                     # больше заголовка не влезло
        self.assertEqual(text, "order_id,region,product,month,units,price")

    def test_none_path(self):
        self.assertEqual(read_csv_text(None), ("", 0))


class ColumnMatchTest(unittest.TestCase):
    def test_mentions_column(self):
        self.assertTrue(mentions_column("how many units were sold", "units"))
        self.assertTrue(mentions_column("how many units", "unit"))
        self.assertFalse(mentions_column("how many crates", "units"))

    def test_mentions_composite_name(self):
        self.assertTrue(mentions_column("group by order id", "order_id"))


class DetectorTest(Env):
    def test_file_missing(self):
        ws = self.make_workspace(csv_text=None)
        pc = precheck(INSTR, [ws])
        self.assertFalse(pc.ok)
        self.assertIn("sales.csv", pc.reason)
        self.assertEqual(csv_file_missing(pc, INSTR), pc.reason)

    def test_file_present_passes(self):
        ws = self.make_workspace()
        self.assertIsNone(csv_file_missing(precheck(INSTR, [ws]), INSTR))

    def test_declared_and_asked_but_absent(self):
        # ловушка: инструкция обещает quantity, в файле units, вопрос про quantity
        instr = ("The file sales.csv has columns product,quantity (3 rows). "
                 "What is the total quantity of product 'widget'? "
                 "Write the answer to answer.txt.")
        ws = self.make_workspace(instruction=instr)
        pc = precheck(instr, [ws])
        self.assertFalse(pc.ok)
        self.assertIn("quantity", pc.reason)
        self.assertIn("units", pc.reason)              # фактические колонки видны

    def test_declared_absent_not_asked_passes(self):
        # лишняя колонка в декларации, но о ней не спрашивают — не отказ
        instr = ("The file sales.csv has columns product,quantity,units "
                 "(3 rows). What is the total units of product 'widget'? "
                 "Write the answer to answer.txt.")
        ws = self.make_workspace(instruction=instr)
        self.assertTrue(precheck(instr, [ws]).ok)

    def test_extra_column_in_file_passes(self):
        # в файле больше колонок, чем в декларации: инструкция могла не перечислить всё
        instr = ("The file sales.csv has columns product,units (3 rows). "
                 "What is the total units? Write the answer to answer.txt.")
        ws = self.make_workspace(instruction=instr)
        self.assertTrue(precheck(instr, [ws]).ok)

    def test_no_declaration_passes(self):
        ws = self.make_workspace()
        self.assertTrue(precheck("How many units?", [ws]).ok)

    def test_detectors_can_be_disabled(self):
        ws = self.make_workspace(csv_text=None)
        self.assertTrue(precheck(INSTR, [ws], detectors=[]).ok)
        self.assertIsNone(precheck(INSTR, [ws], detectors=[]).reason)

    def test_single_detector_subset(self):
        ws = self.make_workspace(csv_text=None)
        pc = precheck(INSTR, [ws], detectors=[declared_column_absent])
        self.assertTrue(pc.ok)                         # file_missing отключён

    def test_registry_contents(self):
        self.assertIn(csv_file_missing, DETECTORS)
        self.assertIn(declared_column_absent, DETECTORS)


class PrecheckPipelineTest(Env):
    def test_training_like_task(self):
        ws = self.make_workspace()
        pc = precheck(INSTR, [ws])
        self.assertTrue(pc.ok)
        self.assertEqual(pc.rows, 3)
        self.assertEqual(len(pc.header), 6)
        self.assertEqual(pc.declared[0], "order_id")
        self.assertIn("units: int; nulls 0/3", pc.profile_text())

    def test_empty_csv_is_reported_not_crashing(self):
        ws = self.make_workspace(csv_text="")
        pc = precheck(INSTR, [ws])
        self.assertEqual(pc.header, [])
        self.assertEqual(pc.rows, 0)


if __name__ == "__main__":
    unittest.main()
