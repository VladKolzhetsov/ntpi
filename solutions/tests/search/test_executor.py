# -*- coding: utf-8 -*-
"""Тесты solutions/search/executor.py — исполнение планов на мини-базе. Без LLM.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.search.test_executor -v
"""

import tempfile
import unittest
from pathlib import Path

from solutions.search.v1.executor import execute
from solutions.search.v1.kb import KnowledgeBase
from solutions.search.v1.planner import Plan, Step

KB_FILES = {
    "person_anna.md": "# Anna Petrova\nBorn: 1980\nEmployer: Northwind Ltd\nMentor: Ivan Petrov\n",
    "person_boris.md": "# Boris Ivanov\nBorn: 1980\nEmployer: Northwind Ltd\n",
    "person_ivan.md": "# Ivan Petrov\nBorn: 1950\nEmployer: Northwind Ltd\n",
    "company_northwind.md": "# Northwind Ltd\nFounded: 1995\nHeadquarters: Oslo\n",
    "company_zenith.md": "# Zenith Corp\nFounded: 2001\nHeadquarters: Zenith City\n",
}


def make_plan(steps, answer):
    """Plan из кортежей (var, op, args)."""
    return Plan([Step(var, op, args) for var, op, args in steps], answer)


class ExecutorTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for fname, content in KB_FILES.items():
            (Path(tmp.name) / fname).write_text(content, encoding="utf-8")
        self.kb = KnowledgeBase.from_dir(tmp.name)

    plan = staticmethod(make_plan)

    # --- цепочки ---

    def test_single_hop_scalar(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("b", "get", {"from": "p", "field": "Born"})], "b")
        self.assertEqual(execute(plan, self.kb), "1980")

    def test_answer_node_is_name(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": None})], "p")
        self.assertEqual(execute(plan, self.kb), "Anna Petrova")

    def test_two_hop_person_to_company(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("c", "get", {"from": "p", "field": "Employer"}),
             ("h", "get", {"from": "c", "field": "Headquarters"})], "h")
        self.assertEqual(execute(plan, self.kb), "Oslo")

    def test_two_hop_person_to_person(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("m", "get", {"from": "p", "field": "Mentor"}),
             ("b", "get", {"from": "m", "field": "Born"})], "b")
        self.assertEqual(execute(plan, self.kb), "1950")

    def test_answer_employer_node(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("c", "get", {"from": "p", "field": "Employer"})], "c")
        self.assertEqual(execute(plan, self.kb), "Northwind Ltd")

    # --- match / count ---

    def test_match_unique(self):
        plan = self.plan(
            [("m", "match", {"type": "company", "field": "Headquarters",
                             "value": "Zenith City"})], "m")
        self.assertEqual(execute(plan, self.kb), "Zenith Corp")

    def test_match_case_insensitive(self):
        plan = self.plan(
            [("m", "match", {"type": "company", "field": "Headquarters",
                             "value": "osLO"})], "m")
        self.assertEqual(execute(plan, self.kb), "Northwind Ltd")

    def test_match_list_answer(self):
        plan = self.plan(
            [("m", "match", {"type": "person", "field": "Born", "value": "1980"})], "m")
        self.assertEqual(execute(plan, self.kb), "Anna Petrova, Boris Ivanov")

    def test_match_count(self):
        plan = self.plan(
            [("m", "match", {"type": "person", "field": "Employer",
                             "value": "Northwind Ltd"}),
             ("n", "count", {"from": "m"})], "n")
        self.assertEqual(execute(plan, self.kb), "3")

    def test_match_no_type(self):
        plan = self.plan(
            [("m", "match", {"type": None, "field": "Founded", "value": "2001"})], "m")
        self.assertEqual(execute(plan, self.kb), "Zenith Corp")

    def test_match_empty_is_none(self):
        plan = self.plan(
            [("m", "match", {"type": "person", "field": "Born", "value": "1900"})], "m")
        self.assertIsNone(execute(plan, self.kb))

    # --- неудачи -> None (звонок в fallback) ---

    def test_find_missing_entity(self):
        plan = self.plan(
            [("p", "find", {"name": "Ghost Person", "type": "person"})], "p")
        self.assertIsNone(execute(plan, self.kb))

    def test_get_missing_field(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("f", "get", {"from": "p", "field": "Founded"})], "f")
        self.assertIsNone(execute(plan, self.kb))

    def test_get_from_scalar_value_not_resolvable(self):
        plan = self.plan(
            [("p", "find", {"name": "Anna Petrova", "type": "person"}),
             ("b", "get", {"from": "p", "field": "Born"}),
             ("z", "get", {"from": "b", "field": "Employer"})], "z")
        self.assertIsNone(execute(plan, self.kb))       # "1980" не резолвится

    def test_get_from_list_is_none(self):
        plan = self.plan(
            [("m", "match", {"type": "person", "field": "Born", "value": "1980"}),
             ("x", "get", {"from": "m", "field": "Employer"})], "x")
        self.assertIsNone(execute(plan, self.kb))       # список -> не угадываем


class TypeFilterTest(unittest.TestCase):
    """find с type разбирает коллизию имён между типами."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        files = dict(KB_FILES)
        files["person_northwind.md"] = "# Northwind Ltd\nBorn: 1970\nEmployer: Northwind Ltd\n"
        for fname, content in files.items():
            (Path(tmp.name) / fname).write_text(content, encoding="utf-8")
        self.kb = KnowledgeBase.from_dir(tmp.name)

    plan = staticmethod(make_plan)

    def test_ambiguous_find_without_type(self):
        plan = self.plan([("p", "find", {"name": "Northwind Ltd", "type": None})], "p")
        self.assertIsNone(execute(plan, self.kb))

    def test_type_disambiguates(self):
        plan = self.plan(
            [("c", "find", {"name": "Northwind Ltd", "type": "company"}),
             ("f", "get", {"from": "c", "field": "Founded"})], "f")
        self.assertEqual(execute(plan, self.kb), "1995")


if __name__ == "__main__":
    unittest.main()
