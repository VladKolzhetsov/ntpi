# -*- coding: utf-8 -*-
"""E2E-тесты solutions/search/solver.py — на tmpdir и фейковом LLM, без сети.

Каждый тест собирает мини-задачу (task.json + workspace/kb/*.md), прогоняет
solve() с запрограммированными ответами FakeSession и проверяет answer.txt.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.search.test_solver -v
"""

import json
import unittest
from pathlib import Path

from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.search.v1 import solver
from solutions.search.v1.solver import (
    ANSWERED, FAILED, UNANSWERABLE, solve,
)
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession

QUESTION = "In which city is the headquarters of Sergei Garcia's employer?"

# валидный план: find Sergei Garcia -> get Employer -> get Headquarters
PLAN_OK = ('{"steps": ['
           '{"var": "p", "op": "find", "name": "Sergei Garcia"},'
           '{"var": "e", "op": "get", "from": "p", "field": "Employer"},'
           '{"var": "h", "op": "get", "from": "e", "field": "Headquarters"}],'
           ' "answer": "h"}')


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


def bad_plan(prompt=50, completion=20):
    """Мусор вместо плана: дважды -> планировщик сдаётся, ходит fallback."""
    return llm_text("I cannot produce a plan for this.", prompt, completion)


class SolverEnv:
    """Мини-задача в tmpdir: task.json + workspace/kb с заданными карточками."""

    def __init__(self, test: unittest.TestCase, cards: dict[str, str] | None = None,
                 instruction: str | None = None):
        self.root = Path(test.tmpdir)
        self.task_dir = self.root / "search_9999"
        ws = self.task_dir / "workspace" / "kb"
        ws.mkdir(parents=True)
        for fname, content in (cards or {}).items():
            (ws / fname).write_text(content, encoding="utf-8")
        (self.task_dir / "task.json").write_text(json.dumps({
            "task_id": "search_9999",
            "task_type": "search",
            "instruction": instruction or f"{QUESTION} Write ONLY the final "
                                           "answer to the file answer.txt.",
            "reward": 1,
            "expected": "Osaka",
        }), encoding="utf-8")
        self.task = Task.from_dir(self.task_dir)

    def answers(self) -> list[Path]:
        return [p for p in (
            self.task_dir / "answer.txt",
            self.task_dir / "workspace" / "answer.txt",
        ) if p.exists()]


HEALTHY_CARDS = {
    "person_sergei_garcia.md":
        "# Sergei Garcia\n- Born: 1985\n- Employer: Fjord Data\n",
    "person_irina_sato.md":
        "# Irina Sato\n- Born: 1983\n- Employer: Fjord Data\n",
    "company_fjord_data.md":
        "# Fjord Data\n- Headquarters: Osaka\n- Founded: 2011\n",
}


class HappyPathTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, True))

    def _solve(self, *responses, cards=None, instruction=None):
        env = SolverEnv(self, cards if cards is not None else HEALTHY_CARDS,
                        instruction)
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return solve(env.task, client), env, session

    def test_plan_execute_answer_written(self):
        res, env, session = self._solve(llm_text(PLAN_OK))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "Osaka")
        self.assertEqual(len(res.written), 2)             # ОБЕ позиции
        for path in env.answers():
            self.assertEqual(path.read_text(encoding="utf-8"), "Osaka\n")
        self.assertEqual(len(session.calls), 1)           # только планировщик
        self.assertTrue(res.ok)
        self.assertGreater(res.cost, 0)
        self.assertTrue(any("plan:" in t for t in res.trace))

    def test_unanswerable_no_files(self):
        res, env, session = self._solve(
            llm_text('{"unanswerable": true}'))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(env.answers(), [])               # файлов нет
        self.assertFalse(res.written)
        self.assertEqual(len(session.calls), 1)

    def test_bad_plan_falls_back_to_qa(self):
        res, env, session = self._solve(
            bad_plan(), bad_plan(), llm_text("Osaka"))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "Osaka")
        self.assertEqual(len(res.written), 2)
        self.assertEqual(len(session.calls), 3)           # 2 плана + 1 QA
        # в QA-промпт ушёл дамп базы и маркер NOT_FOUND
        qa = session.calls[2]["json"]["messages"]
        self.assertIn("NOT_FOUND", qa[0]["content"])
        self.assertIn("Sergei Garcia", qa[1]["content"])
        self.assertIn("Headquarters=Osaka", qa[1]["content"])

    def test_executor_none_falls_back_to_qa(self):
        # план ссылается на несуществующую сущность -> executor None -> QA
        bad_link = ('{"steps": ['
                    '{"var": "p", "op": "find", "name": "Nobody Here"},'
                    '{"var": "b", "op": "get", "from": "p", "field": "Born"}],'
                    ' "answer": "b"}')
        res, env, session = self._solve(llm_text(bad_link), llm_text("1983"))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "1983")
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(any("executor:" in t for t in res.trace))

    def test_qa_not_found_gives_up(self):
        res, env, session = self._solve(
            bad_plan(), bad_plan(), llm_text("NOT_FOUND"))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(env.answers(), [])
        self.assertEqual(res.answer, None)
        self.assertTrue(any("fallback:" in t for t in res.trace))

    def test_unhealthy_kb_skips_planner(self):
        # < 50% файлов распарсились -> is_healthy False -> сразу QA, 1 вызов
        cards = {
            "person_sergei_garcia.md": HEALTHY_CARDS["person_sergei_garcia.md"],
            "company_fjord_data.md": HEALTHY_CARDS["company_fjord_data.md"],
            "broken_1.md": "total garbage without any colon pairs here",
            "broken_2.md": "another garbage file, nothing to parse",
            "broken_3.md": "yet one more unparsable line",
        }
        res, env, session = self._solve(llm_text("Osaka"), cards=cards)
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "Osaka")
        self.assertEqual(len(session.calls), 1)           # только QA
        self.assertFalse(any("plan" in t for t in res.trace))

    def test_qa_empty_answer_gives_up(self):
        res, env, session = self._solve(
            bad_plan(), bad_plan(), llm_text("   "))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(env.answers(), [])

    def test_kb_too_big_gives_up_after_planner(self):
        # порог ограничивает только fallback-дамп; планировщик (O(1) от
        # размера базы) отрабатывает, executor/план падает -> too big -> сдача
        cards = {f"company_bulk_{i}.md":
                 f"# Bulk {i}\n- Headquarters: City {i}\n"
                 for i in range(solver.MAX_FALLBACK_CARDS + 5)}
        res, env, session = self._solve(
            bad_plan(), bad_plan(), cards=cards)
        self.assertEqual(res.status, FAILED)
        self.assertEqual(env.answers(), [])
        self.assertEqual(len(session.calls), 2)           # 2 попытки плана, без QA
        self.assertTrue(any("too big" in t for t in res.trace))

    def test_empty_kb_fails_without_llm(self):
        res, env, session = self._solve(cards={})
        self.assertEqual(res.status, FAILED)
        self.assertEqual(env.answers(), [])
        self.assertEqual(len(session.calls), 0)

    def test_qa_answer_punctuation_stripped(self):
        # модель ответила с кавычками и точкой — чистим до чистого значения
        res, env, session = self._solve(
            bad_plan(), bad_plan(), llm_text('"Osaka."'))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "Osaka")
        for path in env.answers():
            self.assertEqual(path.read_text(encoding="utf-8"), "Osaka\n")


class CostTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, True))

    def test_cost_counts_all_llm_calls(self):
        env = SolverEnv(self, HEALTHY_CARDS)
        session = FakeSession([
            bad_plan(prompt=100, completion=10),
            bad_plan(prompt=300, completion=10),
            llm_text("Osaka", prompt=500, completion=10),
        ])
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        res = solve(env.task, client)
        self.assertEqual(res.status, ANSWERED)
        # spent = sum(completion) + sum(prompt)/10 = 30 + 90
        self.assertAlmostEqual(res.cost, 30 + 900 / 10)


if __name__ == "__main__":
    unittest.main()
