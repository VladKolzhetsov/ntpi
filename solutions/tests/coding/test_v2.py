# -*- coding: utf-8 -*-
"""Тесты solutions/coding/v2/solver.py — прегон тестов, ремонтный цикл,
регрессионный фидбэк и детектор осцилляции. Тесты задачи исполняются
реальным python3 в сабпроцессе (cwd=workspace).

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.coding.test_v2 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import solutions.coding.v2.solver as v2solver
from solutions.coding.v2.solver import ANSWERED, FAILED, UNANSWERABLE, solve
from solutions.coding.tools import parse_failures, ran_count, test_report
from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession

INSTR = ("Run `python3 test_mylib.py` in the current directory; some tests "
         "fail. Fix the bugs in mylib.py (do not edit the tests) until all "
         "tests pass. Work only inside the current directory. When you are "
         "done, write ONLY the word DONE to the file answer.txt in the "
         "current directory (for example with bash: echo DONE > answer.txt).")

BUGGY = ("def add(a, b):\n"
         "    return a - b\n"                 # баг
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

# --- Ловушка взаимозависимых функций: g компенсирует баг f ---
# initial: test_f падает (8 != 6), test_g проходит (8-2=6)
TRAP_BUGGY = ("def f(x):\n"
              "    return x * 2 + 2\n"         # баг
              "\n"
              "\n"
              "def g(x):\n"
              "    return f(x) - 2\n")        # компенсация бага

# наивный фикс: f чинится, g остаётся -> test_g ЛОМАЕТСЯ (4 != 6)
TRAP_NAIVE = ("def f(x):\n"
              "    return x * 2\n"
              "\n"
              "\n"
              "def g(x):\n"
              "    return f(x) - 2\n")

# согласованный фикс: обе функции
TRAP_FIXED = ("def f(x):\n"
              "    return x * 2\n"
              "\n"
              "\n"
              "def g(x):\n"
              "    return f(x)\n")

TRAP_TESTS = ("import unittest\n"
              "\n"
              "import mylib as m\n"
              "\n"
              "\n"
              "class T(unittest.TestCase):\n"
              "    def test_f(self):\n"
              "        self.assertEqual(m.f(3), 6)\n"
              "\n"
              "    def test_g(self):\n"
              "        self.assertEqual(m.g(3), 6)\n"
              "\n"
              "\n"
              "if __name__ == '__main__':\n"
              "    unittest.main()\n")

# --- Заготовка на 3 функции: каждый раунд — НОВОЕ множество фейлов ---
TRIPLE_BUGGY = ("def f(x):\n"
                "    return x - 1\n"          # баг (должно быть x + 1)
                "\n"
                "\n"
                "def g(x):\n"
                "    return x * 2\n"
                "\n"
                "\n"
                "def h(x):\n"
                "    return x + 3\n")

TRIPLE_R0 = ("def f(x):\n"        # f чинится, g ломается
             "    return x + 1\n"
             "\n"
             "\n"
             "def g(x):\n"
             "    return x / 2\n"
             "\n"
             "\n"
             "def h(x):\n"
             "    return x + 3\n")

TRIPLE_R1 = ("def f(x):\n"        # g чинится, h ломается
             "    return x + 1\n"
             "\n"
             "\n"
             "def g(x):\n"
             "    return x * 2\n"
             "\n"
             "\n"
             "def h(x):\n"
             "    return x - 3\n")

TRIPLE_R2 = ("def f(x):\n"        # f и g ломаются
             "    return x - 1\n"
             "\n"
             "\n"
             "def g(x):\n"
             "    return x / 2\n"
             "\n"
             "\n"
             "def h(x):\n"
             "    return x + 3\n")

TRIPLE_TESTS = ("import unittest\n"
                "\n"
                "import mylib as m\n"
                "\n"
                "\n"
                "class T(unittest.TestCase):\n"
                "    def test_f(self):\n"
                "        self.assertEqual(m.f(4), 5)\n"
                "\n"
                "    def test_g(self):\n"
                "        self.assertEqual(m.g(3), 6)\n"
                "\n"
                "    def test_h(self):\n"
                "        self.assertEqual(m.h(2), 5)\n"
                "\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n")

STDERR = """FF.
======================================================================
FAIL: test_add (test_mylib.T.test_add)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/x/test_mylib.py", line 8, in test_add
    self.assertEqual(m.add(2, 3), 5)
