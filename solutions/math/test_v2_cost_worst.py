# -*- coding: utf-8 -*-
"""Тесты cost_worst math/v2 — детерминированная граница, без LLM и сети.

Проверяем: разложение формулы (независимый пересчёт), инварианты
(граница >= стоимости первого вызова и >= любого фактического сценария
лестницы), монотонность по длине инструкции, контракт с раннером,
защиту от рассинхрона с константами солвера (MAX_TOKENS/MAX_REPAIRS),
чувствительность к отключению ремонта.

Запуск из корня проекта:
    .venv/bin/python -m unittest solutions.math.test_v2_cost_worst -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.math.v2.complexity import (
    ASSISTANT_ECHO_TOKENS, REPAIR_ERROR_TOKENS, cost_worst, estimate,
)
from solutions.math.v2.solver import MAX_REPAIRS, MAX_TOKENS, SYSTEM_PROMPT
from solutions.task_parser import Task


def make_task(instruction, task_id="math_9999", task_type="math") -> Task:
    d = Path(tempfile.mkdtemp())
    # cleanup на уровне класса ниже; здесь — сразу при удалении ссылки
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    (d / "task.json").write_text(json.dumps({
        "task_id": task_id, "task_type": task_type,
        "instruction": instruction, "reward": 1,
    }), encoding="utf-8")
    return Task.from_dir(d)


def formula(input_tokens, cap) -> float:
    """Независимая копия формулы (защита от рассинхрона)."""
    echo = min(cap, ASSISTANT_ECHO_TOKENS)
    c_first = input_tokens / 10 + cap
    c_repair = (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap
    return c_first + c_repair


class CostWorstTest(unittest.TestCase):
    def setUp(self):
        self.tmpdirs: list[Path] = []

    def tearDown(self):
        for d in self.tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

    def task(self, instruction) -> Task:
        t = make_task(instruction)
        self.tmpdirs.append(t.dir)
        return t

    # ---------- формула ----------

    def test_formula_decomposition_matches(self):
        task = self.task("Compute 2 + 2.")
        c = estimate(task)
        self.assertAlmostEqual(
            c.cost_worst, round(formula(c.input_tokens, c.cap), 1))

    def test_input_counts_system_prompt_and_instruction(self):
        task = self.task("X" * 100)
        c = estimate(task)
        self.assertEqual(c.input_chars, len(SYSTEM_PROMPT) + 100)

    def test_worst_at_least_first_call(self):
        c = estimate(self.task("Compute 2 + 2."))
        self.assertGreaterEqual(c.cost_worst, c.cost_first)
        self.assertAlmostEqual(c.cost_first, c.input_tokens / 10 + c.cap, 1)

    def test_worst_is_above_any_observed_scenario(self):
        # два вызова по полному капу + эхо ответа (не выше самого капа) +
        # хвост ошибки — верх любого пути лестницы
        c = estimate(self.task("Compute 2 + 2."))
        echo = min(c.cap, ASSISTANT_ECHO_TOKENS)
        extreme = (c.input_tokens / 10 + MAX_TOKENS) + \
                  ((c.input_tokens + echo + REPAIR_ERROR_TOKENS) / 10
                   + MAX_TOKENS)
        self.assertGreaterEqual(c.cost_worst + 1e-6, extreme)

    # ---------- монотонность и динамика ----------

    def test_worst_grows_with_instruction_length(self):
        small = estimate(self.task("Compute 2 + 2."))
        big = estimate(self.task("Compute 2 + 2. " * 200))
        self.assertGreater(big.cost_worst, small.cost_worst)

    def test_worst_is_dynamic_not_constant(self):
        values = {estimate(self.task("X" * n)).cost_worst
                  for n in (10, 100, 400, 900)}
        self.assertGreater(len(values), 1)

    # ---------- контракт раннера ----------

    def test_public_cost_worst_matches_estimate(self):
        task = self.task("Compute 2 + 2.")
        self.assertEqual(cost_worst(task), estimate(task).cost_worst)

    def test_cost_worst_returns_plain_float(self):
        value = cost_worst(self.task("Compute 2 + 2."))
        self.assertIsInstance(value, float)

    def test_foreign_task_type_rejected(self):
        t = make_task("Who founded X?", task_type="search")
        self.tmpdirs.append(t.dir)
        with self.assertRaises(ValueError):
            estimate(t)

    # ---------- рассинхрон с константами солвера ----------

    def test_cap_mirrors_solver_constant(self):
        self.assertEqual(estimate(self.task("Compute 2 + 2.")).cap, MAX_TOKENS)

    def test_repair_term_dropped_when_repairs_disabled(self):
        task = self.task("Compute 2 + 2.")
        baseline = estimate(task).cost_worst
        original = MAX_REPAIRS
        try:
            import solutions.math.v2.solver as solver
            solver.MAX_REPAIRS = 0
            without = estimate(task).cost_worst
        finally:
            solver.MAX_REPAIRS = original
        self.assertGreater(baseline, without)
        self.assertAlmostEqual(without, estimate(task).cost_first, 0)

    def test_reasons_mention_ladder_and_cap(self):
        c = estimate(self.task("Compute 2 + 2."))
        joined = " ".join(c.reasons)
        self.assertIn("no fallback", joined)
        self.assertIn(str(c.cap), joined)


if __name__ == "__main__":
    unittest.main()
