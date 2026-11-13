# -*- coding: utf-8 -*-
"""Тесты solutions/search/planner.py — на фейковом LLM-транспорте, без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.search.test_planner -v
"""

import unittest

import requests

from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.search.v1.planner import PlanOutcome, SearchPlanner, validate_plan
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession

SCHEMA = {"person": ["Born", "Employer", "Mentor"],
           "company": ["Founded", "Headquarters"]}

QUESTION = "In which city is the headquarters of Sergei Garcia's employer?"

VALID_PLAN = """{"steps": [
    {"var": "p", "op": "find", "name": "Sergei Garcia"},
    {"var": "c", "op": "get", "from": "p", "field": "Employer"},
    {"var": "h", "op": "get", "from": "c", "field": "Headquarters"}],
    "answer": "h"}"""


class StubKB:
    """Планировщику от базы нужен только schema()."""

    def __init__(self, schema):
        self._schema = schema

    def schema(self):
        return self._schema


def llm_text(text, prompt=50, completion=20):
    """Ответ LLM с заданным текстом content."""
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


def make_planner(*responses, max_repairs=1):
    session = FakeSession(list(responses))
    client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
    return SearchPlanner(client, max_repairs=max_repairs), session


class HappyPathTest(unittest.TestCase):
    def test_valid_plan(self):
        planner, session = make_planner(llm_text(VALID_PLAN))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsInstance(outcome, PlanOutcome)
        self.assertIsNotNone(outcome.plan)
        self.assertFalse(outcome.unanswerable)
        self.assertIsNone(outcome.error)
        self.assertEqual(len(outcome.plan.steps), 3)
        self.assertEqual(outcome.plan.answer, "h")
        self.assertEqual(outcome.plan.steps[0].args["name"], "Sergei Garcia")
        self.assertEqual(outcome.plan.steps[2].args["field"], "Headquarters")
        self.assertEqual(len(session.calls), 1)          # без ремонта

    def test_prompt_contents(self):
        planner, session = make_planner(llm_text(VALID_PLAN))
        planner.plan(QUESTION, StubKB(SCHEMA))
        sent = session.calls[0]["json"]
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["max_tokens"], 250)
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        messages = sent["messages"]
        self.assertEqual(messages[0]["role"], "system")
        for op in ("find", "get", "match", "count"):
            self.assertIn(f'"{op}"', messages[0]["content"])
        self.assertIn("unanswerable", messages[0]["content"])
        self.assertEqual(messages[1]["role"], "user")
        self.assertIn("person: Born, Employer, Mentor", messages[1]["content"])
        self.assertIn(QUESTION, messages[1]["content"])

    def test_unanswerable(self):
        planner, session = make_planner(llm_text('{"unanswerable": true}'))
        outcome = planner.plan("What color is Sergei's car?", StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertTrue(outcome.unanswerable)
        self.assertIsNone(outcome.error)
        self.assertEqual(len(session.calls), 1)

    def test_unanswerable_in_fenced_json(self):
        planner, _ = make_planner(llm_text('```json\n{"unanswerable": true}\n```'))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertTrue(outcome.unanswerable)

    def test_match_count_plan(self):
        plan_json = ('{"steps": ['
                     '{"var": "m", "op": "match", "type": "person",'
                     ' "field": "Employer", "value": "Fjord Data"},'
                     '{"var": "n", "op": "count", "from": "m"}], "answer": "n"}')
        planner, session = make_planner(llm_text(plan_json))
        outcome = planner.plan("How many people work at Fjord Data?", StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.steps[0].op, "match")
        self.assertEqual(outcome.plan.steps[1].op, "count")
        self.assertEqual(outcome.plan.answer, "n")
        self.assertEqual(len(session.calls), 1)


class RepairTest(unittest.TestCase):
    def test_repair_after_garbage(self):
        planner, session = make_planner(
            llm_text("I think the answer is 42, probably."),
            llm_text(VALID_PLAN),
        )
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(len(session.calls), 2)
        repair_messages = session.calls[1]["json"]["messages"]
        self.assertEqual(len(repair_messages), 4)
        self.assertEqual(repair_messages[2]["role"], "assistant")
        self.assertIn("42", repair_messages[2]["content"])
        self.assertIn("Invalid plan", repair_messages[3]["content"])

    def test_repair_exhausted(self):
        planner, session = make_planner(
            llm_text("garbage"), llm_text("more garbage"),
        )
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertEqual(outcome.error, "response is not a JSON object")
        self.assertEqual(len(session.calls), 2)

    def test_unknown_op_repaired(self):
        bad = VALID_PLAN.replace('"op": "find"', '"op": "search"')
        planner, session = make_planner(llm_text(bad), llm_text(VALID_PLAN))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        repair_user = session.calls[1]["json"]["messages"][3]["content"]
        self.assertIn("unknown op", repair_user)

    def test_unknown_field_repaired(self):
        bad = VALID_PLAN.replace('"field": "Employer"', '"field": "Birthday"')
        planner, session = make_planner(llm_text(bad), llm_text(VALID_PLAN))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        repair_user = session.calls[1]["json"]["messages"][3]["content"]
        self.assertIn("unknown field", repair_user)

    def test_dangling_from(self):
        bad = VALID_PLAN.replace('"from": "p"', '"from": "zz"')
        planner, session = make_planner(llm_text(bad), max_repairs=0)
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertIn("unknown variable", outcome.error)

    def test_answer_unknown_var(self):
        bad = VALID_PLAN.replace('"answer": "h"', '"answer": "zz"')
        planner, _ = make_planner(llm_text(bad), max_repairs=0)
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertIn("must reference", outcome.error)

    def test_count_needs_match(self):
        plan_json = ('{"steps": ['
                     '{"var": "p", "op": "find", "name": "Sergei Garcia"},'
                     '{"var": "b", "op": "get", "from": "p", "field": "Born"},'
                     '{"var": "n", "op": "count", "from": "b"}], "answer": "n"}')
        planner, _ = make_planner(llm_text(plan_json), max_repairs=0)
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertIn("needs a list", outcome.error)


class ValidationAutofixTest(unittest.TestCase):
    """Регистр полей/типов чиним по схеме без ремонтного вызова."""

    def test_field_case_autofixed(self):
        lowered = VALID_PLAN.replace('"field": "Employer"', '"field": "employer"')
        planner, session = make_planner(llm_text(lowered))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(len(session.calls), 1)          # без ремонта
        self.assertEqual(outcome.plan.steps[1].args["field"], "Employer")

    def test_type_case_autofixed(self):
        plan_json = ('{"steps": [{"var": "p", "op": "find",'
                     ' "name": "Sergei Garcia", "type": "Person"}], "answer": "p"}')
        planner, _ = make_planner(llm_text(plan_json))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.steps[0].args["type"], "person")

    def test_match_number_value_coerced(self):
        plan_json = ('{"steps": [{"var": "m", "op": "match", "type": "person",'
                     ' "field": "Born", "value": 1980}], "answer": "m"}')
        planner, _ = make_planner(llm_text(plan_json))
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(outcome.plan.steps[0].args["value"], "1980")

    def test_unknown_type_rejected(self):
        plan_json = ('{"steps": [{"var": "p", "op": "find",'
                     ' "name": "X", "type": "alien"}], "answer": "p"}')
        plan, unanswerable, error = validate_plan(
            __import__("json").loads(plan_json), SCHEMA)
        self.assertIsNone(plan)
        self.assertFalse(unanswerable)
        self.assertIn("unknown type", error)


