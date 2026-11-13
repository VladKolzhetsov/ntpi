# -*- coding: utf-8 -*-
"""Тесты solutions/math/v1/solver.py — на tmpdir и фейковом LLM, без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.math.test_v1 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.math.v1.solver import ANSWERED, FAILED, solve
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


class Env(unittest.TestCase):
    """tmpdir с задачей math_<id> и нужным task.json."""

    def make_task(self, instruction, check="exact_int"):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "math_9999"
        (task_dir / "workspace").mkdir(parents=True)
        (task_dir / "task.json").write_text(json.dumps({
            "task_id": "math_9999",
            "task_type": "math",
            "instruction": instruction,
            "reward": 1,
            "expected": "389",
            "check": check,
        }), encoding="utf-8")
        return Task.from_dir(task_dir)

    def solve_with(self, task, *responses):
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return solve(task, client), session


class HappyPathTest(Env):
    INSTRUCTION = ("A warehouse starts with 1752 boxes. How many boxes "
                   "does it have at the end? Compute it with the bash tool "
                   "(python3 -c). Write ONLY the final answer to answer.txt.")

    def test_answer_marker(self):
        task = self.make_task(self.INSTRUCTION)
        res, session = self.solve_with(
            task, llm_text("1752 / 2 = 876\nAnswer: 876"))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "876")
        self.assertEqual(len(res.written), 2)             # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "876\n")

    def test_last_answer_marker_wins(self):
        # модель пересчитала - берём последний маркер
        task = self.make_task(self.INSTRUCTION)
        res, session = self.solve_with(
            task, llm_text("Answer: 876\nWait: recheck.\nAnswer: 389"))
        self.assertEqual(res.answer, "389")

    def test_int_cleanup(self):
        # «1,735» и «876 boxes» не должны ломать exact_int
        task = self.make_task(self.INSTRUCTION)
        res, _ = self.solve_with(task, llm_text("Answer: 1,735"))
        self.assertEqual(res.answer, "1735")
        task = self.make_task(self.INSTRUCTION)
        res, _ = self.solve_with(task, llm_text("Answer: 876 boxes"))
        self.assertEqual(res.answer, "876")

    def test_no_marker_takes_last_line(self):
        task = self.make_task(self.INSTRUCTION)
        res, _ = self.solve_with(task, llm_text("Computation done.\n\n876\n"))
        self.assertEqual(res.answer, "876")

    def test_negative_int(self):
        task = self.make_task(self.INSTRUCTION)
        res, _ = self.solve_with(task, llm_text("Answer: -5"))
        self.assertEqual(res.answer, "-5")

    def test_exact_str_kept_verbatim(self):
        task = self.make_task(self.INSTRUCTION, check="exact_str")
        res, _ = self.solve_with(task, llm_text('Answer: "12.5".'))
        self.assertEqual(res.answer, "12.5")

    def test_instruction_passed_verbatim(self):
        task = self.make_task(self.INSTRUCTION)
        _, session = self.solve_with(task, llm_text("Answer: 1"))
        sent = session.calls[0]["json"]
        self.assertEqual(sent["messages"][1]["content"], self.INSTRUCTION)
        self.assertIn("Answer:", sent["messages"][0]["content"])
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)
        self.assertEqual(sent["temperature"], 0.0)


class FailureTest(Env):
    def test_no_int_in_answer(self):
        task = self.make_task("Some instruction")
        res, _ = self.solve_with(task, llm_text("I cannot solve this."))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(res.answer, None)
        for path in (self.tmpdir / "math_9999" / "answer.txt",
                     self.tmpdir / "math_9999" / "workspace" / "answer.txt"):
            self.assertFalse(path.exists())                # файлов нет

    def test_llm_error_fails(self):
        import requests

        class BoomSession(FakeSession):
            def post(self, url, json=None, headers=None, timeout=None):
                self.calls.append({"url": url})
                raise requests.ConnectionError("boom")

        task = self.make_task("Some instruction")
        session = BoomSession([])
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        res = solve(task, client)
        self.assertEqual(res.status, FAILED)
        self.assertFalse(res.written)

    def test_cost_counts_all_llm_calls(self):
        task = self.make_task("Some instruction")
        session = FakeSession([llm_text("Answer: 42", prompt=100, completion=10)])
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        res = solve(task, client)
        self.assertEqual(res.status, ANSWERED)
        self.assertAlmostEqual(res.cost, 10 + 100 / 10)


if __name__ == "__main__":
    unittest.main()
