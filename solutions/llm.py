# -*- coding: utf-8 -*-
"""Центральный LLM-клиент харнесса: OpenAI-совместимый прокси + учёт токенов.

Почему не клиент openJiuwen: детерминированным компонентам (планировщику)
нужен тонкий контроль — точный учёт usage, мягкий бюджет, дешёвые ретраи.
openJiuwen остаётся для агентного цикла (fallback-ветка), где нужны его
абстракции. Весь тратящий токены код локализован здесь и в тонких обёртках.

Учёт бюджета (регламент):
- бюджет задаётся в выходных токенах; 10 входных = 1 выходный
  (см. BudgetTracker.spent);
- reasoning-токенты считаются выходными. Предположение: прокси следует
  конвенции OpenAI и уже включает их в completion_tokens; отдельно
  пришедшие reasoning-поля сохраняются в LLMResponse.reasoning и raw
  для проверки на реальном прокси;
- кеш провайдера не учитывается: все входные токены считаются полностью.

Окружение:
- прогон (Docker): LLM_BASE_URL + LLM_API_KEY (+ опц. LLM_SSL_VERIFY,
  LLM_BUDGET в выходных токенах);
- локальная разработка: .env с API_BASE/API_KEY/MODEL_NAME
  (env-переменные побеждают над .env). 
"""

from __future__ import annotations

import os
import threading
import time
import urllib3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

# --- Разрешённые модели прокси (регламент) ---
QWEN_38_27B = "qwen3.8-27b"
QWEN_38_27B_NOREASON = "qwen3.8-27b-noreason"
QWEN_36_35B_A3B = "qwen3.6-35b-a3b"
QWEN_36_35B_A3B_NOREASON = "qwen3.6-35b-a3b-noreason"
ALL_MODELS = (QWEN_38_27B, QWEN_38_27B_NOREASON, QWEN_36_35B_A3B, QWEN_36_35B_A3B_NOREASON)


# ---------- Ошибки ----------

class LLMError(Exception):
    """Базовая ошибка вызова LLM."""


class LLMNetworkError(LLMError):
    """Сеть: прокси недоступен, таймаут. Кандидат на ретрай."""