class FailureTest(unittest.TestCase):
    def test_json_mode_fallback_end_to_end(self):
        """Планировщик переживает «прокси не знает response_format»:
        клиент повторяет без параметра, план строится."""
        planner, session = make_planner(
            FakeResponse(status_code=400, data={"error": "unknown param"}),
            llm_text(VALID_PLAN),
        )
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNotNone(outcome.plan)
        self.assertEqual(len(session.calls), 2)
        self.assertIn("response_format", session.calls[0]["json"])
        self.assertNotIn("response_format", session.calls[1]["json"])

    def test_empty_schema_no_llm_call(self):
        planner, session = make_planner()                # ни одного ответа
        outcome = planner.plan(QUESTION, StubKB({}))
        self.assertIsNone(outcome.plan)
        self.assertIn("schema is empty", outcome.error)
        self.assertEqual(len(session.calls), 0)          # в сеть не пошли

    def test_llm_network_failure(self):
        # chat() делает 1 ретрай на сетевые ошибки -> нужно 2 исключения
        planner, _ = make_planner(
            requests.ConnectionError("down"),
            requests.ConnectionError("down"),
        )
        outcome = planner.plan(QUESTION, StubKB(SCHEMA))
        self.assertIsNone(outcome.plan)
        self.assertIn("LLM call failed", outcome.error)


if __name__ == "__main__":
    unittest.main()
