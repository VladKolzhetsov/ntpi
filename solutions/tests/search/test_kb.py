# -*- coding: utf-8 -*-
"""Тесты solutions/search/kb.py на тренировочной базе search_0001 / search_0005.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.search.test_kb -v
"""

import tempfile
import unittest
from pathlib import Path

from solutions.search.v1.kb import KnowledgeBase, Node

PROJECT = Path(__file__).resolve().parents[3]
TRAIN = PROJECT / "tasks" / "task-dir" / "task-dir"


class TrainingKbTest(unittest.TestCase):
    """Интеграционные тесты на реальной тренировочной базе."""

    @classmethod
    def setUpClass(cls):
        cls.kb5 = KnowledgeBase.from_dir(TRAIN / "search_0005" / "workspace" / "kb")
        cls.kb1 = KnowledgeBase.from_dir(TRAIN / "search_0001" / "workspace" / "kb")

    def test_counts(self):
        self.assertEqual(len(self.kb5), 38)
        persons = [n for n in self.kb5 if n.type == "person"]
        companies = [n for n in self.kb5 if n.type == "company"]
        self.assertEqual((len(persons), len(companies)), (28, 10))
        self.assertEqual(len(self.kb1), self.kb1.files_seen)

    def test_healthy(self):
        self.assertTrue(self.kb5.is_healthy())
        self.assertTrue(self.kb1.is_healthy())

    def test_schema(self):
        schema = self.kb5.schema()
        self.assertEqual(schema["person"], ["Born", "Employer", "Mentor"])
        self.assertEqual(schema["company"], ["Founded", "Headquarters"])

    def test_node_fields(self):
        irina = self.kb1.find("Irina Smith")
        self.assertEqual(len(irina), 1)
        self.assertEqual(irina[0].get("Born"), "1983")       # expected search_0001
        self.assertEqual(irina[0].get("Employer"), "Dune Robotics")

    def test_get_is_case_insensitive(self):
        sergei = self.kb5.find("Sergei Garcia")[0]
        self.assertEqual(sergei.get("born"), "1985")
        self.assertIsNone(sergei.get("no-such-field"))

    def test_two_hop_end_to_end(self):
        """search_0005 целиком: человек -> работодатель -> город (expected: Osaka)."""
        sergei = self.kb5.find("Sergei Garcia")[0]
        employer = self.kb5.resolve(sergei.get("Employer"), prefer_type="company")
        self.assertIsNotNone(employer)
        self.assertEqual(employer.name, "Fjord Data")
        self.assertEqual(employer.get("Headquarters"), "Osaka")

    def test_find_in_text(self):
        hits = self.kb5.find_in_text(
            "In which city is the headquarters of Sergei Garcia's employer?")
        self.assertEqual(hits[0].name, "Sergei Garcia")      # possessive не мешает
        hits = self.kb1.find_in_text("In which year was Irina Smith born?")
        self.assertEqual(hits[0].name, "Irina Smith")


class EdgeCaseTest(unittest.TestCase):
    """Краевые случаи: битые данные, коллизии, скрытые файлы."""

    def test_missing_dir(self):
        kb = KnowledgeBase.from_dir("/no/such/dir")
        self.assertEqual(len(kb), 0)
        self.assertFalse(kb.is_healthy())

    def test_garbage_only(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "random.txt").write_text("no structure\njust text\n",
                                                encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual(len(kb), 0)
            self.assertFalse(kb.is_healthy())

    def test_value_with_colon_and_duplicate_keys(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "thing_x.md").write_text(
                "# X\nNote: a:b:c\nBorn: 1983\nBorn: 2000\n", encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual(len(kb), 1)
            self.assertEqual(kb.nodes[0].get("Note"), "a:b:c")   # двоеточие в value
            self.assertEqual(kb.nodes[0].get("Born"), "2000")    # последний побеждает

    def test_hidden_files_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "._person_ghost.md").write_text("# Ghost\nBorn: 1900\n",
                                                       encoding="utf-8")
            (Path(d) / "person_real.md").write_text("# Real\nBorn: 1950\n",
                                                    encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual(len(kb), 1)
            self.assertEqual(kb.nodes[0].get("Born"), "1950")

    def test_binary_file_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "img.bin").write_bytes(b"\xff\xfe\x00\x01garbage")
            (Path(d) / "person_a.md").write_text("# A\nBorn: 1\n", encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual([n.name for n in kb], ["A"])

    def test_bullets_and_subdirs(self):
        with tempfile.TemporaryDirectory() as d:
            subdir = Path(d) / "nested"
            subdir.mkdir()
            (subdir / "person_x.md").write_text(
                "# X\n\n- Born: 1983\n* Employer: Helix Motors\nrandom line\n",
                encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual(len(kb), 1)
            self.assertEqual(kb.nodes[0].get("Born"), "1983")
            self.assertEqual(kb.nodes[0].get("Employer"), "Helix Motors")
            self.assertEqual(kb.nodes[0].garbage_lines, 1)
            self.assertEqual(kb.nodes[0].type, "person")

    def test_name_collision(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "person_granite.md").write_text("# Granite\nBorn: 1\n",
                                                       encoding="utf-8")
            (Path(d) / "company_granite.md").write_text("# Granite\nFounded: 2\n",
                                                       encoding="utf-8")
            kb = KnowledgeBase.from_dir(d)
            self.assertEqual(len(kb.find("Granite")), 2)            # неоднозначно
            self.assertIsNone(kb.resolve("Granite"))                # -> None
            winner = kb.resolve("Granite", prefer_type="company")
            self.assertIsNotNone(winner)
            self.assertEqual(winner.type, "company")


class MatchTest(unittest.TestCase):
    """kb.match(): обратный поиск по значению поля (для операции match)."""

    @classmethod
    def setUpClass(cls):
        cls.kb5 = KnowledgeBase.from_dir(TRAIN / "search_0005" / "workspace" / "kb")

    def test_unique_hit(self):
        self.assertEqual([n.name for n in self.kb5.match("Headquarters", "Osaka")],
                         ["Fjord Data"])

    def test_case_insensitive(self):
        self.assertEqual([n.name for n in self.kb5.match("headquarters", "OSAKA")],
                         ["Fjord Data"])

    def test_type_filter(self):
        employees = self.kb5.match("Employer", "Fjord Data", type="person")
        self.assertEqual(len(employees), 6)
        self.assertIn("Sergei Garcia", [n.name for n in employees])
        self.assertEqual(self.kb5.match("Employer", "Fjord Data", type="company"), [])

    def test_no_hits(self):
        self.assertEqual(self.kb5.match("Born", "9999"), [])


if __name__ == "__main__":
    unittest.main()