class LLMResponseError(LLMError):
    """Прокси ответил мусором: HTTP != 2xx, битый JSON, пустой choices.

    status — HTTP-код, если ответ был получен (по нему принимают решение
    о фолбэке, напр. отключение response_format при 400).
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class LLMBudgetError(LLMError):
    """Клиентский (мягкий) бюджет исчерпан — до запроса не дошли."""


# ---------- Данные ----------

@dataclass
class Usage:
    """Расход одного вызова в токенах (по полям usage ответа)."""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def output_equivalents(self) -> float:
        """Расход в выходных токенах: completion + prompt/10."""
        return self.completion_tokens + self.prompt_tokens / 10


@dataclass
class LLMResponse:
    """Результат успешного вызова."""
    text: str                       # choices[0].message.content
    model: str
    usage: Usage
    reasoning: str | None = None    # reasoning_content, если отдаётся отдельно
    raw: dict = field(default_factory=dict)  # сырой ответ (диагностика формата)


# ---------- Учёт бюджета ----------

class BudgetTracker:
    """Центральный учёт расхода по всем вызовам. Потокобезопасен.

    budget задаётся в выходных токенах (output-эквивалентах); None —
    ограничения нет (локальная разработка).
    """

    def __init__(self, budget: float | None = None):
        self._budget = float(budget) if budget is not None else None
        # RLock: snapshot() читает свойства (spent/remaining), которые сами
        # берут лок — с обычным Lock получаем дедлок.
        self._lock = threading.RLock()
        self.calls = 0          # успешные вызовы
        self.failed_calls = 0   # упавшие (сеть/мусорный ответ)
        self.total_prompt = 0
        self.total_completion = 0

    def record(self, usage: Usage) -> None:
        """Зачислить расход успешного вызова."""
        with self._lock:
            self.calls += 1
            self.total_prompt += usage.prompt_tokens
            self.total_completion += usage.completion_tokens

    def note_failure(self) -> None:
        """Зафиксировать упавший вызов (токены могли списаться — сюрприз)."""
        with self._lock:
            self.failed_calls += 1

    @property
    def spent(self) -> float:
        """Израсходовано в выходных токенах: completion + prompt/10."""
        with self._lock:
            return self.total_completion + self.total_prompt / 10

    @property
    def budget(self) -> float | None:
        return self._budget

    @property
    def remaining(self) -> float | None:
        if self._budget is None:
            return None
        return max(0.0, self._budget - self.spent)

    @property
    def exceeded(self) -> bool:
        return self._budget is not None and self.spent >= self._budget

    def can_spend(self) -> bool:
        return not self.exceeded

    def snapshot(self) -> dict[str, Any]:
        """Сводка для логов/отчёта (в т.ч. для README про способ учёта)."""
        with self._lock:  # RLock: spent/remaining сами берут лок
            return {
                "calls": self.calls,
                "failed_calls": self.failed_calls,
                "prompt_tokens": self.total_prompt,
                "completion_tokens": self.total_completion,
                "spent_output_equivalents": round(self.spent, 1),
                "budget": self._budget,
                "remaining": None if self._budget is None else round(max(0.0, self._budget - self.spent), 1),
            }


# ---------- Клиент ----------

def _endpoint(base_url: str) -> str:
    """Нормализация базового URL: ожидаем, что версия (/v1) уже включена."""
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


class LLMClient:
    """Тонкий клиент OpenAI-совместимого /chat/completions."""

    def __init__(self, base_url: str, api_key: str,
                 tracker: BudgetTracker | None = None,
                 timeout: float = 60.0,
                 ssl_verify: bool = True,
                 session: requests.Session | None = None,
                 retry_delay: float = 1.0):
        self.endpoint = _endpoint(base_url)
        self.tracker = tracker if tracker is not None else BudgetTracker()
        self.timeout = timeout
        self.retry_delay = retry_delay
        # Opportunistic json-режим: если прокси не принял response_format
        # (HTTP 400), повторяем без него и больше не пробуем этой сессией.
        self.json_mode_supported = True
        # Логгер вызовов (только эксперименты, off by default):
        # None -> ноль I/O и ноль накладных расходов в боевом прогоне.
        self.call_logger = None
        # Инжектируемый session — для тестов (verify и заголовки уже делегированы)
        if session is not None:
            self._session = session
        else:
            self._session = requests.Session()
            self._session.verify = ssl_verify
            if not ssl_verify:
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def chat(self, model: str, messages: list[dict],
             temperature: float = 0.0,
             max_tokens: int | None = None,
             retries: int = 1,
             response_format: dict | None = None) -> LLMResponse:
        """Один вызов. Ретраи — только на сетевые ошибки (HTTP-ошибки не ретраим:
        прокси, отрезавший нас по бюджету, не лечится повторами).

        response_format (напр. {"type": "json_object"}) — opportunistic:
        фича работала на dev-прокси, но eval-прокси чужой, поэтому при
        HTTP 400 повторяем один раз без параметра и отключаем режим
        до конца сессии. Worst case = поведение без json-режима.

        Логирование (только эксперименты): если выставлен call_logger,
        каждый ЛОГИЧЕСКИЙ вызов (успех, ошибка транспорта, отказ по
        бюджету) записывается одной строкой JSONL — промпт, ответ,
        reasoning, usage, гиперпараметры. По умолчанию выключено.
        """
        logger = self.call_logger
        if logger is None:
            return self._chat(model, messages, temperature, max_tokens,
                              retries, response_format)
        t0 = time.monotonic()
        json_mode_before = self.json_mode_supported
        request = {
            "model": model, "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens,
            "response_format": response_format, "retries": retries,
        }
        try:
            resp = self._chat(model, messages, temperature, max_tokens,
                              retries, response_format)
        except LLMError as e:
            logger.log_call(request=request, response=None, error=e,
                            latency_ms=(time.monotonic() - t0) * 1000.0,
                            json_mode_fallback=(
                                json_mode_before
                                and not self.json_mode_supported
                                and response_format is not None))
            raise
        logger.log_call(request=request, response=resp, error=None,
                        latency_ms=(time.monotonic() - t0) * 1000.0,
                        json_mode_fallback=(
                            json_mode_before and not self.json_mode_supported
                            and response_format is not None))
        return resp

    def _budget_refusal(self) -> str:
        """Текст отказа по бюджету; различает мягкий гейт и жёсткий кап.

        Различие важно в трейсе: «soft gate» = задача уже потратила больше
        ожидаемого уровня и НЕ углубляется (ремонтные раунды закрыты);
        «hard cap» = исчерпан envelope/бюджет целиком. Оба приходят
        одним классом LLMBudgetError (солверы ловят его одинаково).
        """
        tracker = self.tracker
        soft = getattr(tracker, "soft_exceeded", None)
        if callable(soft) and soft():
            limit = getattr(tracker, "soft_limit", None)
            return (f"мягкий гейт задачи исчерпан: израсходовано "
                    f"{tracker.spent:.0f} из ожидаемых "
                    f"{limit:.0f} output-токенов (ремонт не углубляем)")
        return (f"мягкий бюджет исчерпан: израсходовано {tracker.spent:.0f} "
                f"из {tracker.budget:.0f} output-токенов")

    def _chat(self, model: str, messages: list[dict],
              temperature: float = 0.0,
              max_tokens: int | None = None,
              retries: int = 1,
              response_format: dict | None = None) -> LLMResponse:
        """Реализация вызова (транспорт, ретраи, json-фолбэк) без логирования."""
        if not self.tracker.can_spend():
            raise LLMBudgetError(self._budget_refusal())

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None and self.json_mode_supported:
            payload["response_format"] = response_format

        try:
            return self._chat_with_retries(payload, retries)
        except LLMResponseError as e:
            if "response_format" not in payload or e.status != 400:
                self.tracker.note_failure()
                raise
            # возможно, прокси не знает response_format -> раз пробуем без него
            self.json_mode_supported = False
            # не мутируем словарь: он может храниться по ссылке (трейсинг/тесты)
            retry_payload = {k: v for k, v in payload.items() if k != "response_format"}
            try:
                return self._chat_with_retries(retry_payload, retries)
            except (LLMNetworkError, LLMResponseError):
                self.tracker.note_failure()
                raise
        except LLMNetworkError:
            self.tracker.note_failure()
            raise

    # ----- внутреннее -----

    def _chat_with_retries(self, payload: dict, retries: int) -> LLMResponse:
        last_error: LLMError | None = None
        for attempt in range(retries + 1):
            try:
                status, data = self._post(payload)
            except (requests.ConnectionError, requests.Timeout) as e:
                last_error = LLMNetworkError(f"прокси недоступен: {e}")
                if attempt < retries:
                    time.sleep(self.retry_delay * (attempt + 1))
                    continue
                raise last_error from e
            if status >= 400 or "error" in data:
                raise LLMResponseError(
                    f"прокси ответил ошибкой (HTTP {status}): {str(data)[:300]}",
                    status=status)
            return self._parse(payload["model"], data)
        raise last_error or LLMError("недостижимая ветка")

    def _post(self, payload: dict) -> tuple[int, dict]:
        resp = self._session.post(self.endpoint, json=payload,
                                  headers=self._headers, timeout=self.timeout)
        try:
            data = resp.json()
        except ValueError as e:
            raise LLMResponseError(
                f"некорректный JSON от прокси (HTTP {resp.status_code}): "
                f"{resp.text[:200]!r}", status=resp.status_code) from e
        return resp.status_code, data

    def _parse(self, model: str, data: dict) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            raise LLMResponseError(f"пустой ответ (нет choices): {str(data)[:300]}")
        message = choices[0].get("message") or {}
        usage_raw = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_raw.get("prompt_tokens") or 0),
            completion_tokens=int(usage_raw.get("completion_tokens") or 0),
        )
        self.tracker.record(usage)
        return LLMResponse(
            text=message.get("content") or "",
            model=data.get("model") or model,
            usage=usage,
            reasoning=message.get("reasoning_content") or message.get("reasoning"),
            raw=data,
        )


# ---------- Фабрика из окружения ----------

def _load_dotenv(path: Path) -> dict[str, str]:
    """Минимальный парсер .env (без зависимости от python-dotenv)."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def merged_env() -> dict[str, str]:
    """Окружение процесса поверх .env проекта (env побеждает)."""
    base = Path(__file__).resolve().parents[1]
    return {**_load_dotenv(base / ".env"), **dict(os.environ)}


