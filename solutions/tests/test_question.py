# -*- coding: utf-8 -*-
"""Тесты solutions/question.py на всех 8 тренировочных инструкциях.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.test_question -v
"""

import unittest
from pathlib import Path

from solutions.question import extract_question, split_sentences, strip_howto
from solutions.task_parser import Task

PROJECT = Path(__file__).resolve().parents[2]
TRAIN = PROJECT / "tasks" / "task-dir" / "task-dir"


def _instruction(task_id: str) -> str:
    return Task.from_dir(TRAIN / task_id).instruction


class TrainingInstructionsTest(unittest.TestCase):
    """Золотые ответы: чистка всех 8 инструкций тренировочного набора."""

    def test_search_question_is_clean(self):
        self.assertEqual(extract_question(_instruction("search_0001")),
                         "In which year was Irina Smith born?")
        self.assertEqual(extract_question(_instruction("search_0005")),
                         "In which city is the headquarters of Sergei Garcia's employer?")

    def test_math_keeps_narrative(self):
        self.assertEqual(
            extract_question(_instruction("math_0004")),
            "A warehouse starts with 1669 boxes. It ships 36 boxes per day for 3 days. "
            "It receives 2 deliveries of 87 boxes each. How many boxes does it have at the end?")

    def test_math_long_narrative(self):
        q = extract_question(_instruction("math_0000"))
        self.assertTrue(q.startswith("A warehouse starts with 1752 boxes."))
        self.assertTrue(q.endswith("How many boxes does it have at the end?"))
        self.assertNotIn("answer.txt", q)
        self.assertNotIn("Work only", q)

    def test_data_analysis_keeps_columns_context(self):
        # columns-преамбула — контент, не howto: должна сохраниться
        q = extract_question(_instruction("data_analysis_0002"))
        self.assertTrue(q.startswith("The file sales.csv has columns"))
        self.assertTrue(q.endswith("months 7 through 8 inclusive?"))
        self.assertNotIn("Use bash", q)

    def test_coding_keeps_task_content(self):
        self.assertEqual(
            extract_question(_instruction("coding_0003")),
            "Run `python3 test_mylib.py` in the current directory; some tests fail. "
            "Fix the bugs in mylib.py (do not edit the tests) until all tests pass.")


class EdgeCaseTest(unittest.TestCase):
    def test_no_question_mark_returns_stripped(self):
        text = "Do the thing. Work only inside the current directory."
        self.assertEqual(extract_question(text), "Do the thing.")

    def test_empty_and_whitespace(self):
        self.assertEqual(extract_question(""), "")
        self.assertEqual(extract_question("   "), "")

    def test_overzealous_patterns_fall_back(self):
        # все предложения ушли по паттернам -> возвращаем голову как есть
        text = ("Work only inside the current directory. When you are done, "
                "write ONLY the final answer to the file answer.txt.")
        self.assertIn("Work only inside", extract_question(text))

    def test_plain_text_untouched(self):
        text = "Просто вопрос без боилерплейта: сколько будет 2 + 2?"
        self.assertEqual(extract_question(text), text)

    def test_split_sentences(self):
        self.assertEqual(
            split_sentences("Один. Два? Три! Четыре"),
            ["Один.", "Два?", "Три!", "Четыре"])
        self.assertEqual(split_sentences(""), [])


if __name__ == "__main__":
    unittest.main()
