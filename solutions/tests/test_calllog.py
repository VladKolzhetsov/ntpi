# -*- coding: utf-8 -*-
"""Тесты логирования LLM-вызовов (off by default) — офлайн, без сети.

Проверяем обе стороны контракта:
- хук в LLMClient.chat (успех / ошибка транспорта / отказ по бюджету /
  json-фолбэк), выключенный по умолчанию = ноль I/O;
- calllog: формат JSONL, нумерация вызовов, контекст задачи;
- раннер: RunConfig.on_task проставляет контекст перед задачей и
  сбрасывает логгер после (иначе вызовы утекут в чужую задачу).
"""

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

import requests

from solutions.calllog import (
    CallLogger, render_call, solver_constants, summarize,
)
from solutions.llm import BudgetTracker, LLMBudgetError, LLMClient, LLMNetworkError
from solutions.runner import RunConfig, run
from solutions.task_parser import Task
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession
from solutions.tests.test_runner import LoopSession, make_task

CONN_ERROR = requests.ConnectionError("transport down")


def llm_text(text="ok", prompt=100, completion=20, reasoning=None):
    message = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return FakeResponse(data={
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    })


def http_400():
    return FakeResponse(status_code=400, data={"error": {"message": "bad"}})


class Env(unittest.TestCase):
    """tmpdir + фабрика клиента на FakeSession."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.sink = self.tmpdir / "calls.jsonl"

    def make_client(self, responses, logger=None, **kw):
        session = FakeSession(list(responses))
        client = LLMClient("https://proxy/v1", "k", session=session,
                           retry_delay=0, **kw)
        client.call_logger = logger
        return client, session

    def records(self):
        if not self.sink.exists():
            return []
        return [json.loads(line) for line in
                self.sink.read_text(encoding="utf-8").splitlines() if line]


class OffByDefaultTest(Env):

    def test_no_logger_no_files(self):
        client, _ = self.make_client([llm_text("hi")])
        resp = client.chat("m", [{"role": "user", "content": "q"}])
        self.assertEqual(resp.text, "hi")
        self.assertIsNone(client.call_logger)
        self.assertEqual(list(self.tmpdir.iterdir()), [])

    def test_logger_off_creates_no_file(self):
        # Даже если файл указан, но логгер не выставлен — ничего не пишем.
        self.make_client([llm_text("hi")])
        self.assertFalse(self.sink.exists())


class SuccessLogTest(Env):

    def test_single_call_record(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="math_0000", task_type="math",
                           solver="solutions.math.v2.solver",
                           model="qwen3.8-27b-noreason")
        client, _ = self.make_client([llm_text("42", prompt=100, completion=7)],
                                     logger=logger)
        client.chat("qwen3.8-27b-noreason",
                    [{"role": "user", "content": "2+2?"}],
                    max_tokens=400, temperature=0.0)

        recs = self.records()
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["task_id"], "math_0000")
        self.assertEqual(rec["task_type"], "math")
        self.assertEqual(rec["solver"], "solutions.math.v2.solver")
        self.assertEqual(rec["model"], "qwen3.8-27b-noreason")
        self.assertEqual(rec["call"], 0)                 # первый вызов задачи
        self.assertEqual(rec["error"], None)
        self.assertFalse(rec["json_mode_fallback"])
        self.assertEqual(rec["request"]["messages"],
                         [{"role": "user", "content": "2+2?"}])
        self.assertEqual(rec["request"]["max_tokens"], 400)
        self.assertEqual(rec["response"]["text"], "42")
        self.assertEqual(rec["response"]["usage"]["prompt_tokens"], 100)
        self.assertEqual(rec["response"]["usage"]["completion_tokens"], 7)
        self.assertEqual(rec["response"]["cost"], 17.0)  # 7 + 100/10
        self.assertGreaterEqual(rec["latency_ms"], 0.0)

    def test_reasoning_kept(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="math", solver="s")
        client, _ = self.make_client(
            [llm_text("4", reasoning="thought hard")], logger=logger)
        client.chat("m", [{"role": "user", "content": "q"}])
        self.assertEqual(self.records()[0]["response"]["reasoning"],
                         "thought hard")

    def test_call_numbers_grow_within_task(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="math", solver="s")
        client, _ = self.make_client([llm_text("a"), llm_text("b")],
                                     logger=logger)
        client.chat("m", [{"role": "user", "content": "1"}])
        client.chat("m", [{"role": "user", "content": "2"}])
        self.assertEqual([r["call"] for r in self.records()], [0, 1])


class ErrorPathTest(Env):

    def test_network_error_logged(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="math", solver="s")
        client, _ = self.make_client([CONN_ERROR, CONN_ERROR], logger=logger)
        with self.assertRaises(LLMNetworkError):
            client.chat("m", [{"role": "user", "content": "q"}])
        recs = self.records()
        self.assertEqual(len(recs), 1)                   # один ЛОГИЧЕСКИЙ вызов
        self.assertEqual(recs[0]["response"], None)
        self.assertEqual(recs[0]["error"]["type"], "LLMNetworkError")
        self.assertIn("transport down", recs[0]["error"]["message"])
        self.assertEqual(recs[0]["request"]["messages"][0]["content"], "q")

    def test_budget_refusal_logged_without_http(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="coding", solver="s")
        client, session = self.make_client([], logger=logger)
        client.tracker = BudgetTracker(budget=0)
        with self.assertRaises(LLMBudgetError):
            client.chat("m", [{"role": "user", "content": "q"}])
        recs = self.records()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["error"]["type"], "LLMBudgetError")
        self.assertEqual(session.calls, [])              # конверта не коснулись

    def test_json_mode_fallback_flagged(self):
        # 400 на payload с response_format -> повтор без него -> успех.
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="search", solver="s")
        client, session = self.make_client([http_400(), llm_text("{}")],
                                           logger=logger)
        resp = client.chat("m", [{"role": "user", "content": "q"}],
                           response_format={"type": "json_object"})
        self.assertEqual(resp.text, "{}")
        self.assertFalse(client.json_mode_supported)
        recs = self.records()
        self.assertEqual(len(recs), 1)                   # фолбэк = тот же вызов
        self.assertTrue(recs[0]["json_mode_fallback"])
        self.assertEqual(recs[0]["error"], None)
        self.assertNotIn("response_format", session.calls[1]["json"])


class RunnerHookTest(Env):

    def test_on_task_sets_and_resets_context(self):
        tasks = [
            make_task(self.tmpdir / "m_1", "m_1", "math", 5,
                      expected="42", check="exact_int"),
            make_task(self.tmpdir / "s_2", "s_2", "search", 2,
                      expected="A", check="exact_str"),
        ]
        logger = CallLogger(self.sink)
        made_clients = []

        def make_client():
            client = LLMClient("https://proxy/v1", "k",
                               session=LoopSession(completion=30),
                               retry_delay=0)
            made_clients.append(client)
            return client

        def on_task(client, task):
            logger.set_context(task_id=task.task_id, task_type=task.task_type,
                               solver=f"{task.task_type}/spy",
                               model="spy-model")
            client.call_logger = logger

        def spy_solver(task, client, model):
            client.chat(model, [{"role": "user", "content": task.task_id}])
            from solutions.base import FAILED, SolveResult
            return SolveResult(task_id=task.task_id, status=FAILED)

        config = RunConfig(model="spy-model", log=lambda msg: None,
                           workers=1, on_task=on_task)
        config.solvers = {t: spy_solver
                          for t in ("math", "search", "coding",
                                    "data_analysis")}
        run(tasks, make_client, config)

        recs = self.records()
        self.assertEqual(len(recs), 2)
        by_task = {r["task_id"]: r for r in recs}
        self.assertEqual(set(by_task), {"m_1", "s_2"})
        self.assertEqual(by_task["m_1"]["task_type"], "math")
        self.assertEqual(by_task["s_2"]["solver"], "search/spy")
        self.assertEqual(by_task["m_1"]["request"]["messages"][0]["content"],
                         "m_1")
        self.assertEqual([r["call"] for r in recs], [0, 0])  # счётчик на задачу
        # после прогона логгер снят: чужие вызовы не припишутся
        self.assertTrue(all(c.call_logger is None for c in made_clients))


class SolverConstantsTest(Env):

    def test_snapshot_from_module(self):
        class FakeModule:
            MAX_REPAIRS = 2
            CODE_TIMEOUT = 10
            SYSTEM_PROMPT = "long text"
            _PRIVATE = 1

        consts = solver_constants(FakeModule())
        self.assertEqual(consts["MAX_REPAIRS"], 2)
        self.assertEqual(consts["CODE_TIMEOUT"], 10)
        self.assertNotIn("SYSTEM_PROMPT", consts)
        self.assertNotIn("_PRIVATE", consts)


class SummaryAndRenderTest(Env):

    def test_summarize_groups_by_task(self):
        from solutions.calllog import CallRecord
        recs = [
            CallRecord(context={"task_id": "a", "model": "m1", "solver": "s"},
                       request={"messages": []},
                       response={"cost": 10.0, "usage": {}}),
            CallRecord(context={"task_id": "a", "model": "m1", "solver": "s"},
                       request={"messages": []},
                       response={"cost": 5.0, "usage": {}}),
            CallRecord(context={"task_id": "b", "model": "m2", "solver": "s"},
                       request={"messages": []}, response=None,
                       error={"type": "E", "message": "x"}),
        ]
        out = summarize(recs)
        self.assertEqual(out["calls"], 3)
        self.assertEqual(out["errors"], 1)
        self.assertEqual(out["cost"], 15.0)
        self.assertEqual(out["per_task"]["a"]["calls"], 2)
        self.assertEqual(out["per_task"]["a"]["cost"], 15.0)
        self.assertEqual(out["per_task"]["b"]["errors"], 1)

    def test_render_call_contains_prompt_and_output(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="math", solver="s",
                           model="m")
        client, _ = self.make_client([llm_text("ANSWER")], logger=logger)
        client.chat("m", [{"role": "user", "content": "PROMPT TEXT"}])
        text = render_call(logger.records[0])
        self.assertIn("PROMPT TEXT", text)
        self.assertIn("ANSWER", text)
        self.assertIn("usage:", text)

    def test_render_truncates_huge_prompt(self):
        logger = CallLogger(self.sink)
        logger.set_context(task_id="t", task_type="math", solver="s")
        client, _ = self.make_client([llm_text("x")], logger=logger)
        client.chat("m", [{"role": "user", "content": "A" * 50000}])
        text = render_call(logger.records[0])
        self.assertIn("обрезано", text)
        self.assertLess(len(text), 50000)

    def test_jsonl_survives_reopen(self):
        # Дозапись, а не перезапись: воркеры пишут в один файл.
        for i in range(3):
            logger = CallLogger(self.sink)
            logger.set_context(task_id=f"t{i}", task_type="math", solver="s")
            client, _ = self.make_client([llm_text(str(i))], logger=logger)
            client.chat("m", [{"role": "user", "content": f"q{i}"}])
        self.assertEqual([r["task_id"] for r in self.records()],
                         ["t0", "t1", "t2"])

    def test_concurrent_appends_are_whole_lines(self):
        # Один файл на несколько «воркеров»: строки не должны перемешиваться.
        shared = CallLogger(self.sink)
        clients = []
        for i in range(4):
            c, _ = self.make_client([llm_text("x" * 200)] * 5, logger=shared)
            clients.append(c)

        def worker(i):
            shared.set_context(task_id=f"t{i}", task_type="math", solver="s")
            for _ in range(5):
                clients[i].chat("m", [{"role": "user", "content": "q" * 100}])

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        lines = self.sink.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 20)
        for line in lines:                       # каждая строка — целый JSON
            rec = json.loads(line)
            self.assertEqual(rec["request"]["messages"][0]["content"], "q" * 100)
        self.assertEqual(shared.summary()["calls"], 20)

    def test_bound_keeps_context_per_worker(self):
        # bound(): контекст фиксирован на задачу — гонки нет даже когда
        # воркеры переплетаются (баг: вызовы math_0051 подписаны 0056)
        shared = CallLogger(self.sink)
        clients, loggers = [], []
        for i in range(4):
            child = shared.bound(task_id=f"t{i}", task_type="math",
                                 solver="s")
            loggers.append(child)
            c, _ = self.make_client([llm_text("x")] * 3, logger=child)
            clients.append(c)

        def worker(i):
            for _ in range(3):
                clients[i].chat("m", [{"role": "user", "content": "q"}])
                time.sleep(0.001)            # окно для перемешивания

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        per_task: dict[str, int] = {}
        for line in self.sink.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            per_task[rec["task_id"]] = per_task.get(rec["task_id"], 0) + 1
        self.assertEqual(per_task, {f"t{i}": 3 for i in range(4)})
        # номера вызовов считаются на задачу, а не на общий логгер
        for logger in loggers:
            self.assertEqual([r.context["call"] for r in logger.records
                              if r.context.get("task_id") ==
                              logger._context["task_id"]],
                             [0, 1, 2])
        self.assertEqual(shared.summary()["calls"], 12)   # records общие


if __name__ == "__main__":
    unittest.main()