def client_from_env(tracker: BudgetTracker | None = None,
                    **kwargs: Any) -> LLMClient:
    """Клиент из окружения: LLM_BASE_URL/LLM_API_KEY (прогон), fallback на
    API_BASE/API_KEY из .env (разработка). Опционально LLM_BUDGET."""
    env = merged_env()
    url = env.get("LLM_BASE_URL") or env.get("API_BASE")
    key = env.get("LLM_API_KEY") or env.get("API_KEY")
    if not url or not key:
        # Диагностика вместо загадочного отказа: чаще всего в docker run
        # передали -e LLM_BASE_URL="$API_BASE", а переменная не была
        # экспортирована (в .env ключи называются API_BASE/API_KEY) —
        # тогда в контейнер уехала пустая строка.
        missing = "/".join(n for n, v in (("LLM_BASE_URL", url),
                                          ("LLM_API_KEY", key)) if not v)
        raise LLMError(
            f"нет настроек LLM: пусто {missing}.\n"
            "Ожидается LLM_BASE_URL/LLM_API_KEY в окружении (или "
            "API_BASE/API_KEY — они читаются из .env проекта).\n"
            "Docker: -e LLM_BASE_URL=\"$LLM_BASE_URL\" -e LLM_API_KEY=\"$LLM_API_KEY\"\n"
            "        (в .env локально ключи называются API_BASE/API_KEY — "
            "либо передайте --env-file .env, либо экспортируйте их "
            "перед docker run: set -a && . ./.env && set +a)\n"
            "        проверка: docker run --rm --env-file .env --entrypoint sh "
            "<образ> -c 'echo ${#API_KEY}'")
    ssl_verify = env.get("LLM_SSL_VERIFY", "true").strip().lower() != "false"
    if tracker is None:
        budget_raw = env.get("LLM_BUDGET")
        tracker = BudgetTracker(budget=float(budget_raw) if budget_raw else None)
    return LLMClient(url, key, tracker=tracker, ssl_verify=ssl_verify, **kwargs)


def main() -> None:
    """Живой smoke-тест: один крошечный вызов, печать usage и учёта."""
    env = merged_env()
    client = client_from_env()
    model = env.get("MODEL_NAME", QWEN_38_27B_NOREASON)
    print(f"endpoint: {client.endpoint}")
    print(f"model:    {model}")
    resp = client.chat(model,
                       [{"role": "user",
                         "content": 'Return the JSON object {"status": "OK"}'}],
                       temperature=0.0, max_tokens=100,
                       response_format={"type": "json_object"})
    print(f"text:     {resp.text!r}")
    print(f"reasoning: {resp.reasoning!r}")
    print(f"usage:    prompt={resp.usage.prompt_tokens} "
          f"completion={resp.usage.completion_tokens} "
          f"(output-экв. {resp.usage.output_equivalents:.1f})")
    print(f"tracker:  {client.tracker.snapshot()}")


if __name__ == "__main__":
    main()
