"""Регрессии search/v2: реальные цепочки и неоднозначность графа."""

import unittest
from pathlib import Path
from unittest.mock import patch

from solutions.llm import LLMClient
from solutions.search.v2.complexity import estimate, estimate_task
from solutions.search.v2.kb import KnowledgeBase, Node
from solutions.search.v2.planner import SearchPlanner
from solutions.task_parser import Task
from solutions.tests.search.test_planner import llm_text, StubKB, SCHEMA, VALID_PLAN
from solutions.tests.test_llm_fakes import FakeSession


ROOT = Path(__file__).resolve().parents[2]


def kb_nodes(*records):
    return KnowledgeBase([Node(name, "node", attrs, "", Path(f"node_{name}.md"))
                          for name, attrs in records], files_seen=len(records))


class V2Test(unittest.TestCase):
    def test_unmeasured_depth_marked_as_interpolation(self):
        result = estimate("Who is " + "the father of " * 7 + "Anna?",
                          {"person": ["Father"]})
        self.assertEqual(result.predicted_steps, 8)
        self.assertFalse(result.extrapolated)
        self.assertLessEqual(result.confidence, 0.5)
        self.assertTrue(any("interpolated depth" in r for r in result.reasons))

    def test_training_depth_and_actual_relations(self):
        for task_id, fields in (
            ("search_0032", ["President", "Father", "Father"]),
            ("search_0033", ["Capital", "Main road", "Material", "Type"]),
            ("search_0034", ["Quadrant", "Planetary system", "Planet", "Continent",
                             "Country", "Region", "City", "Sector", "House", "Floor",
                             "Apartment", "Resident", "Arrived from"]),
        ):
            with self.subTest(task=task_id):
                task = Task.from_dir(ROOT / "tasks/task-dir/task-dir" / task_id)
                result = estimate_task(task)
                self.assertEqual(result.predicted_steps, len(fields) + 1)
                self.assertEqual(result.relation_path, fields)
                self.assertEqual(result.graph_status, "unique")
                self.assertCountEqual(result.fields, fields)

    def test_repeated_edge_cycle_is_finite(self):
        kb = kb_nodes(("Anna", {"Father": "Anna"}))
        result = estimate("Who is Anna's father's father?", kb.schema(), kb=kb)
        self.assertEqual(result.relation_path, ["Father", "Father"])
        self.assertEqual(result.predicted_steps, 3)

    def test_two_orders_are_ambiguous(self):
        kb = kb_nodes(("Anna", {"Father": "Ben", "Mentor": "Cora"}),
                      ("Ben", {"Mentor": "Dan"}), ("Cora", {"Father": "Eve"}))
        result = estimate("Who is Anna's father's mentor?", kb.schema(), kb=kb)
        self.assertEqual(result.graph_status, "ambiguous")
        self.assertEqual(result.relation_path, [])
        self.assertLessEqual(result.confidence, 0.3)

    def test_broken_chain_does_not_invent_path(self):
        kb = kb_nodes(("Anna", {"Father": "Missing"}), ("Ben", {"Mentor": "Dan"}))
        result = estimate("Who is Anna's father's mentor?", kb.schema(), kb=kb)
        self.assertEqual(result.graph_status, "no_path")

    def test_graph_work_is_bounded(self):
        kb = kb_nodes(("Anna", {"Father": "Anna"}))
        with patch("solutions.search.v2.complexity.MAX_GRAPH_STATES", 1):
            result = estimate("Who is Anna's father's father?", kb.schema(), kb=kb)
        self.assertEqual(result.graph_status, "limit")

    def test_main_and_primary_alias_collision(self):
        result = estimate("What is the road material of Anna?",
                          {"node": ["Main road", "Primary road", "Material"]})
        self.assertLessEqual(result.confidence, 0.4)
        self.assertTrue(any("ambiguous field" in r for r in result.reasons))

    def test_reverse_lookup_preserved(self):
        result = estimate("Which companies are headquartered in Osaka?",
                          {"company": ["Headquarters"]})
        self.assertEqual((result.strategy, result.predicted_steps), ("match", 1))

    def test_compact_prompt_and_cap_including_repair(self):
        session = FakeSession([llm_text("broken"), llm_text(VALID_PLAN)])
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        result = SearchPlanner(client).plan("Where is Anna's employer headquartered?", StubKB(SCHEMA))
        self.assertIsNotNone(result.plan)
        self.assertEqual(len(session.calls), 2)
        for call in session.calls:
            payload = call["json"]
            self.assertEqual(payload["max_tokens"], 400)
            self.assertIn("no spaces outside strings", payload["messages"][0]["content"])
        self.assertIn("compact single-line", session.calls[1]["json"]["messages"][-1]["content"])


if __name__ == "__main__":
    unittest.main()
