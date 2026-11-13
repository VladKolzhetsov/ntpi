# -*- coding: utf-8 -*-
"""Тесты solutions/math/v2/detectors.py — чистые функции, без LLM и сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.math.test_v2_detectors -v
"""

import unittest

from solutions.math.v2.detectors import (
    DETECTORS, _question_bounds, detect_unsolvable, question_noun_absent,
)

TRAP = ("A warehouse starts with 1752 boxes. It ships 36 boxes per day "
        "for 3 days. How many crates does it have at the end? Compute it "
        "with the bash tool (python3 -c). Write ONLY the final answer to "
        "the file answer.txt.")

SOLVABLE = ("A warehouse starts with 1752 boxes. It ships 36 boxes per day "
            "for 3 days. How many boxes does it have at the end? Compute "
            "it with the bash tool (python3 -c).")


class QuestionBoundsTest(unittest.TestCase):
    def test_last_question_sentence(self):
        question, start, end = _question_bounds(TRAP)
        self.assertEqual(question, "How many crates does it have at the end?")
        self.assertEqual(TRAP[start:end], question)

    def test_no_question_mark(self):
        question, start, end = _question_bounds("Compute 2 + 2.")
        self.assertEqual(question, "")
        self.assertEqual((start, end), (len("Compute 2 + 2."),) * 2)


class QuestionNounAbsentTest(unittest.TestCase):
    def test_trap_detected(self):
        reason = question_noun_absent(TRAP)
        self.assertIsNotNone(reason)
        self.assertIn("crates", reason)

    def test_same_noun_passes(self):
        self.assertIsNone(question_noun_absent(SOLVABLE))

    def test_stopwords_only_passes(self):
        # нет явных сущностей — судить не о чем
        text = ("There are 1752 boxes. It ships some. How much is left "
                "at the end in total?")
        self.assertIsNone(question_noun_absent(text))

    def test_decoy_noun_passes(self):
        # «crates» нет в условии, но «warehouse» из вопроса есть ->
        # консервативно пропускаем (ловушку добьёт маркер UNSOLVABLE)
        text = ("A warehouse starts with 1752 boxes. How many crates does "
                "the warehouse have?")
        self.assertIsNone(question_noun_absent(text))

    def test_question_only_task_passes(self):
        # вопрос = первая же фраза: подменять сущность не из чего
        # (баг пойман на math_0015/0016/0038 — ложные срабатывания)
        text = ("How many different 5-card hands can be dealt from a "
                "standard deck of 52 distinct cards? Compute it with the "
                "bash tool (python3 -c). Write ONLY the final answer to "
                "the file answer.txt.")
        self.assertIsNone(question_noun_absent(text))

    def test_question_only_trap_goes_to_marker(self):
        # самодостаточный вопрос-ловушка (данные не даны) — не наш фильтр,
        # его ловит маркер UNSOLVABLE у LLM
        text = ("A warehouse stores 1752 boxes and several bags. How many "
                "bags does the warehouse have after the inspection?")
        self.assertIsNone(question_noun_absent(text))

    def test_plural_stemming(self):
        text = ("An orchard starts with 120 apple. It sells 30 per day. "
                "How many apples are left?")
        self.assertIsNone(question_noun_absent(text))

    def test_unit_alias(self):
        text = ("A car travels for 5 hours at 90 km per hour. "
                "How many kilometers does it cover?")
        self.assertIsNone(question_noun_absent(text))

    def test_no_question_passes(self):
        self.assertIsNone(question_noun_absent("Compute 1752 // 2."))


class RegistryTest(unittest.TestCase):
    def test_all_detectors_registered(self):
        self.assertIn(question_noun_absent, DETECTORS)

    def test_detect_unsolvable_delegates(self):
        self.assertIsNotNone(detect_unsolvable(TRAP))
        self.assertIsNone(detect_unsolvable(SOLVABLE))

    def test_custom_detector_list(self):
        # отключение фильтра = пустой список, солвер звонить не будет
        self.assertIsNone(detect_unsolvable(TRAP, detectors=[]))

    def test_reason_is_first_hit(self):
        def always(instr):
            return "always"
        self.assertEqual(detect_unsolvable(SOLVABLE, detectors=[always]),
                         "always")


if __name__ == "__main__":
    unittest.main()
