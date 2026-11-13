"""Структурные ловушки бесплатного предиктора; вызовов LLM нет."""

import unittest
from pathlib import Path

from solutions.search.v1.complexity import estimate
from solutions.search.v1.kb import KnowledgeBase, Node


class TestComplexity(unittest.TestCase):
    schema = {
        "person": ["Born", "Employer", "Father"],
        "company": ["Headquarters"],
        "country": ["President"],
    }

    def test_direct_and_multihop(self):
        for question, steps in (
            ("In which year was Irina Smith born?", 2),
            ("In which city is Irina Smith's employer headquartered?", 3),
            ("Who is the father of the President of Tralivali's father?", 4),
        ):
            with self.subTest(question=question):
                self.assertEqual(estimate(question, self.schema).predicted_steps, steps)

    def test_match_is_one_step_not_two(self):
        result = estimate("Which companies are headquartered in Osaka?", self.schema)
        self.assertEqual((result.strategy, result.predicted_steps), ("match", 1))

    def test_match_count(self):
        result = estimate("How many people were born in 1983?", self.schema)
        self.assertEqual((result.strategy, result.predicted_steps), ("match_count", 2))

    def test_context_does_not_add_employer_step(self):
        result = estimate("Irina Petrov works for Fjord Data. In which year was Irina Petrov born?",
                          self.schema)
        self.assertEqual(result.fields, ["Born"])
        self.assertEqual(result.predicted_steps, 2)

    def test_entity_names_are_not_relations(self):
        kb = KnowledgeBase([Node("Father Brown", "person", {"Born": "1980"},
                                 "", Path("person_father_brown.md"))], files_seen=1)
        result = estimate("When was Father Brown born?", self.schema, kb=kb)
        self.assertEqual(result.fields, ["Born"])

    def test_longest_field_match(self):
        result = estimate("What is the birth year of Anna?",
                          {"person": ["Birth", "Birth year", "Year"]})
        self.assertEqual(len(result.fields), 1)
        self.assertLessEqual(result.confidence, 0.4)

    def test_unknown_question_is_not_confident(self):
        result = estimate("Who succeeded Anna?", self.schema)
        self.assertEqual(result.strategy, "unknown")
        self.assertLess(result.confidence, 0.5)

    def test_repeated_relations_and_extrapolation(self):
        result = estimate("Who is " + "the father of " * 50 + "Anna?", self.schema)
        self.assertEqual(result.predicted_steps, 51)
        self.assertTrue(result.extrapolated)
        self.assertLess(result.cost_low, result.predicted_cost)
        self.assertGreater(result.cost_high, result.predicted_cost)

    def test_implicit_repetition_is_flagged(self):
        result = estimate("Follow Father 50 times from Anna. Who is reached?", self.schema)
        self.assertLess(result.confidence, 0.5)

    def test_empty_kb_not_confident(self):
        result = estimate("When was Anna born?", {}, kb=KnowledgeBase([]))
        self.assertLessEqual(result.confidence, 0.1)


if __name__ == "__main__":
    unittest.main()
