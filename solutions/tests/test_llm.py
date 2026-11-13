# -*- coding: utf-8 -*-
"""Тесты solutions/llm.py — всё на фейковом транспорте, без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.test_llm -v
"""

import unittest

import requests

from solutions.llm import (BudgetTracker, LLMBudgetError, LLMClient,
                            LLMNetworkError, LLMResponseError, LLMResponse,
                            Usage, _endpoint, _load_dotenv)
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession


def ok_response(prompt=100, completion_tokens=20, text="OK", reasoning=None):
    """Успешный ответ в формате OpenAI-совместимого прокси."""
    message = {"role": "assistant", "content": text}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return FakeResponse(data={
        "model": "test-model",
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion_tokens},
    })


class ChatTest(unittest.TestCase):
    def _client(self, *responses, **kwargs):
        session = FakeSession(list(responses))
        client = LLMClient("https://proxy.example/v1", "sk-test",
                           session=session, **kwargs)
        return client, session

    def test_chat_ok_and_accounting(self):
        client, session = self._client(ok_response(prompt=100, completion_tokens=20))
        resp = client.chat("m", [{"role": "user", "content": "hi"}])
        self.assertIsInstance(resp, LLMResponse)
        self.assertEqual(resp.text, "OK")
        self.assertEqual(resp.usage, Usage(100, 20))
        # 100/10 + 20 = 30 output-эквивалентов
        self.assertAlmostEqual(client.tracker.spent, 30.0)
        self.assertEqual(client.tracker.calls, 1)
        # payload корректен
        sent = session.calls[0]["json"]
        self.assertEqual(sent["model"], "m")
        self.assertEqual(sent["temperature"], 0.0)
        self.assertFalse(sent["stream"])
        self.assertNotIn("max_tokens", sent)

    def test_max_tokens_passed(self):
        client, _ = self._client(ok_response())
        client.chat("m", [{"role": "user", "content": "hi"}], max_tokens=200)
        self.assertEqual(self_payload(client)["max_tokens"], 200)

    def test_reasoning_field_captured(self):
        client, _ = self._client(ok_response(reasoning="думаю..."))
        resp = client.chat("m", [{"role": "user", "content": "hi"}])
        self.assertEqual(resp.reasoning, "думаю...")
        self.assertEqual(resp.text, "OK")

    def test_http_error_is_not_retried(self):
        client, session = self._client(
            FakeResponse(status_code=500, data={"error": "boom"}))
        with self.assertRaises(LLMResponseError):
            client.chat("m", [{"role": "user", "content": "hi"}], retries=3)
        self.assertEqual(len(session.calls), 1)          # ретраев нет
        self.assertEqual(client.tracker.failed_calls, 1)
        self.assertEqual(client.tracker.calls, 0)

    def test_bad_json(self):
        client, _ = self._client(FakeResponse(status_code=200, data=None,
                                              text="not json at all"))
        with self.assertRaises(LLMResponseError):
            client.chat("m", [{"role": "user", "content": "hi"}])

    def test_empty_choices(self):
        client, _ = self._client(FakeResponse(data={"choices": []}))
        with self.assertRaises(LLMResponseError):
            client.chat("m", [{"role": "user", "content": "hi"}])

    def test_network_error_then_success(self):
        client, session = self._client(
            requests.ConnectionError("reset"),
            ok_response(prompt=10, completion_tokens=5),
            retry_delay=0,
        )
        resp = client.chat("m", [{"role": "user", "content": "hi"}], retries=1)
        self.assertEqual(resp.text, "OK")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(client.tracker.calls, 1)        # один успешный
        self.assertEqual(client.tracker.failed_calls, 0)

    def test_network_error_after_retries(self):
        client, session = self._client(requests.Timeout("t"))
        with self.assertRaises(LLMNetworkError):
            client.chat("m", [{"role": "user", "content": "hi"}], retries=0)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(client.tracker.failed_calls, 1)

    def test_soft_budget_blocks_before_http(self):
        tracker = BudgetTracker(budget=10.0)
        tracker.record(Usage(prompt_tokens=100, completion_tokens=0))  # уже 10 экв.
        session = FakeSession([])                        # ни одного ответа
        client = LLMClient("https://p/v1", "k", tracker=tracker, session=session)
        with self.assertRaises(LLMBudgetError):
            client.chat("m", [{"role": "user", "content": "hi"}])
        self.assertEqual(len(session.calls), 0)          # в сеть не пошли
        self.assertEqual(client.tracker.calls, 1)        # только предзаполненный
        self.assertEqual(client.tracker.failed_calls, 0) # бюджет — не failure


def self_payload(client):
    """Payload последнего отправленного запроса клиента."""
    return client._session.calls[-1]["json"]


