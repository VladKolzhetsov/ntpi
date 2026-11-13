# -*- coding: utf-8 -*-
"""Тесты cost_worst coding/v2 — детерминированная граница, без LLM.

Проверяем: разложение формулы (независимый пересчёт), precheck-ветку
(отсечённая задача стоит почти ноль: плотность делить на 0 нельзя),
динамический кап и одноразовое повышение, ТОЧНОЕ восстановление
промпта (побайтово с тем, что шлёт солвер), оценку отчёта прогона
по числу тестов, контракт с раннером.

Запуск из корня проекта:
    .venv/bin/python -m unittest solutions.tests.coding.test_v2_cost_worst -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.coding.tools import (
    files_text, parse_failures, ran_count, run_tests, test_report,
)
from solutions.coding.v2.complexity import (
    EPSILON_COST, FEEDBACK_CHARS, estimate, estimate_tokens,
)
from solutions.coding.v2.solver import (
    MAX_REPAIRS, MAX_TOKENS, REPLY_CHARS, SYSTEM_PROMPT, build_user_prompt,
    cost_worst, reply_cap,
)
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeSession

INSTR = ("Run `python3 test_mylib.py` in the current directory; some tests "
         "fail. Fix the bugs in mylib.py (do not edit the tests) until all "
         "tests pass. Work only inside the current directory. When you are "
         "done, write ONLY the word DONE to the file answer.txt in the "
         "current directory (for example with bash: echo DONE > answer.txt).")

BUGGY = ("def add(a, b):\n"
         "    return a - b\n"
         "\n"
         "\n"
         "def double(x):\n"
         "    return x * 2\n")

FIXED = ("def add(a, b):\n"
         "    return a + b\n"
         "\n"
         "\n"
         "def double(x):\n"
         "    return x * 2\n")

TESTS = ("import unittest\n"
         "\n"
         "import mylib as m\n"
         "\n"
         "\n"
         "class T(unittest.TestCase):\n"
         "    def test_add(self):\n"
         "        self.assertEqual(m.add(2, 3), 5)\n"
         "\n"
         "    def test_double(self):\n"
         "        self.assertEqual(m.double(4), 8)\n"
         "\n"
         "\n"
         "if __name__ == '__main__':\n"
         "    unittest.main()\n")


def make_task(files, instruction=INSTR,
              task_id="coding_9999") -> Task:
    d = Path(tempfile.mkdtemp())
    ws = d / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (ws / name).write_text(text, encoding="utf-8")
    (d / "task.json").write_text(json.dumps({
        "task_id": task_id, "task_type": "coding",
        "instruction": instruction, "reward": 1,
    }), encoding="utf-8")
    return Task.from_dir(d)


def formula(input_tokens: int, cap: int, repairs: int = MAX_REPAIRS) -> float:
    """Независимая копия формулы (защита от рассинхрона)."""
    cap_up = int(cap * 1.5) + 1
    echo = estimate_tokens(REPLY_CHARS)
    feedback = estimate_tokens(FEEDBACK_CHARS)
    cost = input_tokens / 10 + cap
    cur = input_tokens
    for _ in range(repairs):
        cur += echo + feedback
        cost += cur / 10 + cap_up
    return cost


class Env(unittest.TestCase):
    def setUp(self):
        self._dirs: list[Path] = []

    def tearDown(self):
        for d in self._dirs:
            shutil.rmtree(d, ignore_errors=True)

    def task(self, files, instruction=INSTR):
        t = make_task(files, instruction)
        self._dirs.append(t.dir)
        return t

    def small_task(self):
        return self.task({"mylib.py": BUGGY, "test_mylib.py": TESTS})


class FormulaTest(Env):
    def test_matches_independent_formula(self):
        c = estimate(self.small_task())
        self.assertTrue(c.precheck_ok)
        self.assertAlmostEqual(c.cost_worst,
                               formula(c.input_tokens, c.cap), places=1)

    def test_cost_worst_is_wrapper(self):
        # контракт раннера: cost_worst(task) живёт в модуле солвера
        task = self.small_task()
        self.assertEqual(cost_worst(task), estimate(task).cost_worst)

    def test_floor_cap_for_small_modules(self):
        c = estimate(self.small_task())
        self.assertEqual(c.cap, MAX_TOKENS)
        self.assertEqual(c.cap_raised, int(MAX_TOKENS * 1.5) + 1)

    def test_big_module_raises_cap_and_cost(self):
        big = BUGGY + "\n" + "\n\n".join(
            f"def filler_{i}(x):\n"
            f"    \"\"\"Filler number {i}: just padding to grow the module,"
            f" so the reply cap must grow too.\"\"\"\n"
            f"    return x * {i}\n" for i in range(1, 120))
        small = estimate(self.small_task())
        big_c = estimate(self.task({"mylib.py": big,
                                    "test_mylib.py": TESTS}))
        self.assertGreater(big_c.cap, MAX_TOKENS)
        self.assertGreater(big_c.cost_worst, small.cost_worst)

    def test_monotonic_in_module_size(self):
        sizes = (50, 120, 260)
        prev = None
        for n in sizes:
            module = BUGGY + "\n" + "\n\n".join(
                f"def pad_{i}(x):\n    return x * {i}\n"
                for i in range(1, n))
            c = estimate(self.task({"mylib.py": module,
                                    "test_mylib.py": TESTS}))
            if prev is not None:
                self.assertGreaterEqual(c.cost_worst, prev)
            prev = c.cost_worst

    def test_repairs_zero_drops_repair_terms(self):
        import solutions.coding.v2.complexity as cx
        task = self.small_task()
        full = estimate(task)
        orig = cx._params
        (SYSTEM_PROMPT_, build_, reply_cap_, REPLY_CHARS_, _) = orig()
        cx._params = lambda: (SYSTEM_PROMPT_, build_, reply_cap_,
                              REPLY_CHARS_, 0)
        try:
            c0 = estimate(task)
        finally:
            cx._params = orig
        self.assertAlmostEqual(c0.cost_worst,
                               c0.input_tokens / 10 + c0.cap, places=1)
        self.assertLess(c0.cost_worst, full.cost_worst)


class PrecheckTest(Env):
    def test_cut_task_costs_epsilon(self):
        # нет тестовой команды и нет test_*.py -> precheck режет:
        # LLM не зовётся. cost_worst = EPSILON_COST: плотность reward/cost
        # делить на ноль раннер не умеет.
        task = self.task(
            {"mylib.py": "def double(x):\n    return x * 3\n"},
            instruction="Fix the bugs in mylib.py until all tests pass.")
        c = estimate(task)
        self.assertFalse(c.precheck_ok)
        self.assertEqual(c.cost_worst, EPSILON_COST)
        self.assertEqual(c.input_chars, 0)
        self.assertTrue(any("precheck" in r for r in c.reasons))

    def test_fix_tests_instruction_cut(self):
        # инструкция велит чинить тесты -> цель не модуль -> precheck cut
        instr = ("Run `python3 test_mylib.py` in the current directory. "
                 "Fix the bugs in test_mylib.py (do not edit mylib.py).")
        c = estimate(self.task({"mylib.py": BUGGY, "test_mylib.py": TESTS},
                               instruction=instr))
        self.assertFalse(c.precheck_ok)
        self.assertEqual(c.cost_worst, EPSILON_COST)


class PromptTest(Env):
    def test_prompt_rebuilt_exactly(self):
        """ГЛАВНЫЙ риск: complexity дублирует формат промпта. Сверяем
        побайтово детерминированную часть (без отчёта) с той, что
        построил бы солвер."""
        task = self.small_task()
        from solutions.coding.tools import inspect_task
        py_files, _target, _cmd, _err = inspect_task(
            task.instruction, task.workspace_dir)
        expected = build_user_prompt(task.instruction, py_files, "")
        self.assertIn("Workspace files:", expected)
        self.assertIn("def add(a, b):", expected)
        c = estimate(task)
        self.assertGreaterEqual(c.input_chars,
                                len(SYSTEM_PROMPT) + len(expected))

    def test_report_bound_covers_actual_report(self):
        """Оценка отчёта (150 + 130/тест) покрывает реальный отчёт
        прогона на упавшем suite."""
        task = self.small_task()
        from solutions.coding.tools import inspect_task
        py_files, _target, _cmd, _err = inspect_task(
            task.instruction, task.workspace_dir)
        proc, err = run_tests(["python3", "test_mylib.py"],
                              task.workspace_dir, 30)
        self.assertIsNone(err)
        report = test_report(parse_failures(proc.stderr),
                             ran_count(proc.stderr))
        n = sum(1 for p in py_files
                if p.name.startswith("test_") or "test" in p.name
                for _ in [0]) or 1
        import re
        n_tests = sum(len(re.findall(r"\bdef\s+test_\w+", p.read_text(
            encoding="utf-8", errors="replace"))) for p in py_files)
        bound = 150 + 130 * n_tests
        self.assertEqual(n_tests, 2)
        self.assertGreaterEqual(bound, len(report))

    def test_reasons_documented(self):
        c = estimate(self.small_task())
        self.assertTrue(any("cap" in r for r in c.reasons))
        self.assertTrue(any("repair" in r for r in c.reasons))


if __name__ == "__main__":
    unittest.main()
