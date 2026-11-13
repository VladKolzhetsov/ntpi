# -*- coding: utf-8 -*-
"""Тесты solutions/data_analysis/v2/solver.py — tmpdir + фейковый LLM,
код исполняется реальным python3 в сабпроцессе (cwd=workspace).

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.data_analysis.test_v2 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.data_analysis import v2 as v2_pkg
from solutions.data_analysis.v2.solver import (
    ANSWERED, FAILED, UNANSWERABLE, solve,
)
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
       "2,north,widget,8,153,28\n"
       "3,north,gizmo,1,39,17\n")            # widget в месяце 8: 153

CODE_OK = (
    "import csv\n"
    "total = 0\n"
    "with open('sales.csv') as f:\n"
    "    for row in csv.DictReader(f):\n"
    "        if row['product'] == 'widget' and 7 <= int(row['month']) <= 8:\n"
    "            total += int(row['units'])\n"
    "print(total)\n")

CODE_FENCED = ("Here is the program:\n"
               "```python\n" + CODE_OK + "```\n")

CODE_RUNTIME_ERROR = ("import csv\n"
                      "with open('sales.csv') as f:\n"
                      "    rows = list(csv.DictReader(f))\n"
                      "print(1 / 0)\n")

CODE_TIMEOUT = "while True: pass\nprint(1)\n"       # extract_code видит print(

CODE_WRONG_FILE = "print(open('sales.csv').read())\n"   # ок, файл существует


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
    def test_code_reads_csv_and_answers(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text(CODE_OK))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "153")
        self.assertEqual(len(res.written), 2)          # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "153\n")
        self.assertEqual(len(session.calls), 1)        # без ремонта

    def test_fenced_code_extracted(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text(CODE_FENCED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "153")

    def test_prompt_has_profile_not_table(self):
        # суть v2: данные НЕ в контексте — вместо них заголовок и profile
        task = self.make_task()
        _, session = self.solve_with(task, llm_text(CODE_OK))
        sent = session.calls[0]["json"]
        user = sent["messages"][1]["content"]
        self.assertIn(task.instruction, user)          # инструкция вербатим
        self.assertIn("CSV file: sales.csv", user)
        self.assertIn("Header: order_id,region,product,month,units,price", user)
        self.assertIn("product: text", user)           # profile
        self.assertNotIn("1,west,widget,5,5,33", user) # НИ ОДНОЙ строки данных
        self.assertIn("data analyst", sent["messages"][0]["content"])
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)

    def test_big_csv_same_cost_shape(self):
        # 1000 строк не попадают в промпт — стоимость не растёт с файлом
        rows = "\n".join(f"{i},west,widget,7,1,10" for i in range(1, 1001))
        task = self.make_task(csv_text="order_id,region,product,month,units,price\n" + rows + "\n",
                              expected="1000")
        res, session = self.solve_with(task, llm_text(CODE_OK))
        self.assertEqual(res.status, ANSWERED)
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("1000 data rows", user)
        self.assertLess(len(user), 1200)               # промпт компактный

    def test_cost_counts_prompt_at_tenth(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text(CODE_OK, prompt=500, completion=40))
        self.assertAlmostEqual(res.cost, 40 + 500 / 10)


class PrecheckGateTest(Env):
    def test_missing_csv_is_unanswerable_without_llm(self):
        task = self.make_task(csv_text=None)
        res, session = self.solve_with(task)           # ответов нет!
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(res.cost, 0.0)
        self.assertEqual(session.calls, [])
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_declared_column_absent_is_unanswerable(self):
        instr = ("The file sales.csv has columns product,quantity (3 rows). "
                 "What is the total quantity of product 'widget'? "
                 "Write the answer to answer.txt.")
        task = self.make_task(instruction=instr)
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("quantity" in t for t in res.trace))


class UnsolvableTest(Env):
    def test_marker_first_round(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text("UNSOLVABLE\n"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 1)        # без ремонта
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_marker_with_note(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text("UNSOLVABLE: no such product is defined"))
        self.assertEqual(res.status, UNANSWERABLE)

    def test_marker_in_repair(self):
        task = self.make_task()
        res, session = self.solve_with(
            task, llm_text(CODE_RUNTIME_ERROR), llm_text("UNSOLVABLE"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 2)

    def test_code_printing_marker_word_is_not_refusal(self):
        # программа, печатающая слово маркера, — это код, а не отказ
        task = self.make_task(expected="153")
        res, _ = self.solve_with(
            task, llm_text('print("UNSOLVABLE", 153)\n'))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "153")


class DelimiterTest(Env):
    """Ловушка 0036: разделитель ';' не раскрывался в промпте — модель
    парсила файл запятой, падала на KeyError и «чинила» его в уверенно
    неверный '0'. precheck знает диалект (Sniffer) — обязан сообщить его."""

    CSV_SEMI = ("order_id;region;product;month;units;price\n"
                "1;west;bolt;4;12;3\n"
                "2;east;bolt;4;8;3\n")

    def make_semi_task(self):
        return self.make_task(
            instruction="The file sales.csv has columns "
            "order_id,region,product,month,units,price (2 rows). "
            "How many units of product 'bolt' were sold in total? "
            "Use bash with python3 to compute it.",
            csv_text=self.CSV_SEMI, expected="20")

    def test_prompt_discloses_non_comma_delimiter(self):
        _, session = self.solve_with(self.make_semi_task(),
                                     llm_text("print(1)"))
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("(delimiter ';')", user)

    def test_comma_delimiter_not_disclosed(self):
        # запятая — дефолт: шумить примечанием не нужно
        _, session = self.solve_with(self.make_task(), llm_text(CODE_OK))
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("CSV file: sales.csv\n", user)
        self.assertNotIn("delimiter", user)

    def test_semi_program_solves_with_hint(self):
        # программа, следующая подсказке о разделителе, обязана решать
        code = ("import csv\n"
                "total = 0\n"
                "with open('sales.csv', newline='') as f:\n"
                "    for row in csv.DictReader(f, delimiter=';'):\n"
                "        if row['product'] == 'bolt':\n"
                "            total += int(row['units'])\n"
                "print(total)\n")
        task = self.make_semi_task()
        res, _ = self.solve_with(task, llm_text(code))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "20")


class RepairTest(Env):
    def test_runtime_error_repaired(self):
        # 1-й код падает (1/0), 2-й чинит -> 2 вызова
        task = self.make_task()
        res, session = self.solve_with(
            task, llm_text(CODE_RUNTIME_ERROR), llm_text(CODE_OK))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "153")
        self.assertEqual(len(session.calls), 2)

    def test_repair_prompt_contains_stderr_and_code(self):
        task = self.make_task()
        _, session = self.solve_with(
            task, llm_text(CODE_RUNTIME_ERROR), llm_text(CODE_OK))
        repair = session.calls[1]["json"]["messages"]
        self.assertIn("ZeroDivisionError", repair[-1]["content"])
        self.assertIn("1 / 0", repair[-2]["content"])  # свой код в контексте

    def test_double_failure_no_files(self):
        task = self.make_task()
        res, session = self.solve_with(
            task, llm_text(CODE_RUNTIME_ERROR), llm_text(CODE_RUNTIME_ERROR))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 2)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_timeout_is_reported_not_hung(self):
        task = self.make_task()
        old = v2_pkg.solver.CODE_TIMEOUT
        v2_pkg.solver.CODE_TIMEOUT = 0.3
        self.addCleanup(setattr, v2_pkg.solver, "CODE_TIMEOUT", old)
        res, session = self.solve_with(
            task, llm_text(CODE_TIMEOUT), llm_text(CODE_TIMEOUT))
        self.assertEqual(res.status, FAILED)           # 1 + 1 ремонт
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(any("timeout" in t for t in res.trace))


class NullsTest(Env):
    def test_null_rows_survive_execution(self):
        # профиль nan уходит в промпт; пустая ячейка не должна ломать
        # исполнение типичной программы (int('') -> ValueError и т.п.)
        csv_text = ("order_id,region,product,month,units,price\n"
                    "1,west,widget,8,153,33\n"
                    "2,west,widget,8,10,33\n"
                    "3,north,gizmo,1,39,17\n")
        code = ("import csv\n"
                "rows = [r for r in csv.DictReader(open('sales.csv'))\n"
                "        if r['units']]\n"              # nan-скип модели
                "total = sum(int(r['units']) for r in rows\n"
                "           if r['product'] == 'widget')\n"
                "print(total)\n")
        task = self.make_task(csv_text=csv_text, expected="163")
        res, _ = self.solve_with(task, llm_text(code))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "163")

    def test_prompt_contains_null_counts(self):
        csv_text = ("order_id,units\n"
                    "1,10\n"
                    "2,\n")                             # units пусто
        instr = ("The file sales.csv has columns order_id,units (2 rows). "
                 "What is the total units? Write the answer to answer.txt.")
        task = self.make_task(instruction=instr, csv_text=csv_text,
                              expected="10")
        _, session = self.solve_with(task, llm_text("print(10)\n"))
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("nulls 1/2", user)                # nan виден модели


if __name__ == "__main__":
    unittest.main()