AssertionError: -1 != 5

======================================================================
FAIL: test_double (test_mylib.T.test_double)
----------------------------------------------------------------------
Traceback (most recent call last):
  File "/tmp/x/test_mylib.py", line 11, in test_double
    self.assertEqual(m.double(4), 8)
AssertionError: 8 != 9

----------------------------------------------------------------------
Ran 2 tests in 0.001s

FAILED (failures=2)
"""


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


class Env(unittest.TestCase):
    def make_task(self, instruction=INSTR, mylib=BUGGY, tests=TESTS):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "coding_8888"
        ws = task_dir / "workspace"
        ws.mkdir(parents=True)
        if mylib is not None:
            (ws / "mylib.py").write_text(mylib, encoding="utf-8")
        if tests is not None:
            (ws / "test_mylib.py").write_text(tests, encoding="utf-8")
        (task_dir / "task.json").write_text(json.dumps({
            "task_id": "coding_8888",
            "task_type": "coding",
            "instruction": instruction,
            "reward": 2,
            "expected": "tests_pass",
            "check": "unittest",
        }), encoding="utf-8")
        return Task.from_dir(task_dir)

    def solve_with(self, task, *responses):
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return solve(task, client), session


class FastPathTest(Env):
    def test_tests_already_pass_done_zero_tokens(self):
        # «баги» уже не воспроизводятся: DONE до первого токена
        task = self.make_task(mylib=FIXED)
        res, session = self.solve_with(task)          # ответов нет!
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "DONE")
        self.assertEqual(res.cost, 0.0)
        self.assertEqual(len(res.written), 2)         # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "DONE\n")
        self.assertEqual(session.calls, [])
        self.assertTrue(any("already pass" in t for t in res.trace))


class HappyPathTest(Env):
    def test_fixed_first_round(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "DONE")
        self.assertEqual(len(res.written), 2)
        ws_lib = task.workspace_dir / "mylib.py"
        self.assertEqual(ws_lib.read_text(encoding="utf-8").rstrip("\n"),
                         FIXED.rstrip("\n"))
        # тесты не тронуты
        self.assertEqual((task.workspace_dir / "test_mylib.py")
                         .read_text(encoding="utf-8"), TESTS)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(any("round0: tests OK" in t for t in res.trace))

    def test_prompt_contains_test_report(self):
        # главное отличие v2: модель видит, ЧТО падает, до вызова
        task = self.make_task()
        _, session = self.solve_with(task, llm_text(FIXED))
        sent = session.calls[0]["json"]
        user = sent["messages"][1]["content"]
        self.assertIn(task.instruction, user)          # инструкция вербатим
        self.assertIn("--- mylib.py ---", user)
        self.assertIn("--- test_mylib.py ---", user)
        self.assertIn("return a - b", user)            # исходник в контексте
        self.assertIn("Test run output: 1 of 2 tests pass, 1 fail:", user)
        self.assertIn("- test_add: AssertionError:", user)
        self.assertIn("ALL tests pass", sent["messages"][0]["content"])
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)

    def test_cost_counts_prompt_at_tenth(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text(FIXED, prompt=800, completion=100))
        self.assertAlmostEqual(res.cost, 100 + 800 / 10)


class RepairTest(Env):
    def test_regression_feedback_then_success(self):
        # ловушка: наивный фикc чинит f и ЛОМАЕТ проходивший test_g
        task = self.make_task(mylib=TRAP_BUGGY, tests=TRAP_TESTS)
        res, session = self.solve_with(task, llm_text(TRAP_NAIVE),
                                       llm_text(TRAP_FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(len(session.calls), 2)
        # mylib = согласованный фикс
        self.assertEqual((task.workspace_dir / "mylib.py")
                         .read_text(encoding="utf-8").rstrip("\n"),
                         TRAP_FIXED.rstrip("\n"))
        # трейс фиксирует регрессию раунда 0
        self.assertTrue(any("round0: failing: ['test_g']" in t
                            for t in res.trace))
        # ремонтный промпт: свой ответ + регрессионная подпись + stderr
        repair = session.calls[1]["json"]["messages"]
        self.assertEqual(repair[2]["role"], "assistant")
        self.assertEqual(repair[2]["content"], TRAP_NAIVE)
        feedback = repair[3]["content"]
        self.assertIn("BROKE these tests that were passing before", feedback)
        self.assertIn("test_g", feedback)
        self.assertIn("AssertionError:", feedback)
        self.assertIn("Full test output:", feedback)

    def test_invalid_reply_repaired_without_touching_workspace(self):
        # проза вместо модуля: ast-гейт не пускает мусор, ремонт чинит
        task = self.make_task()
        res, session = self.solve_with(
            task, llm_text("The bug is in add(): you should use a + b."),
            llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(len(session.calls), 2)
        feedback = session.calls[1]["json"]["messages"][3]["content"]
        self.assertIn("not a valid Python module", feedback)

    def test_all_invalid_replies_leave_workspace_intact(self):
        task = self.make_task()
        res, session = self.solve_with(
            task, llm_text("maybe a + b?"), llm_text("try again"),
            llm_text("no code"))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 3)       # кап 2 ремонтов
        self.assertEqual((task.workspace_dir / "mylib.py")
                         .read_text(encoding="utf-8"), BUGGY)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())


class OscillationTest(Env):
    def test_ping_pong_stops_early(self):
        # A -> B -> A: возврат к исходному модулю = то же множество фейлов,
        # что в initial run. Детектор обязан остановиться ПОСЛЕ 2 вызовов,
        # а не сжигать третий (кап позволяет 3).
        task = self.make_task(mylib=TRAP_BUGGY, tests=TRAP_TESTS)
        res, session = self.solve_with(task, llm_text(TRAP_NAIVE),
                                       llm_text(TRAP_BUGGY))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 2)       # ранний стоп!
        self.assertTrue(any("oscillation" in t and "initial run" in t
                            for t in res.trace))
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_no_progress_stops_after_first_call(self):
        # «фикс» вернул исходный модуль: множество фейлов не изменилось
        task = self.make_task()
        res, session = self.solve_with(task, llm_text(BUGGY))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 1)       # сразу, без ремонтов
        self.assertTrue(any("oscillation" in t for t in res.trace))
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_cap_exhaustion_without_oscillation(self):
        # каждое множество фейлов новое: кап 2 ремонтов = ровно 3 вызова
        task = self.make_task(mylib=TRIPLE_BUGGY, tests=TRIPLE_TESTS)
        res, session = self.solve_with(task, llm_text(TRIPLE_R0),
                                       llm_text(TRIPLE_R1),
                                       llm_text(TRIPLE_R2))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 3)
        self.assertTrue(any("repair budget exhausted" in t
                            for t in res.trace))
        self.assertFalse(any("oscillation" in t for t in res.trace))
        for path in task.answer_candidates:
            self.assertFalse(path.exists())


class GateTest(Env):
    def test_no_python_files_unanswerable(self):
        task = self.make_task(mylib=None, tests=None)
        res, session = self.solve_with(task)            # ответов нет!
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(res.cost, 0.0)
        self.assertEqual(session.calls, [])

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

    def test_initial_run_timeout_fails_without_llm(self):
        task = self.make_task()
        old = v2solver.TEST_TIMEOUT
        v2solver.TEST_TIMEOUT = 0.001               # python не успевает
        self.addCleanup(setattr, v2solver, "TEST_TIMEOUT", old)
        res, session = self.solve_with(task)            # ответов нет!
        self.assertEqual(res.status, FAILED)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("timeout" in t for t in res.trace))

    def test_non_unittest_output_goes_to_prompt_raw(self):
        # тест-файл без юниттеста: rc!=0, фейлов не распарсили —
        # сырой вывод уходит в промпт как есть
        tests = ("import sys\n"
                 "print('boom', file=sys.stderr)\n"
                 "sys.exit(1)\n")
        task = self.make_task(tests=tests)
        res, session = self.solve_with(task, llm_text(FIXED))
        self.assertEqual(len(session.calls), 1)
        user = session.calls[0]["json"]["messages"][1]["content"]
        self.assertIn("Test run output:", user)
        self.assertIn("boom", user)
        # второй прогон даёт то же пустое множество -> осцилляция, failed
        self.assertEqual(res.status, FAILED)


class ReplyCapTest(Env):
    """Ловушка 0037: статический кап 800 рвал большие модули."""

    def test_small_module_gets_floor_cap(self):
        task = self.make_task()
        cap = v2solver.reply_cap(task.workspace_dir / "mylib.py")
        self.assertEqual(cap, v2solver.MAX_TOKENS)

    def test_big_module_raises_cap(self):
        # ~10k chars ≈ 2400 токенов: кап обязан вырасти за нижнюю грань
        big = BUGGY + "\n" + "\n".join(
            f"def filler_{i}(x):\n    \"\"\"Filler #{i}.\"\"\"\n"
            f"    return x * {i}\n" for i in range(1, 200))
        task = self.make_task(mylib=big)
        cap = v2solver.reply_cap(task.workspace_dir / "mylib.py")
        self.assertGreater(cap, v2solver.MAX_TOKENS)

    def test_solve_trace_carries_cap(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text(FIXED))
        self.assertTrue(any(t.startswith("cap=") for t in res.trace))

    def test_truncation_detected_and_cap_raised(self):
        """Ловушка 0035/0036: completion == cap + мусор -> CUT OFF,
        следующий вызов идёт с капом ×1.5, а не с тем же самым."""
        task = self.make_task()
        # первый ответ: усечённый мусор (completion >= cap)
        # второй ответ: валидный фикс -> DONE
        res, session = self.solve_with(task, llm_text("def broken(",
                                                      completion=800),
                                       llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(any("cut off" in t for t in res.trace))
        # второй вызов — с выросшим капом
        self.assertEqual(session.calls[1]["json"]["max_tokens"],
                         int(v2solver.MAX_TOKENS * 1.5) + 1)
        # фидбэк упоминает причину
        self.assertIn("CUT OFF", session.calls[1]["json"]["messages"][3]["content"])


class UnsolvableTest(Env):
    """Ловушка 0046: противоречивые тесты — модель обязана иметь
    легальный канал отказа (маркер, как в math/v2 и DA/v2)."""

    def test_marker_first_round_unanswerable(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text(
            "UNSOLVABLE: the tests contradict each other"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 1)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_marker_with_short_explanation(self):
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text("UNSOLVABLE: no module fits"))
        self.assertEqual(res.status, UNANSWERABLE)

    def test_marker_in_repair_round(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text("no module here"),
                                       llm_text("UNSOLVABLE: contradiction"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 2)

    def test_long_reply_is_not_marker(self):
        # первая строка длиннее допуска маркера — это мусорный модуль,
        # а не отказ: обычный путь ремонта
        task = self.make_task()
        rambling = ("UNSOLVABLE is definitely not the correct answer for "
                    "this task despite what the marker suggests\n"
                    + "x = 1\n") * 5
        res, session = self.solve_with(task, llm_text(rambling),
                                       llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(len(session.calls), 2)

    def test_prompt_mentions_unsolvable(self):
        from solutions.coding.v2.solver import SYSTEM_PROMPT
        self.assertIn("UNSOLVABLE", SYSTEM_PROMPT)


class ParseFailuresTest(unittest.TestCase):
    """Разбор stderr юниттеста (coding/tools.parse_failures)."""

    def test_two_failures_with_assertions(self):
        failures = parse_failures(STDERR)
        self.assertEqual(failures, {
            "test_add": "AssertionError: -1 != 5",
            "test_double": "AssertionError: 8 != 9",
        })

    def test_ran_count(self):
        self.assertEqual(ran_count(STDERR), 2)
        self.assertEqual(ran_count("Ran 1 test in 0.001s"), 1)
        self.assertIsNone(ran_count("no summary"))

    def test_report_format(self):
        report = test_report({"test_add": "AssertionError: -1 != 5"}, 2)
        self.assertIn("1 of 2 tests pass, 1 fail:", report)
        self.assertIn("- test_add: AssertionError: -1 != 5", report)

    def test_error_block_parsed(self):
        stderr = ("E\n"
                  + "=" * 70 + "\n"
                  "ERROR: test_mylib (unittest.loader._FailedLoadTests)\n"
                  "----------------------------------------------------------------------\n"
                  "ImportError: No module named 'x'\n")
        self.assertEqual(parse_failures(stderr),
                         {"test_mylib": "ImportError: No module named 'x'"})

    def test_empty_and_passing(self):
        self.assertEqual(parse_failures(""), {})
        self.assertEqual(parse_failures("OK\nRan 3 tests\n"), {})


if __name__ == "__main__":
    unittest.main()
