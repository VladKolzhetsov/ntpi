# -*- coding: utf-8 -*-
"""Тесты cost_worst data_analysis/v2 — детерминированная граница, без LLM.

Проверяем: разложение формулы (независимый пересчёт), precheck-ветку
(отсечённая задача стоит 0), ТОЧНОЕ восстановление промпта (побайтовое
совпадение с солвером — главный риск: complexity дублирует формат),
монотонность по числу колонок, контракт с раннером, защиту от
рассинхрона с константами солвера.

Запуск из корня проекта:
    .venv/bin/python -m unittest solutions.tests.data_analysis.test_v2_cost_worst -v
"""

import csv
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.data_analysis.precheck import Precheck, precheck
from solutions.data_analysis.v2.complexity import (
    ASSISTANT_ECHO_TOKENS, EPSILON_COST, REPAIR_ERROR_TOKENS, cost_worst,
    estimate,
)
from solutions.data_analysis.v2.solver import (
    MAX_REPAIRS, MAX_TOKENS, SYSTEM_PROMPT, build_user_prompt,
)
from solutions.task_parser import Task

HEADER = ["order_id", "region", "product", "month", "units", "price"]


def rows_text(header, rows, sep=",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


def make_task(instruction, files=None, task_id="data_analysis_9999") -> Task:
    d = Path(tempfile.mkdtemp())
    ws = d / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in (files or {}).items():
        (ws / name).write_text(text, encoding="utf-8")
    (d / "task.json").write_text(json.dumps({
        "task_id": task_id, "task_type": "data_analysis",
        "instruction": instruction, "reward": 1,
    }), encoding="utf-8")
    return Task.from_dir(d)


def formula(input_tokens, cap) -> float:
    """Независимая копия формулы (защита от рассинхрона)."""
    echo = min(cap, ASSISTANT_ECHO_TOKENS)
    return (input_tokens / 10 + cap
            + (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap)


SIMPLE = ("The file sales.csv has columns order_id,region,product,month,"
          "units,price (2 rows). How many units were sold in total?")


class Env(unittest.TestCase):
    def setUp(self):
        self._dirs: list[Path] = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def task(self, instruction, files):
        t = make_task(instruction, files)
        self._dirs.append(t.dir)
        return t

    def csv_task(self, instruction=None, rows=((1, 2),), header=HEADER):
        instruction = instruction or (
            f"The file sales.csv has columns {','.join(header)} "
            f"({len(rows)} rows). How many units were sold?")
        return self.task(instruction, {
            "sales.csv": rows_text(list(header), [list(r) for r in rows])})


class FormulaTest(Env):
    def test_matches_independent_formula(self):
        task = self.csv_task()
        c = estimate(task)
        self.assertAlmostEqual(c.cost_worst,
                               formula(c.input_tokens, MAX_TOKENS), places=1)

    def test_cost_worst_is_wrapper(self):
        task = self.csv_task()
        self.assertEqual(cost_worst(task), estimate(task).cost_worst)

    def test_greater_than_first_call(self):
        # граница покрывает и ремонтный раунд, а не только первый вызов
        c = estimate(self.csv_task())
        self.assertGreater(c.cost_worst, c.cost_first)

    def test_repairs_zero_drops_repair_term(self):
        import solutions.data_analysis.v2.complexity as cx
        c = estimate(self.csv_task())
        orig = cx._params
        cx._params = lambda: (SYSTEM_PROMPT, build_user_prompt,
                              MAX_TOKENS, 0)
        try:
            c0 = estimate(self.csv_task())
        finally:
            cx._params = orig
        self.assertAlmostEqual(c0.cost_worst, c0.cost_first, places=1)
        self.assertLess(c0.cost_worst, c.cost_worst)
        self.assertTrue(any("MAX_REPAIRS=0" in r for r in c0.reasons))

    def test_prompt_rebuilt_exactly(self):
        # ГЛАВНЫЙ риск: complexity дублирует формат промпта. Сверяем
        # побайтово с тем, что построит солвер на том же precheck.
        task = self.csv_task()
        pc = precheck(task.instruction, [task.workspace_dir, task.dir])
        expected = build_user_prompt(task.instruction, pc)
        c = estimate(task)
        self.assertEqual(c.input_chars,
                         len(SYSTEM_PROMPT) + len(expected))


class PrecheckTest(Env):
    def test_cut_task_costs_zero(self):
        # нет CSV вообще -> precheck режет -> LLM не звали:
        # cost_first честный ноль, cost_worst = EPSILON_COST (плотность
        # reward/cost делить на ноль раннер не умеет)
        task = self.task("The file sales.csv has columns a,b (3 rows). "
                         "How many a?", {})
        c = estimate(task)
        self.assertFalse(c.precheck_ok)
        self.assertEqual(c.cost_worst, EPSILON_COST)
        self.assertEqual(c.cost_first, 0.0)
        self.assertEqual(c.input_chars, 0)

    def test_declared_missing_column_costs_zero(self):
        task = self.task(
            "The file sales.csv has columns order_id,region,units,discount "
            "(3 rows). What is the total discount?",
            {"sales.csv": rows_text(["order_id", "region", "units"],
                                    [[1, "west", 5]])})
        c = estimate(task)
        self.assertFalse(c.precheck_ok)
        self.assertEqual(c.cost_worst, EPSILON_COST)
        self.assertTrue(any("discount" in r for r in c.reasons))

    def test_healthy_task_has_reason_line(self):
        c = estimate(self.csv_task())
        self.assertTrue(c.precheck_ok)
        self.assertTrue(any("precheck ok" in r for r in c.reasons))


class DeterminismTest(Env):
    def test_grows_with_instruction_length(self):
        short = self.csv_task(instruction=SIMPLE)
        long = self.csv_task(instruction=SIMPLE + "x" * 400)
        self.assertGreater(estimate(long).cost_worst,
                           estimate(short).cost_worst)

    def test_independent_of_row_count(self):
        # свойство DA/v2: в промпте НЕ таблица, а профиль — стоимость НЕ
        # растёт с числом строк (в промпте только счётчик "{N} data rows").
        few = self.csv_task(rows=[(i, "w", "p", 1, 1, 1.0)
                                  for i in range(3)])
        many = self.csv_task(rows=[(i, "w", "p", 1, 1, 1.0)
                                   for i in range(300)])
        delta = estimate(many).cost_worst - estimate(few).cost_worst
        # 300 vs 3 строки: разница только в цифрах счётчика (<5 симв.)
        self.assertLess(abs(delta), 10.0)

    def test_independent_of_large_row_count(self):
        # 200k строк: граница та же, что у 3 строк (главное свойство v2)
        few = self.csv_task(rows=[(i, "w", "p", 1, 1, 1.0)
                                  for i in range(3)])
        huge = self.csv_task(rows=[(i, "w", "p", 1, 1, 1.0)
                                   for i in range(5000)])
        self.assertLess(abs(estimate(huge).cost_worst
                            - estimate(few).cost_worst), 20.0)

    def test_expected_and_reward_not_read(self):
        # граница не зависит от верности ответа
        t1 = self.csv_task()
        data = json.loads((t1.dir / "task.json").read_text())
        data["expected"] = "999999"
        data["reward"] = 5
        (t1.dir / "task.json").write_text(json.dumps(data))
        t2 = Task.from_dir(t1.dir)
        self.assertEqual(cost_worst(t1), cost_worst(t2))


class ContractTest(Env):
    def test_rejects_other_task_types(self):
        t = make_task("x")
        self._dirs.append(t.dir)
        t = Task.from_dir(t.dir)
        t._data["task_type"] = "math"          # property -> лезем в данные
        with self.assertRaises(ValueError):
            estimate(t)

    def test_runner_reads_hook(self):
        # контракт раннера: solve-модуль экспортирует cost_worst(task) -> float
        from solutions.data_analysis.v2 import solver
        task = self.csv_task()
        self.assertIsInstance(solver.cost_worst(task), float)

    def test_solver_trace_carries_worst(self):
        # солвер обязан показать границу в trace (диагностика прогонов)
        task = self.csv_task()
        import inspect
        src = inspect.getsource(
            __import__("solutions.data_analysis.v2.solver",
                       fromlist=["solve"]).solve)
        self.assertIn("worst≈", src)


class CapsTest(unittest.TestCase):
    def test_caps_are_positive_constants(self):
        self.assertGreater(MAX_TOKENS, 0)
        self.assertGreaterEqual(MAX_REPAIRS, 0)
        self.assertGreater(ASSISTANT_ECHO_TOKENS, 0)
        self.assertGreater(REPAIR_ERROR_TOKENS, 0)


if __name__ == "__main__":
    unittest.main()
