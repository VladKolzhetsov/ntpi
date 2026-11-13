# -*- coding: utf-8 -*-
"""Тесты solutions/math/v2/solver.py — на tmpdir и фейковом LLM, без сети
(код исполняется реальным python3 в сабпроцессе).

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.math.test_v2 -v
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from solutions.llm import LLMClient, QWEN_38_27B_NOREASON
from solutions.math.v2.solver import (
    ANSWERED, FAILED, MAX_TOKENS, TRUNCATION_REPAIR_CAP, UNANSWERABLE,
    _TRUNCATION_ECHO_CHARS, solve,
)
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession

# валидная программа: решает обе тренировочные задачи
CODE_OK = ("stock = 1752\n"
           "stock = stock // 2\n"
           "stock = stock * 2\n"
           "stock = stock // 3\n"
           "stock = stock * 2\n"
           "stock = stock // 3\n"
           "print(stock)\n")

CODE_FENCED = "Here is the program:\n```python\nprint(40 + 2)\n```\n"

# обёртки, которые модели любят добавлять (инструкция задачи велит python3 -c)
CODE_WRAPPED_PY = 'python3 -c "print(40 + 2)"\n'
CODE_WRAPPED_PY_MULTI = (
    'python3 -c "\n'
    'stock = 1752\n'
    'stock = stock // 2\n'
    'stock = stock * 2\n'
    'stock = stock // 3\n'
    'stock = stock * 2\n'
    'stock = stock // 3\n'
    'print(stock)\n'
    '"\n'
)
CODE_WRAPPED_FENCED = (
    "Run this:\n```bash\npython3 -c 'print(40 + 2)'\n```\n"
)

CODE_DEBUG = ("x = 21\n"
              "print('intermediate', x)\n"
              "print(x * 2)\n")

CODE_INT_WITH_COMMAS = 'print("1,735 boxes")\n'

CODE_RUNTIME_ERROR = "print(1 / 0)\n"
CODE_TIMEOUT = "while True: pass\n"
CODE_NO_PRINT = "x = 42\n"
CODE_BAD_SYNTAX = "def f(:\n"

CODE_FIXED = "print(6 * 7)\n"


def llm_text(text, prompt=50, completion=20):
    return FakeResponse(data={
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


def code_reply(code, **kw):
    """Ответ LLM = голый код (типичный ответ планировщика)."""
    return llm_text(code, **kw)


class Env(unittest.TestCase):
    def make_task(self, instruction="Compute 1752 // 2 * 2 // 3 * 2 // 3.",
                  check="exact_int", expected="389"):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        task_dir = self.tmpdir / "math_8888"
        (task_dir / "workspace").mkdir(parents=True)
        (task_dir / "task.json").write_text(json.dumps({
            "task_id": "math_8888",
            "task_type": "math",
            "instruction": instruction,
            "reward": 1,
            "expected": expected,
            "check": check,
        }), encoding="utf-8")
        return Task.from_dir(task_dir)

    def solve_with(self, task, *responses):
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return solve(task, client), session


class HappyPathTest(Env):
    def test_code_runs_answer_written(self):
        task = self.make_task()
        res, session = self.solve_with(task, code_reply(CODE_OK))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "389")               # 1752//2*2//3*2//3
        self.assertEqual(len(res.written), 2)             # ОБЕ позиции
        for path in task.answer_candidates:
            self.assertEqual(path.read_text(encoding="utf-8"), "389\n")
        self.assertEqual(len(session.calls), 1)

    def test_fenced_code_extracted(self):
        task = self.make_task(expected="42")
        res, _ = self.solve_with(task, code_reply(CODE_FENCED))
        self.assertEqual(res.answer, "42")

    def test_python3_c_wrapper_unwrapped(self):
        task = self.make_task(expected="42")
        res, session = self.solve_with(task, code_reply(CODE_WRAPPED_PY))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")
        self.assertEqual(len(session.calls), 1)           # без ремонта

    def test_python3_c_wrapper_multiline(self):
        task = self.make_task()
        res, session = self.solve_with(task, code_reply(CODE_WRAPPED_PY_MULTI))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "389")
        self.assertEqual(len(session.calls), 1)

    def test_bash_fenced_wrapper_unwrapped(self):
        task = self.make_task(expected="42")
        res, session = self.solve_with(task, code_reply(CODE_WRAPPED_FENCED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")
        self.assertEqual(len(session.calls), 1)

    def test_last_print_wins(self):
        # отладочные принты не мешают: последний — итоговый
        task = self.make_task(expected="42")
        res, _ = self.solve_with(task, code_reply(CODE_DEBUG))
        self.assertEqual(res.answer, "42")

    def test_int_with_commas_cleaned(self):
        task = self.make_task(expected="1735")
        res, _ = self.solve_with(task, code_reply(CODE_INT_WITH_COMMAS))
        self.assertEqual(res.answer, "1735")

    def test_float_stdout_not_split(self):
        # «270.0» — целое в float-записи, а не «270» и «0»:
        # баг пойман adversarial-пакетом (math_0058: ответ '0' вместо '270')
        task = self.make_task(expected="270")
        res, _ = self.solve_with(
            task, code_reply("print(900 * 2 / 3 * 0.6 * 0.75)\n"))
        self.assertEqual(res.answer, "270")

    def test_negative_float_stdout(self):
        task = self.make_task(expected="-50")
        res, _ = self.solve_with(task, code_reply("print(100 - 150.0)\n"))
        self.assertEqual(res.answer, "-50")

    def test_non_integer_float_answer_kept(self):
        # нецелый float не округляем за модель: честный «136.5»,
        # а не мусорный «5» (правила округления — работа модели)
        task = self.make_task(expected="137")
        res, _ = self.solve_with(task, code_reply("print(136.5)\n"))
        self.assertEqual(res.answer, "136.5")

    def test_prompt_contains_instruction_verbatim(self):
        task = self.make_task()
        _, session = self.solve_with(task, code_reply(CODE_OK))
        sent = session.calls[0]["json"]
        self.assertEqual(sent["messages"][1]["content"], task.instruction)
        self.assertIn("Python", sent["messages"][0]["content"])
        self.assertEqual(sent["temperature"], 0.0)
        self.assertEqual(sent["model"], QWEN_38_27B_NOREASON)

    def test_prompt_carries_rounding_and_format_rules(self):
        # уроки adversarial-пакета закреплены в промпте
        task = self.make_task()
        _, session = self.solve_with(task, code_reply(CODE_OK))
        system = session.calls[0]["json"]["messages"][0]["content"]
        self.assertIn("half-up", system)          # 0051: banker's rounding
        self.assertIn(".3f", system)              # 0053: хвостовые нули
        self.assertIn("rounds the RESULT", system)  # 0055: конвенция корпуса
        self.assertIn("never print a number you guessed", system)
        self.assertIn("do not floor the shipped amount", system)

    def test_prompt_forbids_comment_rambling(self):
        # 2026-11-12: 64% символов ответов — '#'-комментарии; у болтунов
        # (0043/0010/0055) они съедали 223–344 из 400 токенов cap'а.
        task = self.make_task()
        _, session = self.solve_with(task, code_reply(CODE_OK))
        system = session.calls[0]["json"]["messages"][0]["content"]
        self.assertIn("Write NO comments at all", system)
        self.assertIn("no '#'", system)
        self.assertIn("Output the code FIRST", system)
        self.assertIn("not with notes", system)


class RepairTest(Env):
    def test_runtime_error_repaired(self):
        # 1-й ответ ломается, 2-й чинит -> успех, 2 вызова
        task = self.make_task(expected="42")
        res, session = self.solve_with(
            task, code_reply(CODE_RUNTIME_ERROR), code_reply(CODE_FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")
        self.assertEqual(len(session.calls), 2)
        # ремонтный промпт содержит stderr и исходный код
        repair = session.calls[1]["json"]["messages"]
        self.assertIn("ZeroDivisionError", repair[-1]["content"])
        self.assertIn("print(1 / 0)", repair[-2]["content"])

    def test_syntax_error_repaired(self):
        task = self.make_task(expected="42")
        res, _ = self.solve_with(
            task, code_reply(CODE_BAD_SYNTAX), code_reply(CODE_FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")

    def test_timeout_gives_up_after_repair(self):
        task = self.make_task()
        res, session = self.solve_with(
            task, code_reply(CODE_TIMEOUT), code_reply(CODE_TIMEOUT))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 2)           # 1 + 1 ремонт
        self.assertFalse(res.written)

    def test_no_print_detected(self):
        # ответ без print( и без фенсов -> код не извлечён -> ремонт -> успех
        task = self.make_task(expected="42")
        res, session = self.solve_with(
            task, llm_text("I will think about it."), code_reply(CODE_FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")
        self.assertEqual(len(session.calls), 2)

    def test_double_failure_no_files(self):
        task = self.make_task()
        res, _ = self.solve_with(
            task, code_reply(CODE_RUNTIME_ERROR), code_reply(CODE_RUNTIME_ERROR))
        self.assertEqual(res.status, FAILED)
        for path in task.answer_candidates:
            self.assertFalse(path.exists())               # файлов нет


RAMBLE = ("Let me think about this carefully. " * 20)   # болтовня без кода


class RamblingTruncationTest(Env):
    """Болтовня вместо кода: усечение по капу детектируется и лечится.

    Факт с живой базы (2026-11-12): 7/143 первых вызовов math упираются
    в кап 400 рассуждениями в комментариях БЕЗ единого print(). Это
    теряло решаемые задачи (math_0010 -> FAIL за 446 экв.).
    """

    def test_truncated_rambling_gets_focused_repair(self):
        # первый ответ = болтовня до капа -> ремонт с капом 250 и «только код»
        task = self.make_task(expected="42")
        res, session = self.solve_with(
            task, llm_text(RAMBLE, completion=400), code_reply(CODE_FIXED))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")
        self.assertEqual(len(session.calls), 2)
        # второй вызов: кап понижен
        self.assertEqual(session.calls[1]["json"]["max_tokens"],
                         TRUNCATION_REPAIR_CAP)
        # и требование жёсткое: «только код», без разбора причин отказа
        repair = session.calls[1]["json"]["messages"][-1]["content"]
        self.assertIn("cut off at the token limit", repair)
        self.assertIn("program ONLY", repair)
        self.assertIn("no comments", repair)

    def test_truncated_rambling_echo_is_short(self):
        # эхо болтовни не тащим целиком: оно шум, а не полезный контекст
        task = self.make_task(expected="42")
        _, session = self.solve_with(
            task, llm_text(RAMBLE, completion=400), code_reply(CODE_FIXED))
        echo = session.calls[1]["json"]["messages"][-2]
        self.assertEqual(echo["role"], "assistant")
        self.assertLessEqual(len(echo["content"]), _TRUNCATION_ECHO_CHARS)

    def test_rambling_detected_only_on_cap_hit(self):
        # короткий ответ без кода — обычный ремонт (полный кап, разбор ошибки)
        task = self.make_task(expected="42")
        _, session = self.solve_with(
            task, llm_text("I will think about it.", completion=8),
            code_reply(CODE_FIXED))
        self.assertEqual(session.calls[1]["json"]["max_tokens"], MAX_TOKENS)
        repair = session.calls[1]["json"]["messages"][-1]["content"]
        self.assertIn("no Python program", repair)

    def test_rambling_repair_failing_gives_up_cheaply(self):
        # повторная болтовня: второй кап 250 (не 400) -> сдаёмся без файлов
        task = self.make_task(expected="42")
        res, session = self.solve_with(
            task, llm_text(RAMBLE, completion=400),
            llm_text(RAMBLE, completion=250))
        self.assertEqual(res.status, FAILED)
        self.assertEqual(len(session.calls), 2)            # третьего нет
        self.assertLess(res.cost, 400 + 250 + 60)          # не как два капа 400
        for path in task.answer_candidates:
            self.assertFalse(path.exists())

    def test_rambling_trace_marks_cap_hit(self):
        task = self.make_task(expected="42")
        res, _ = self.solve_with(
            task, llm_text(RAMBLE, completion=400), code_reply(CODE_FIXED))
        self.assertTrue(any("rambling" in line for line in res.trace))

    def test_code_at_cap_is_not_treated_as_rambling(self):
        # код, упёршийся в кап (обрезанная программа) — обычный путь:
        # syntax error -> разбор ошибки, полный кап ремонта
        task = self.make_task(expected="42")
        _, session = self.solve_with(
            task, llm_text("print(6 * 7)\nprint(", completion=400),
            code_reply(CODE_FIXED))
        self.assertEqual(session.calls[1]["json"]["max_tokens"], MAX_TOKENS)


TRAP_INSTRUCTION = (
    "A warehouse starts with 1752 boxes. It ships 36 boxes per day for 3 "
    "days. How many crates does it have at the end? Compute it with the "
    "bash tool (python3 -c). Write ONLY the final answer to answer.txt.")


class UnsolvableTest(Env):
    """Детекция нерешаемости: детерминированные фильтры + маркер UNSOLVABLE."""

    def test_noun_guard_stops_before_llm(self):
        # ловушка «условие про boxes, вопрос про crates»
        task = self.make_task(TRAP_INSTRUCTION)
        res, session = self.solve_with(task)                 # ответов нет!
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(res.cost, 0.0)                      # LLM не звали
        self.assertFalse(res.written)
        self.assertEqual(session.calls, [])
        self.assertTrue(any("detector:" in t for t in res.trace))

    def test_unsolvable_marker_first_round(self):
        task = self.make_task(
            "A warehouse has 1752 boxes and ships 36 per day for 3 days. "
            "How many boxes are left at the end? Write answer.txt.")
        res, session = self.solve_with(task, code_reply("UNSOLVABLE\n"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 1)              # без ремонта
        self.assertFalse(res.written)
        self.assertTrue(any("UNSOLVABLE" in t for t in res.trace))

    def test_unsolvable_marker_with_note(self):
        task = self.make_task(
            "A warehouse has 1752 boxes and ships 36 per day for 3 days. "
            "How many boxes are left at the end? Write answer.txt.")
        res, _ = self.solve_with(
            task, code_reply("UNSOLVABLE: the daily rate is not given"))
        self.assertEqual(res.status, UNANSWERABLE)

    def test_unsolvable_marker_in_repair(self):
        # сначала сломанный код, после ремонта модель осознаёт нехватку
        task = self.make_task(
            "A warehouse has 1752 boxes and ships 36 per day for 3 days. "
            "How many boxes are left at the end? Write answer.txt.")
        res, session = self.solve_with(
            task, code_reply(CODE_RUNTIME_ERROR), code_reply("UNSOLVABLE"))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 2)

    def test_code_printing_marker_word_is_not_refusal(self):
        # программа, печатающая слово, — это код, а не отказ
        task = self.make_task(expected="42")
        res, _ = self.solve_with(
            task, code_reply('print("UNSOLVABLE", 21 * 2)\n'))
        self.assertEqual(res.status, ANSWERED)
        self.assertEqual(res.answer, "42")                   # exact_int: 42

    def test_stdout_marker_is_refusal(self):
        # отказ ВНУТРИ программы (print("UNSOLVABLE")) — тот же маркер:
        # баг пойман adversarial-пакетом (math_0056: 404 экв. в failed)
        task = self.make_task(
            "Trains are moving at different speeds. Train A leaves the "
            "station 60 minutes before Train B at a speed of 81 km per "
            "hour. Train B travels at 62 km per hour and can never catch "
            "up. After how many minutes does B catch up? Write answer.txt.")
        res, session = self.solve_with(
            task, code_reply('print("UNSOLVABLE")\n'))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertEqual(len(session.calls), 1)              # без ремонта
        self.assertFalse(res.written)

    def test_stdout_marker_exact_str_not_answer(self):
        # для exact_str stdout-маркер не должен утечь в answer.txt
        task = self.make_task(check="exact_str", expected="1.200")
        res, _ = self.solve_with(task, code_reply('print("UNSOLVABLE")\n'))
        self.assertEqual(res.status, UNANSWERABLE)
        self.assertFalse(res.written)


class TimeoutTest(unittest.TestCase):
    """Таймаут не должен заметно тормозить suite (10с x2 раунда)."""

    def test_solve_completes_within_25s(self):
        import time
        tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmpdir, True)
        task_dir = tmpdir / "math_7777"
        task_dir.mkdir(parents=True)
        (task_dir / "task.json").write_text(json.dumps({
            "task_id": "math_7777", "task_type": "math",
            "instruction": "loop forever", "reward": 1,
        }), encoding="utf-8")
        task = Task.from_dir(task_dir)
        session = FakeSession([code_reply(CODE_TIMEOUT),
                               code_reply(CODE_TIMEOUT)])
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        t0 = time.monotonic()
        res = solve(task, client)
        self.assertLess(time.monotonic() - t0, 25)
        self.assertEqual(res.status, FAILED)


if __name__ == "__main__":
    unittest.main()
