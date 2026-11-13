# -*- coding: utf-8 -*-
"""Тесты solutions/runner.py — триаж, бюджет, пер-задачный envelope.

Без сети: солверы подменяются заглушками, клиент — LLMClient на фейковой
сессии. Настоящие солверы здесь не тестируются (у каждого свой набор).

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.test_runner -v
"""

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import ANY, patch

from solutions.base import (ANSWERED, FAILED, SolveResult, check_writable,
                            write_answer)
from solutions.llm import BudgetTracker, LLMBudgetError, LLMClient, Usage
from solutions.runner import (
    CAP_FACTOR, EXPECTED_COST, SKIPPED, SOFT_GATE_FACTOR, RunConfig,
    ScopedBudget, allowance_for, cost_worst_for, fmt_worst, order_tasks,
    parse_worst_case, priority, resolve_solver, run, task_costs, verify,
    worst_enabled,
)
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse

MODEL = "fake-model"
ROOT = Path(__file__).resolve().parents[2]


class LoopSession:
    """Всегда отвечает одним и тем же usage: имитирует «ненасытный» прокси."""

    def __init__(self, completion: int = 100):
        self.completion = completion
        self.calls = 0
        self.closed = False

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls += 1
        return FakeResponse(data={
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": self.completion},
        })

    def close(self):
        self.closed = True


class DeadSession:
    """Любой запрос — ошибка теста: LLM вызываться не должен."""

    def post(self, *a, **kw):                        # pragma: no cover
        raise AssertionError("LLM не должен вызываться в этом тесте")

    def close(self):
        pass


def make_task(task_dir: Path, task_id: str, task_type: str, reward: int,
              expected=None, check=None, instruction=None) -> Task:
    (task_dir / "workspace").mkdir(parents=True, exist_ok=True)
    (task_dir / "task.json").write_text(json.dumps({
        "task_id": task_id,
        "task_type": task_type,
        "instruction": instruction or f"instruction of {task_id}",
        "reward": reward,
        "expected": expected,
        "check": check,
    }), encoding="utf-8")
    return Task.from_dir(task_dir)


def answering_solver(answer: str | None = None, status: str = ANSWERED):
    """Заглушка солвера: пишет answer.txt (обе позиции), как настоящий."""

    def solver(task, client, model):
        if status != ANSWERED:
            return SolveResult(task_id=task.task_id, status=status)
        value = answer if answer is not None else (task.expected or "42")
        written, errors = write_answer(task, value)
        return SolveResult(task_id=task.task_id, status=ANSWERED, answer=value,
                           written=written, trace=list(errors))

    return solver