class JsonModeTest(unittest.TestCase):
    """Opportunistic response_format: в payload, фолбэк при 400, флаг сессии."""

    def _client(self, *responses):
        session = FakeSession(list(responses))
        client = LLMClient("https://p/v1", "k", session=session, retry_delay=0)
        return client, session

    def test_response_format_in_payload(self):
        client, session = self._client(ok_response())
        client.chat("m", [{"role": "user", "content": "return JSON"}],
                    response_format={"type": "json_object"})
        self.assertEqual(session.calls[0]["json"]["response_format"],
                         {"type": "json_object"})
        self.assertTrue(client.json_mode_supported)

    def test_fallback_on_400(self):
        client, session = self._client(
            FakeResponse(status_code=400, data={"error": "unknown parameter"}),
            ok_response(),
        )
        resp = client.chat("m", [{"role": "user", "content": "return JSON"}],
                           response_format={"type": "json_object"})
        self.assertEqual(resp.text, "OK")
        self.assertEqual(len(session.calls), 2)
        self.assertIn("response_format", session.calls[0]["json"])
        self.assertNotIn("response_format", session.calls[1]["json"])
        self.assertFalse(client.json_mode_supported)
        self.assertEqual(client.tracker.calls, 1)        # 400 не в счёт

    def test_json_mode_disabled_for_followups(self):
        client, session = self._client(
            FakeResponse(status_code=400, data={"error": "unknown parameter"}),
            ok_response(),
            ok_response(prompt=10, completion_tokens=5),
        )
        client.chat("m", [{"role": "user", "content": "return JSON"}],
                    response_format={"type": "json_object"})
        client.chat("m", [{"role": "user", "content": "return JSON"}],
                    response_format={"type": "json_object"})
        # третий вызов (index 2) идёт уже без response_format
        self.assertNotIn("response_format", session.calls[2]["json"])

    def test_plain_400_without_format_still_raises(self):
        client, session = self._client(
            FakeResponse(status_code=400, data={"error": "bad model"}))
        with self.assertRaises(LLMResponseError) as ctx:
            client.chat("m", [{"role": "user", "content": "hi"}])
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(len(session.calls), 1)          # никакого повтора


class BudgetTrackerTest(unittest.TestCase):
    def test_math(self):
        t = BudgetTracker()
        t.record(Usage(prompt_tokens=1000, completion_tokens=100))
        t.record(Usage(prompt_tokens=500, completion_tokens=50))
        # (1000+500)/10 + (100+50) = 300
        self.assertAlmostEqual(t.spent, 300.0)
        self.assertEqual(t.calls, 2)

    def test_budget_limits(self):
        t = BudgetTracker(budget=250)
        t.record(Usage(prompt_tokens=1000, completion_tokens=100))     # 200
        self.assertTrue(t.can_spend())
        self.assertAlmostEqual(t.remaining, 50.0)
        t.record(Usage(prompt_tokens=1000, completion_tokens=100))     # ещё 200 -> 400
        self.assertTrue(t.exceeded)
        self.assertFalse(t.can_spend())
        self.assertAlmostEqual(t.remaining, 0.0)         # не уходим в минус

    def test_no_budget_always_can(self):
        t = BudgetTracker()
        t.record(Usage(prompt_tokens=10**9, completion_tokens=10**9))
        self.assertTrue(t.can_spend())

    def test_snapshot(self):
        t = BudgetTracker(budget=100)
        t.record(Usage(prompt_tokens=50, completion_tokens=10))
        t.note_failure()
        snap = t.snapshot()
        self.assertEqual(snap["calls"], 1)
        self.assertEqual(snap["failed_calls"], 1)
        self.assertEqual(snap["prompt_tokens"], 50)
        self.assertEqual(snap["completion_tokens"], 10)
        self.assertAlmostEqual(snap["spent_output_equivalents"], 15.0)
        self.assertAlmostEqual(snap["remaining"], 85.0)


class MiscTest(unittest.TestCase):
    def test_endpoint_normalization(self):
        self.assertEqual(_endpoint("https://x/v1/"), "https://x/v1/chat/completions")
        self.assertEqual(_endpoint("https://x/v1"), "https://x/v1/chat/completions")
        self.assertEqual(_endpoint("https://x/v1/chat/completions"),
                         "https://x/v1/chat/completions")

    def test_load_dotenv_skips_comments(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / ".env"
            p.write_text("# comment\nA=1\n\nB = spaced \nNOEQ\n", encoding="utf-8")
            values = _load_dotenv(p)
        self.assertEqual(values, {"A": "1", "B": "spaced"})


if __name__ == "__main__":
    unittest.main()
