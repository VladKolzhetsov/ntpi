# -*- coding: utf-8 -*-
"""Тесты solutions/coding/v1/solver.py — tmpdir + фейковый LLM; тесты
задачи исполняются реальным python3 в сабпроцессе (cwd=workspace).

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.coding.test_v1 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.coding.v1.solver import ANSWERED, FAILED, UNANSWERABLE, solve
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


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


class Env(unittest.TestCase):
    def make_task(self, instruction=INSTR, mylib=BUGGY, tests=TESTS,
                  mylib_name="mylib.py", tests_name="test_mylib.py"):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "coding_8888"
        ws = task_dir / "workspace"
        ws.mkdir(parents=True)
        if mylib is not None:
            (ws / mylib_name).write_text(mylib, encoding="utf-8")
        if tests is not None:
            (ws / tests_name).write_text(tests, encoding="utf-8")
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


class HappyPathTest(Env):
    def test_fixed_module_written_and_tests_pass(self):
        task = self.make_task()
        res, session = self.solve_with(task, llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "DONE")
        self.assertEqual(len(res.written), 2)          # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "DONE\n")
        # mylib.py перезаписан исправленной версией
        ws_lib = task.workspace_dir / "mylib.py"
        self.assertEqual(ws_lib.read_text(encoding="utf-8").rstrip("\n"),
                         FIXED.rstrip("\n"))
        # тесты не тронуты
        self.assertEqual((task.workspace_dir / "test_mylib.py")
                         .read_text(encoding="utf-8"), TESTS)
        self.assertEqual(len(session.calls), 1)
        self.assertTrue(any("tests: OK" in t for t in res.trace))

    def test_prompt_has_instruction_and_files(self):
        task = self.make_task()
        _, session = self.solve_with(task, llm_text(FIXED))
        sent = session.calls[0]["json"]
        user = sent["messages"][1]["content"]
        self.assertIn(task.instruction, user)          # инструкция вербатим
        self.assertIn("--- mylib.py ---", user)
        self.assertIn("--- test_mylib.py ---", user)
        self.assertIn("return a - b", user)            # исходник в контексте
        self.assertIn("debugging", sent["messages"][0]["content"])
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)

    def test_fenced_reply_extracted(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text("```python\n" + FIXED + "```\n"))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(
            (task.workspace_dir / "mylib.py").read_text(encoding="utf-8")
            .rstrip("\n"), FIXED.rstrip("\n"))

    def test_cost_counts_prompt_at_tenth(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, llm_text(FIXED, prompt=800, completion=100))
        self.assertAlmostEqual(res.cost, 100 + 800 / 10)


class ExtractionGuardTest(Env):
    def test_invalid_reply_does_not_touch_workspace(self):
        # объяснение без фенсов: не python -> не пишем, workspace цел
        task = self.make_task()
        before = (task.workspace_dir / "mylib.py").read_text(encoding="utf-8")
        res, _ = self.solve_with(
            task, llm_text("The bug is in add(): you should use a + b."))
        self.assertEqual(res.status, FAILED)
        self.assertEqual((task.workspace_dir / "mylib.py")
                         .read_text(encoding="utf-8"), before)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_still_failing_fix_is_failed_without_answer(self):
        # «фикс» не чинит тесты: ответа нет, файл ответа не создаём
        task = self.make_task()
        res, _ = self.solve_with(task, llm_text(BUGGY))  # вернули как было
        self.assertEqual(res.status, FAILED)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())
        self.assertTrue(any("tests: exit=1" in t for t in res.trace))


class PrecheckGateTest(Env):
    def test_no_python_files_unanswerable(self):
        task = self.make_task(mylib=None, tests=None)
        res, session = self.solve_with(task)            # ответов нет!
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(res.cost, 0.0)
        self.assertEqual(session.calls, [])

    def test_named_file_missing_unanswerable(self):
        instr = INSTR.replace("mylib.py", "other.py")
        task = self.make_task(instruction=instr)
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])

    def test_no_test_file_unanswerable(self):
        task = self.make_task(tests=None)
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])

    def test_instruction_asking_to_fix_tests_unanswerable(self):
        # «чинить тесты» противоречит «do not edit the tests»
        instr = INSTR.replace("Fix the bugs in mylib.py",
                              "Fix the bugs in test_mylib.py")
        task = self.make_task(instruction=instr)
        res, session = self.solve_with(task)
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(session.calls, [])

    def test_command_whitelist_dangerous_backticks_ignored(self):
        # бэктики с неразрешённой командой не исполняются никогда
        instr = ("Run `rm -rf /` and then `python3 test_mylib.py` in the "
                 "current directory. Fix the bugs in mylib.py (do not edit "
                 "the tests) until all tests pass. Write DONE to answer.txt.")
        task = self.make_task(instruction=instr)
        res, _ = self.solve_with(task, llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)         # fallback на test_*.py
        self.assertTrue(any('tests="python3 test_mylib.py"' in t
                            for t in res.trace))

    def test_fallback_target_single_module_file(self):
        # инструкция без явного имени модуля: чиним единственный не-test .py
        instr = ("Some tests fail. Fix the bugs (do not edit the tests) "
                 "until all tests pass. Write DONE to answer.txt.")
        task = self.make_task(instruction=instr)
        res, _ = self.solve_with(task, llm_text(FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertTrue(any("target=mylib.py" in t for t in res.trace))


class FailureTest(Env):
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


if __name__ == "__main__":
    unittest.main()