class Env(unittest.TestCase):
    """tmpdir для задач + фабрика клиента на DeadSession."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    def client_factory(self, session=None):
        session = session if session is not None else DeadSession()

        def make():
            return LLMClient("https://p/v1", "k", session=session,
                             retry_delay=0)
        return make

    def config(self, **kw) -> RunConfig:
        cfg = RunConfig(model=MODEL, log=lambda msg: None, **kw)
        cfg.solvers = {t: answering_solver() for t in
                       ("search", "math", "data_analysis", "coding")}
        return cfg


class TriageTest(Env):
    """Сортировка по плотности reward/стоимость."""

    def _task(self, task_id, task_type, reward):
        return make_task(self.tmpdir / task_id, task_id, task_type, reward)

    def test_density_order(self):
        # coding дорогой: 1/460 < 2/140 (search) < 5/80 (math)
        tasks = [self._task("c_1", "coding", 1),
                 self._task("s_2", "search", 2),
                 self._task("m_5", "math", 5)]
        self.assertEqual([t.task_id for t in order_tasks(tasks)],
                         ["m_5", "s_2", "c_1"])

    def test_tie_break_cheaper_first_then_id(self):
        # плотности: math 2/80 = .025 > search 2/140 = .014
        tasks = [self._task("b_1", "math", 2),
                 self._task("z_1", "search", 2),
                 self._task("a_1", "math", 2)]
        self.assertEqual([t.task_id for t in order_tasks(tasks)],
                         ["a_1", "b_1", "z_1"])

    def test_priority_zero_reward(self):
        self.assertEqual(priority(self._task("z", "coding", 0)), 0.0)


class AllowanceTest(Env):
    def test_envelope_capped_by_expected_cost(self):
        self.assertEqual(allowance_for("coding", 1e9), 460.0 * 3)

    def test_allowance_limited_by_remaining(self):
        self.assertEqual(allowance_for("coding", 200.0), 200.0)

    def test_allowance_floor_is_min_viable(self):
        # остаток ниже минимума -> floor (гейт всё равно не пустит задачу)
        # MIN_VIABLE[math]=75 по фактам: медиана задачи 80 экв. (2026-11-12)
        self.assertEqual(allowance_for("math", 10.0), 75.0)

    def test_no_budget_means_no_cap(self):
        self.assertIsNone(allowance_for("coding", None))

    def test_scoped_budget_mirrors_to_global(self):
        from solutions.llm import BudgetTracker
        scoped = ScopedBudget(limit=100.0, mirror=BudgetTracker())
        scoped.record(Usage(prompt_tokens=300, completion_tokens=7))
        self.assertEqual(scoped.spent, 37.0)
        self.assertEqual(scoped._mirror.spent, 37.0)
        self.assertEqual(scoped._mirror.calls, 1)
        self.assertEqual(scoped._mirror.total_completion, 7)


class EnvelopeEnforcementTest(Env):
    """Envelope реально останавливает жадный солвер через LLMBudgetError."""

    def _burner(self, calls: list[int], max_calls: int = 100):
        def solver(task, client, model):
            while len(calls) < max_calls:
                try:
                    client.chat(MODEL, [{"role": "user", "content": "x"}])
                except LLMBudgetError:
                    break
                calls.append(1)
            return SolveResult(task_id=task.task_id, status=FAILED)
        return solver

    def test_envelope_stops_calls(self):
        # math: envelope = 80*3 = 240 экв.; вызов стоит 100 экв.
        # Мягкий гейт отказывает ДО вызова: проходят вызовы при spent
        # 0, 100, 200 (итог 300), четвёртый — отказ. Перерасход ≤ цены
        # одного вызова — так и задумано (LLMClient не режет на середине).
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)
        calls: list[int] = []
        cfg = self.config(budget=5000)
        cfg.solvers["math"] = self._burner(calls)
        session = LoopSession(completion=100)
        report = run([task], self.client_factory(session), cfg)
        self.assertEqual(len(calls), 3)
        self.assertLessEqual(report.outcomes[0].cost, 240.0 + 100.0)
        self.assertEqual(report.spent, 300.0)

    def test_without_budget_no_envelope_no_soft_reserve(self):
        # Без глобального бюджета envelope не выдаётся, и резерв (soft_limit)
        # раннером не ставится: гейт откачен (см. комментарий у SOFT_GATE_FACTOR).
        # Ограничение — только сейфти самого солвера.
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)
        calls: list[int] = []
        cfg = self.config(budget=None)
        cfg.solvers["math"] = self._burner(calls, max_calls=7)
        session = LoopSession(completion=100)
        report = run([task], self.client_factory(session), cfg)
        self.assertEqual(len(calls), 7)
        self.assertEqual(report.spent, 700.0)
        self.assertIsNone(report.budget)
        self.assertEqual(report.outcomes[0].status, FAILED)


class SoftGateTest(Env):
    """Механизм резерва: поле есть, но раннер его не включает (откат)."""

    def test_runner_does_not_set_soft_limit(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)
        seen = {}

        def probe(task, client, model):
            seen["soft"] = client.tracker.soft_limit
            seen["envelope"] = client.tracker.budget
            return SolveResult(task_id=task.task_id, status=FAILED)

        cfg = self.config(budget=5000)
        cfg.solvers["math"] = probe
        run([task], self.client_factory(LoopSession()), cfg)
        self.assertIsNone(seen["soft"])                  # резерв выключен
        self.assertEqual(seen["envelope"],
                         EXPECTED_COST["math"] * CAP_FACTOR)

    def test_reserve_can_be_enabled_explicitly(self):
        # механизм рабочий: с заданным резервом вызовы прекращаются раньше
        tracker = ScopedBudget(limit=1000.0, mirror=BudgetTracker(),
                               soft_limit=250.0)
        self.assertTrue(tracker.can_spend())
        tracker.total_completion = 300
        self.assertFalse(tracker.can_spend())
        self.assertEqual(tracker.soft_hits, 1)
        self.assertGreater(tracker.spent, tracker.soft_limit)

    def test_healthy_task_unaffected_by_absent_gate(self):
        # без резерва задача ремонтируется сколько нужно (до envelope)
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)
        calls: list[int] = []

        def twice(task, client, model):
            for _ in range(2):
                calls.append(1)
                client.chat("m", [{"role": "user", "content": "x"}])
            return SolveResult(task_id=task.task_id, status=FAILED)

        cfg = self.config(budget=5000)
        cfg.solvers["math"] = twice
        run([task], self.client_factory(LoopSession(completion=20)), cfg)
        self.assertEqual(len(calls), 2)          # 40 экв. << envelope 240


class GlobalBudgetTest(Env):
    def test_remaining_below_min_viable_skips_and_writes_nothing(self):
        # Бюджет 200 покрывает только одну дорогую задачу. Порядок триажа
        # решает, какая это будет: math (5/80) плотнее coding (5/460) —
        # поэтому первым идёт math; coding остаётся без средств.
        coding = make_task(self.tmpdir / "c_1", "c_1", "coding", 5)
        math = make_task(self.tmpdir / "m_1", "m_1", "math", 5)

        def burner(task, client, model):
            while client.tracker.can_spend():
                try:
                    client.chat(MODEL, [{"role": "user", "content": "x"}])
                except LLMBudgetError:
                    break
            return SolveResult(task_id=task.task_id, status=FAILED)

        cfg = self.config(budget=200.0)
        cfg.solvers["math"] = burner          # первый в триаже
        session = LoopSession(completion=100)
        report = run([coding, math], self.client_factory(session), cfg)
        statuses = {o.task_id: o.status for o in report.outcomes}
        self.assertEqual(statuses["m_1"], FAILED)
        self.assertEqual(statuses["c_1"], SKIPPED)
        skipped = next(o for o in report.outcomes if o.task_id == "c_1")
        self.assertIn("budget left", skipped.trace[0])
        # skipped -> answer.txt не создан
        self.assertFalse((coding.workspace_dir / "answer.txt").exists())
        self.assertFalse((coding.dir / "answer.txt").exists())
        self.assertLessEqual(report.spent, 200.0)
        # coding первым быть не может: math плотнее, отчёт в порядке триажа
        self.assertEqual([o.task_id for o in report.outcomes], ["m_1", "c_1"])


class GatesTest(Env):
    def test_min_viable_gate_skips_before_solver(self):
        task = make_task(self.tmpdir / "c_1", "c_1", "coding", 5)
        called: list[int] = []
        cfg = self.config(budget=100.0)          # 100 < MIN_VIABLE[coding]=140
        cfg.solvers["coding"] = lambda t, c, m: called.append(1)
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, SKIPPED)
        self.assertEqual(called, [])

    def test_deadline_skips(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)
        cfg = self.config(deadline=time.time() - 1)
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, SKIPPED)
        self.assertIn("time limit", report.outcomes[0].trace[0])

    def test_unknown_task_type_skipped(self):
        task = make_task(self.tmpdir / "x_1", "x_1", "poetry", 5)
        cfg = RunConfig(model=MODEL, log=lambda msg: None)   # без подмены
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, SKIPPED)
        self.assertIn("no solver", report.outcomes[0].trace[0])

    def test_solver_exception_becomes_failed(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5)

        def boom(task, client, model):
            raise RuntimeError("surprise")

        cfg = self.config()
        cfg.solvers["math"] = boom
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, FAILED)
        self.assertIn("surprise", " ".join(report.outcomes[0].trace))


class RunTest(Env):
    def test_answers_and_rewards(self):
        tasks = [make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                           expected="42", check="exact_int"),
                 make_task(self.tmpdir / "s_2", "s_2", "search", 2,
                           expected="Osaka", check="exact_str")]
        report = run(tasks, self.client_factory(), self.config(budget=5000))
        self.assertEqual(report.answered, 2)
        self.assertEqual(report.reward_total, 7)
        self.assertEqual(report.reward_earned, 7)
        # порядок отчёта = порядок триажа (math 5/80 > search 2/140)
        self.assertEqual([o.task_id for o in report.outcomes], ["m_1", "s_2"])
        for task in tasks:                       # ответы в обеих позициях
            for path in task.answer_candidates:
                self.assertTrue(path.is_file())

    def test_unanswerable_writes_nothing_and_earns_nothing(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        cfg = self.config()
        cfg.solvers["math"] = answering_solver(status="unanswerable")
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, "unanswerable")
        self.assertIs(report.outcomes[0].verified, False)
        self.assertEqual(report.reward_answered, 0)
        self.assertFalse((task.workspace_dir / "answer.txt").exists())

    def test_log_jsonl_and_report_json(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        out = self.tmpdir / "out"
        run([task], self.client_factory(), self.config(out_dir=out))
        lines = (out / "run_log.jsonl").read_text(
            encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["task_id"], "m_1")
        report = json.loads((out / "run_report.json").read_text(
            encoding="utf-8"))
        self.assertEqual(report["counts"]["answered"], 1)
        self.assertEqual(report["reward"]["earned"], 5)

    def test_sandbox_leaves_original_untouched(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        (task.workspace_dir / "m.txt").write_text("data", encoding="utf-8")

        def writer(task, client, model):
            (task.workspace_dir / "m.txt").write_text("changed", "utf-8")
            written, _ = write_answer(task, "42")
            return SolveResult(task_id=task.task_id, status=ANSWERED,
                               answer="42", written=written)

        cfg = self.config(sandbox_dir=self.tmpdir / "sandbox")
        cfg.solvers["math"] = writer
        report = run([task], self.client_factory(), cfg)
        self.assertEqual(report.outcomes[0].status, ANSWERED)
        self.assertEqual((task.workspace_dir / "m.txt").read_text(
            encoding="utf-8"), "data")                 # оригинал цел
        copy_ws = self.tmpdir / "sandbox" / "m_1" / "workspace"
        self.assertEqual((copy_ws / "m.txt").read_text(encoding="utf-8"),
                         "changed")
        self.assertFalse((task.workspace_dir / "answer.txt").exists())

    def test_workers_run_all_and_report_order(self):
        tasks = [make_task(self.tmpdir / f"m_{i}", f"m_{i}", "math", 1,
                           expected="42", check="exact_int")
                 for i in range(4)]
        session = LoopSession(completion=0)          # клиент без вызовов, но живой
        report = run(tasks, self.client_factory(session),
                     self.config(workers=2))
        self.assertEqual(len(report.outcomes), 4)
        self.assertEqual(report.answered, 4)
        self.assertEqual([o.task_id for o in report.outcomes],
                         [t.task_id for t in order_tasks(tasks)])

    def test_per_type_model_override(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        seen: dict[str, str] = {}

        def spy(task, client, model):
            seen[task.task_type] = model
            return SolveResult(task_id=task.task_id, status=FAILED)

        cfg = self.config(models={"math": "other-model"})
        cfg.solvers["math"] = spy
        run([task], self.client_factory(), cfg)
        self.assertEqual(seen["math"], "other-model")
        # без переопределения — общая модель
        cfg2 = self.config()
        cfg2.solvers["math"] = spy
        run([task], self.client_factory(), cfg2)
        self.assertEqual(seen["math"], MODEL)

    def test_clients_are_closed(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        session = LoopSession(completion=0)
        run([task], self.client_factory(session), self.config())
        self.assertTrue(session.closed)


class VerifyTest(Env):
    def _pair(self, answer, expected, check):
        task = make_task(self.tmpdir / "v", "v", "math", 1,
                         expected=expected, check=check)
        return task, SolveResult(task_id="v", status=ANSWERED, answer=answer)

    def test_exact_int(self):
        self.assertIs(verify(*self._pair("1735", "1735", "exact_int")), True)
        self.assertIs(verify(*self._pair("99", "1735", "exact_int")), False)

    def test_exact_int_strips_formatting(self):
        self.assertIs(verify(*self._pair(" 1,735 ", "1735", "exact_int")),
                      True)
        self.assertIs(verify(*self._pair("abc", "1735", "exact_int")), False)

    def test_exact_str_verbatim(self):
        self.assertIs(verify(*self._pair("Osaka", "Osaka", "exact_str")), True)
        self.assertIs(verify(*self._pair("osaka", "Osaka", "exact_str")), False)

    def test_unittest_passed(self):
        task = make_task(self.tmpdir / "u", "u", "coding", 1,
                         expected="tests_pass", check="unittest")
        result = SolveResult(task_id="u", status=ANSWERED, answer="DONE")
        self.assertIs(verify(task, result), True)

    def test_hidden_set_without_expected_is_none(self):
        task = make_task(self.tmpdir / "h", "h", "search", 3)   # expected=None
        result = SolveResult(task_id="h", status=ANSWERED, answer="42")
        self.assertIsNone(verify(task, result))

    def test_not_answered_is_false(self):
        task, _ = self._pair("42", "42", "exact_int")
        self.assertIs(verify(task, SolveResult(task_id="v", status=FAILED)),
                      False)


class WriteGuardTest(Env):
    """Ответ без файла не считается решённым (Docker: --cap-drop ALL)."""

    def test_check_writable_ok(self):
        self.assertIsNone(check_writable(self.tmpdir / "new" / "dir"))
        self.assertTrue((self.tmpdir / "new" / "dir").is_dir())

    @unittest.skipIf(os.geteuid() == 0, "root обходит права каталога")
    def test_check_writable_denied(self):
        ro = self.tmpdir / "ro"
        ro.mkdir()
        ro.chmod(0o500)
        self.addCleanup(ro.chmod, 0o700)
        reason = check_writable(ro)
        self.assertIsNotNone(reason)
        self.assertIn("права", reason)

    def test_answered_without_written_becomes_failed(self):
        """Солвер вернул ответ, но записать не смог — не притворяемся OK."""
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        cfg = self.config()
        cfg.solvers["math"] = lambda task, client, model: SolveResult(
            task_id=task.task_id, status=ANSWERED, answer="42", written=[])
        report = run([task], self.client_factory(), cfg)
        outcome = report.outcomes[0]
        self.assertEqual(outcome.status, FAILED)
        self.assertEqual(report.answered, 0)
        self.assertEqual(report.reward_answered, 0)
        self.assertTrue(any("NOT written" in line for line in outcome.trace))

    def test_answer_file_is_really_written(self):
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                         expected="42", check="exact_int")
        report = run([task], self.client_factory(), self.config())
        self.assertEqual(report.outcomes[0].status, ANSWERED)
        self.assertEqual(task.read_answer(), "42")
        self.assertTrue((task.workspace_dir / "answer.txt").is_file())

    @unittest.skipIf(os.geteuid() == 0, "root обходит права каталога")
    def test_readonly_tasks_dir_fails_fast_before_llm(self):
        """run_agent.py падает ДО прогона, если писать некуда."""
        tasks = self.tmpdir / "tasks"
        make_task(tasks / "m_1", "m_1", "math", 5, expected="42")
        tasks.chmod(0o500)
        self.addCleanup(tasks.chmod, 0o700)
        proc = subprocess.run(
            [sys.executable, str(ROOT / "run_agent.py"),
             "--tasks-dir", str(tasks), "--out", str(self.tmpdir / "out")],
            capture_output=True, text=True, timeout=60, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 3, proc.stderr)
        self.assertIn("некуда записать", proc.stderr)


class WorstCaseTest(Env):
    """Per-task cost_worst: плотность, envelope, контракт солверов."""

    def _search_task(self, task_id, reward) -> Task:
        task = make_task(self.tmpdir / task_id, task_id, "search", reward)
        kb = task.workspace_dir / "kb"
        kb.mkdir()
        (kb / "person_anna.md").write_text(
            "# Anna Petrova\n\n- Born: 1980\n- Employer: Northwind Ltd\n",
            encoding="utf-8")
        return task

    def test_flag_off_returns_none(self):
        task = self._search_task("s_1", 5)
        cfg = self.config()                      # worst_case выключен
        cfg.versions["search"] = "v2"
        self.assertIsNone(cost_worst_for(task, cfg))

    def test_search_v2_publishes_cost_worst(self):
        task = self._search_task("s_1", 5)
        cfg = self.config(worst_case=True, versions={"search": "v2"})
        value = cost_worst_for(task, cfg)
        self.assertIsNotNone(value)
        self.assertGreater(value, 200.0)         # план + ремонт + QA

    def test_per_type_set_excludes_other_types(self):
        # worst_case={'search'}: хук math/v2 есть, но math не включён
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 3)
        cfg = self.config(worst_case={"search"},
                          versions={"search": "v2", "math": "v2"})
        self.assertIsNone(cost_worst_for(task, cfg))

    def test_per_type_set_includes_search(self):
        task = self._search_task("s_1", 5)
        cfg = self.config(worst_case=frozenset({"search"}),
                          versions={"search": "v2"})
        self.assertIsNotNone(cost_worst_for(task, cfg))

    def test_solver_without_hook_returns_none(self):
        task = make_task(self.tmpdir / "s_1", "s_1", "search", 5)
        cfg = self.config(worst_case=True, versions={"search": "v1"})
        self.assertIsNone(cost_worst_for(task, cfg))

    def test_default_search_version_publishes_hook(self):
        # регресс-гейт: worst-case по умолчанию (search) требует хук в
        # дефолтной версии солвера — если default вернётся на v1, триаж
        # молча потеряет per-task границы
        from solutions.runner import DEFAULT_VERSIONS
        version = DEFAULT_VERSIONS["search"]
        module = f"solutions.search.{version}.solver"
        self.assertIsNotNone(
            getattr(importlib.import_module(module), "cost_worst", None))

    def test_math_v2_publishes_cost_worst(self):
        # bound = 2 полных капа (400) + input/10 (+ эхо и хвост ошибки)
        task = make_task(self.tmpdir / "m_1", "m_1", "math", 3)
        cfg = self.config(worst_case=True, versions={"math": "v2"})
        value = cost_worst_for(task, cfg)
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 800.0)        # 2 * MAX_TOKENS
        self.assertLess(value, 1200.0)               # без fallback, не больше

    def test_math_bound_grows_with_instruction(self):
        short = make_task(self.tmpdir / "m_1", "m_1", "math", 3)
        long = make_task(self.tmpdir / "m_2", "m_2", "math", 3,
                         instruction="Compute 2 + 2. " * 120)
        cfg = self.config(worst_case=True, versions={"math": "v2"})
        self.assertGreater(cost_worst_for(long, cfg),
                           cost_worst_for(short, cfg))

    def test_priority_uses_per_task_cost(self):
        task = make_task(self.tmpdir / "s_1", "s_1", "search", 7)
        self.assertEqual(priority(task), 7 / 140.0)
        self.assertEqual(priority(task, 560.0), 7 / 560.0)

    def test_order_tasks_uses_costs(self):
        # типовая стоимость: порядок по reward; cost_worst переворачивает:
        # 10/1400 < 2/200
        cheap = make_task(self.tmpdir / "a_10", "a_10", "search", 10)
        rich = make_task(self.tmpdir / "b_2", "b_2", "search", 2)
        self.assertEqual([t.task_id for t in order_tasks([cheap, rich])],
                         ["a_10", "b_2"])
        self.assertEqual(
            [t.task_id for t in order_tasks(
                [cheap, rich], {"a_10": 1400.0, "b_2": 200.0})],
            ["b_2", "a_10"])

    def test_order_normalized_keeps_cross_type_fairness(self):
        # ГРАНИЦА (worst ~595) против ОЖИДАНИЯ (80): без нормировки search
        # reward 10 проигрывал бы math reward 5 (10/595 < 5/80) — дискри-
        # минация целого типа. Нормировка ставит типовой search на профиль.
        search = make_task(self.tmpdir / "s_10", "s_10", "search", 10)
        math = make_task(self.tmpdir / "m_5", "m_5", "math", 5)
        by_profile = [t.task_id for t in order_tasks([search, math])]
        normalized = [t.task_id for t in
                      order_tasks([search, math], {"s_10": 595.0})]
        self.assertEqual(by_profile, ["s_10", "m_5"])   # 10/140 > 5/80
        self.assertEqual(normalized, ["s_10", "m_5"])   # как без границы

    def test_order_normalized_pushes_expensive_within_type(self):
        # та же награда: дорогая граница (2x медианы) — позже дешёвой
        a = make_task(self.tmpdir / "s_a", "s_a", "search", 5)
        b = make_task(self.tmpdir / "s_b", "s_b", "search", 5)
        order = [t.task_id for t in order_tasks(
            [a, b], {"s_a": 595.0, "s_b": 1190.0})]
        self.assertEqual(order, ["s_a", "s_b"])

    def test_order_single_task_normalizes_to_profile(self):
        # одна задача с границей: медиана = сама граница -> профиль
        task = make_task(self.tmpdir / "s_1", "s_1", "search", 7)
        costs = order_tasks([task], {"s_1": 595.0})
        self.assertEqual([t.task_id for t in costs], ["s_1"])

    def test_worst_enabled_semantics(self):
        self.assertFalse(worst_enabled(False, "search"))
        self.assertFalse(worst_enabled(frozenset(), "search"))
        self.assertTrue(worst_enabled(True, "search"))
        self.assertTrue(worst_enabled(True, "coding"))
        self.assertTrue(worst_enabled({"search"}, "search"))
        self.assertFalse(worst_enabled({"search"}, "math"))

    def test_parse_worst_case(self):
        self.assertEqual(parse_worst_case(""), False)
        self.assertEqual(parse_worst_case("none"), False)
        self.assertEqual(parse_worst_case("NONE "), False)
        self.assertEqual(parse_worst_case("all"), True)
        self.assertEqual(parse_worst_case("ALL"), True)
        self.assertEqual(parse_worst_case("search, math"),
                         frozenset({"search", "math"}))
        with self.assertRaises(ValueError):
            parse_worst_case("searchh")

    def test_fmt_worst(self):
        self.assertEqual(fmt_worst(False), "нет")
        self.assertEqual(fmt_worst(True), "все типы")
        self.assertEqual(fmt_worst(frozenset({"search"})), "search")
        self.assertEqual(fmt_worst({"math", "search"}), "math,search")

    def test_allowance_uses_worst_without_cap_factor(self):
        # 593 > 140*3=420: cost_worst уже полная граница
        self.assertEqual(allowance_for("search", 1e9, 593.1), 593.1)
        self.assertEqual(allowance_for("search", 500.0, 593.1), 500.0)
        # floor min_viable, как и у типового envelope
        self.assertEqual(allowance_for("search", 50.0, 593.1), 110.0)

    def test_task_costs_off_and_on(self):
        task = self._search_task("s_1", 5)
        self.assertEqual(task_costs([task], self.config()), {})
        cfg = self.config(worst_case=True, versions={"search": "v2"})
        self.assertEqual(task_costs([task], cfg), {"s_1": ANY})

    def test_run_envelope_uses_cost_worst(self):
        task = self._search_task("s_1", 5)
        seen = {}
        cfg = self.config(budget=1000, worst_case=True,
                          versions={"search": "v2"})

        def spy(task, client, model):
            seen["budget"] = client.tracker.budget
            return SolveResult(task_id=task.task_id, status=FAILED)

        cfg.solvers["search"] = spy
        with patch("solutions.runner.cost_worst_for", return_value=200.0):
            run([task], self.client_factory(), cfg)
        self.assertEqual(seen["budget"], 200.0)


class ResolverTest(unittest.TestCase):
    def test_all_types_share_version_layout(self):
        """Унифицированный API: solutions/<type>/<version>/solver.py."""
        for task_type, version in (("search", "v1"), ("math", "v2"),
                                   ("data_analysis", "v2"), ("coding", "v2")):
            self.assertEqual(
                resolve_solver(task_type, version).__module__,
                f"solutions.{task_type}.{version}.solver")

    def test_versioned_solver_alternatives(self):
        self.assertEqual(resolve_solver("math", "v1").__module__,
                         "solutions.math.v1.solver")
        self.assertEqual(resolve_solver("coding", "v1").__module__,
                         "solutions.coding.v1.solver")

    def test_missing_version_raises(self):
        with self.assertRaises(KeyError):
            resolve_solver("search", None)

    def test_unknown_type_raises(self):
        with self.assertRaises(KeyError):
            resolve_solver("poetry", "v2")


if __name__ == "__main__":
    unittest.main()
