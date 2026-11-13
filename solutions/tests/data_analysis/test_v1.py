# -*- coding: utf-8 -*-
"""Тесты solutions/data_analysis/v1/solver.py — tmpdir + фейковый LLM, без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.data_analysis.test_v1 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.data_analysis import v1 as v1_pkg
from solutions.data_analysis.v1.solver import ANSWERED, FAILED, UNANSWERABLE, solve
from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession

INSTR = (
    "The file sales.csv has columns order_id,region,product,month,units,price "
    "(3 rows). How many units of product 'widget' were sold in months 7 "
    "through 8 inclusive? Use bash with python3 to compute it. Work only "
    "inside the current directory. When you are done, write ONLY the final "
    "answer to the file answer.txt in the current directory (for example "
    "with bash: echo 42 > answer.txt).")

CSV = ("order_id,region,product,month,units,price\n"
       "1,west,widget,5,5,33\n"
       "2,north,sprocket,6,28,28\n"
       "3,north,widget,8,153,17\n")          # widget 7..8: 153


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


class Env(unittest.TestCase):
    def make_task(self, instruction=INSTR, csv_text=CSV, name="sales.csv",
                  expected="153", check="exact_int"):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "data_analysis_8888"
        ws = task_dir / "workspace"
        ws.mkdir(parents=True)
        if csv_text is not None:
            (ws / name).write_text(csv_text, encoding="utf-8")
        (task_dir / "task.json").write_text(json.dumps({
            "task_id": "data_analysis_8888",
            "task_type": "data_analysis",
            "instruction": instruction,
            "reward": 3,
            "expected": expected,
            "check": check,
        }), encoding="utf-8")
        return Task.from_dir(task_dir)

    def solve_with(self, task, *responses):
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return solve(task, client), session


class HappyPathTest(Env):
    def test_answer_written_to_both_positions(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text("Answer: 153"))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "153")
        self.assertEqual(len(res.written), 2)           # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "153\n")
        self.assertEqual(len(session.calls), 1)

    def test_prompt_has_instruction_and_table(self):
        task = self.make_task()
        _, session = self.solve_with(task, llm_text("Answer: 153"))
        sent = session.calls[0]["json"]
        user = sent["messages"][1]["content"]
        self.assertIn(task.instruction, user)           # инструкция вербатим
        self.assertIn("CSV data from sales.csv", user)
        self.assertIn("order_id,region,product,month,units,price", user)
        self.assertIn("1,west,widget,5,5,33", user)     # строки данных
        self.assertIn("data analyst", sent["messages"][0]["content"])
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)

    def test_no_marker_last_line_used(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text("sum of 153 units.\n153"))
        self.assertEqual(res.answer, "153")

    def test_cost_counts_prompt_at_tenth(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text("Answer: 153", prompt=1000, completion=30))
        self.assertEqual(res.status, ANSWERED)
        self.assertAlmostEqual(res.cost, 30 + 1000 / 10)


class PrecheckGateTest(Env):
    def test_missing_csv_is_unanswerable_without_llm(self):
        task = self.make_task(csv_text=None)
        res, session = self.solve_with(task)            # ответов нет!
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(res.cost, 0.0)
        self.assertEqual(session.calls, [])
        for path in task.answer_candidates:
            self.assertFalse(path.exists())             # файлов нет
        self.assertTrue(any("precheck" in t for t in res.trace))

    def test_declared_column_absent_is_unanswerable(self):
        instr = ("The file sales.csv has columns product,quantity (3 rows). "
                 "What is the total quantity of product 'widget'? "
                 "Write the answer to answer.txt.")
        task = self.make_task(instruction=instr)
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("quantity" in t for t in res.trace))

    def test_empty_csv_is_unanswerable(self):
        task = self.make_task(csv_text="")
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])


class TruncationTest(Env):
    def test_rows_capped_and_noted(self):
        rows = "\n".join(f"{i},west,widget,7,1,10" for i in range(1, 11))
        csv_text = "order_id,region,product,month,units,price\n" + rows + "\n"
        task = self.make_task(csv_text=csv_text)
        old = v1_pkg.solver.MAX_CSV_ROWS
        v1_pkg.solver.MAX_CSV_ROWS = 5
        self.addCleanup(setattr, v1_pkg.solver, "MAX_CSV_ROWS", old)
        res, session = self.solve_with(task, llm_text("Answer: 5"))
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("first 5 of 10 rows", user)
        self.assertTrue(any("truncated" in t for t in res.trace))


class FailureTest(Env):
    def test_no_numeric_answer_fails_without_files(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text("Answer: many"))
        self.assertEqual(res.status, FAILED)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_empty_reply_fails(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text("   "))
        self.assertEqual(res.status, FAILED)

    def test_network_error_fails_cleanly(self):
        import requests

        task = self.make_task()
        res, _ = self.solve_with(
            task, requests.ConnectionError("boom"),
            requests.ConnectionError("boom"))
        self.assertEqual(res.status, FAILED)
        self.assertTrue(any("llm:" in t for t in res.trace))
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_exact_str_verbatim(self):
        task = self.make_task(expected="widget", check="exact_str")
        res, _ = self.solve_with(task, llm_text("Answer: `widget`."))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "widget")


if __name__ == "__main__":
    unittest.main()
