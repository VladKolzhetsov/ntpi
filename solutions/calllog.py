# -*- coding: utf-8 -*-
"""Запись вызовов LLM в JSONL — только для экспериментов.

В боевом прогоне логирование ВЫКЛЮЧЕНО: `LLMClient.call_logger = None`
(по умолчанию), и в `chat()` остаётся одна проверка `is None` — ни I/O,
ни токенов, ни задержек. Здесь живёт всё, что включается по флагу:
формат записи, запись в файл и текстовый рендер.

Зачем JSONL, а не «просто лог»: строка = один вызов LLM, из неё должны
восстанавливаться связи «задача × модель × версия солвера × номер вызова
× расход». Свободный текст такую связку теряет, а HTML-вьювер (позже)
строится по машинно-читаемому источнику в одну функцию.

Учёт: `usage` берём из ответа прокси, `cost` — в выходных эквивалентах
(`completion + prompt/10`, регламент соревнования).

Использование в эксперименте:

    from solutions.calllog import CallLogger

    logger = CallLogger(out_dir / "calls.jsonl")
    logger.set_context(task_id="math_0000", task_type="math",
                       solver="solutions.math.v2.solver", model="...")
    client.call_logger = logger
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Как обрезать очень длинные поля в ТЕКСТОВОМ рендере (JSONL не режем:
# это исходные данные). Промпты с дампом базы бывают на десятки тысяч
# символов — в консоль их целиком выводить бессмысленно.
TEXT_LIMIT = 4000

# Гиперпараметры, которые имеет смысл снимать с модулей солверов
# (UPPER_CASE-константы); список нужен только для отчёта о пропусках.
KNOWN_CONSTANTS = (
    "MAX_TOKENS", "MAX_REPAIRS", "CODE_TIMEOUT", "TEST_TIMEOUT",
    "REPLY_CHARS", "MAX_FALLBACK_CARDS", "UNSOLVABLE",
)


def solver_constants(module: Any) -> dict[str, Any]:
    """Снимок гиперпараметров модуля солвера (UPPER_CASE-константы).

    Автоматически, без списка на поддержке: что бы в солвере ни
    появилось (MAX_REPAIRS, CODE_TIMEOUT...), оно попадёт в лог
    заголовка эксперимента.
    """
    out: dict[str, Any] = {}
    for name in dir(module):
        if not name.isupper() or name.startswith("_"):
            continue
        if name in ("SYSTEM_PROMPT",):       # длинный текст — не константа настройки
            continue
        value = getattr(module, name)
        if isinstance(value, (int, float, str, bool, type(None))):
            out[name] = value
    return out


@dataclass
class CallRecord:
    """Одна запись лога: логический вызов LLM."""

    context: dict[str, Any]                 # task_id/type/solver/model/call
    request: dict[str, Any]                 # что послали (промпт, гиперпараметры)
    response: dict[str, Any] | None         # что получили (текст, reasoning, usage)
    error: dict[str, Any] | None = None     # класс/текст ошибки, если была
    latency_ms: float = 0.0
    json_mode_fallback: bool = False
    ts: float = field(default_factory=time.time)

    @property
    def cost(self) -> float:
        """Расход в выходных эквивалентах (completion + prompt/10)."""
        if not self.response:
            return 0.0
        return float(self.response.get("cost", 0.0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "task_id": self.context.get("task_id"),
            "task_type": self.context.get("task_type"),
            "solver": self.context.get("solver"),
            "call": self.context.get("call"),
            "model": self.context.get("model"),
            "latency_ms": round(self.latency_ms, 1),
            "json_mode_fallback": self.json_mode_fallback,
            "request": self.request,
            "response": self.response,
            "error": self.error,
        }


class CallLogger:
    """Пишет вызовы LLM строками JSONL. Потокобезопасен (воркеры раннера).

    Контекст задачи ставится перед её решением (`set_context`), счётчик
    вызовов ведётся per-context: первый вызов = 0, ремонтный = 1, и т.д. —
    так из лога видно, был ли ремонтный раунд, без правки солверов.
    """

    def __init__(self, path: str | Path, echo: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo
        self._lock = threading.Lock()
        self._context: dict[str, Any] = {}
        self._call_no = 0
        self.records: list[CallRecord] = []      # для тестов и живого разбора

    # ---------- контекст ----------

    def set_context(self, **context: Any) -> None:
        """Начать новую задачу: контекст + сброс счётчика вызовов.

        Только для однопоточного прогона: мутирует общий контекст «на
        бегу». При параллельных воркерах используйте `bound()` — иначе
        вызов задачи A подписывается task_id задачи B (гонка, поймана
        на прогоне math-adversarial при workers=3).
        """
        with self._lock:
            self._context = dict(context)
            self._call_no = 0

    def bound(self, **context: Any) -> "CallLogger":
        """Логгер с НЕИЗМЕНЯЕМЫМ контекстом задачи; файл и записи общие.

        Параллельным воркерам раздавайте по логгеру на задачу: у каждого
        свой контекст и счётчик вызовов, а замок, файл и `records`
        достаются от родителя — `summary()` видит всё.
        """
        child = CallLogger(self.path, echo=self.echo)
        child._lock = self._lock
        child.records = self.records
        child._context = dict(context)
        return child

    # ---------- точка перехвата ----------

    def log_call(self, request: dict[str, Any],
                 response: Any | None, error: Exception | None,
                 latency_ms: float = 0.0,
                 json_mode_fallback: bool = False) -> CallRecord:
        """Записать один вызов (вызывается из LLMClient.chat)."""
        with self._lock:
            context = dict(self._context)
            context["call"] = self._call_no
            self._call_no += 1
            record = CallRecord(
                context=context,
                request=_request_payload(request),
                response=_response_payload(response),
                error=({"type": type(error).__name__, "message": str(error)}
                       if error is not None else None),
                latency_ms=latency_ms,
                json_mode_fallback=json_mode_fallback,
            )
            self.records.append(record)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record.to_dict(), ensure_ascii=False)
                         + "\n")
        if self.echo:
            print(render_call(record))
        return record

    # ---------- сводки ----------

    def summary(self) -> dict[str, Any]:
        """Сводка по всем записанным вызовам (для отчёта эксперимента)."""
        with self._lock:
            records = list(self.records)
        return summarize(records)


def _request_payload(request: dict[str, Any]) -> dict[str, Any]:
    """Нормализовать запрос: сообщения + гиперпараметры вызова."""
    return {
        "model": request.get("model"),
        "messages": list(request.get("messages") or []),
        "temperature": request.get("temperature"),
        "max_tokens": request.get("max_tokens"),
        "response_format": request.get("response_format"),
        "retries": request.get("retries"),
    }


def _response_payload(response: Any | None) -> dict[str, Any] | None:
    """Нормализовать ответ: текст, reasoning, usage, расход."""
    if response is None:
        return None
    usage = getattr(response, "usage", None)
    return {
        "text": getattr(response, "text", ""),
        "reasoning": getattr(response, "reasoning", None),
        "model": getattr(response, "model", None),
        "usage": {
            "prompt_tokens": getattr(usage, "prompt_tokens", 0),
            "completion_tokens": getattr(usage, "completion_tokens", 0),
        },
        "cost": getattr(usage, "output_equivalents", 0.0),
    }


def summarize(records: list[CallRecord]) -> dict[str, Any]:
    """Сводка: вызовы, расход, ошибки, разбивка по задачам."""
    per_task: dict[str, dict[str, Any]] = {}
    for rec in records:
        task_id = rec.context.get("task_id") or "?"
        slot = per_task.setdefault(task_id, {
            "calls": 0, "cost": 0.0, "errors": 0,
            "models": set(), "solvers": set(),
        })
        slot["calls"] += 1
        slot["cost"] += rec.cost
        slot["errors"] += 1 if rec.error else 0
        if rec.context.get("model"):
            slot["models"].add(rec.context["model"])
        if rec.context.get("solver"):
            slot["solvers"].add(rec.context["solver"])
    return {
        "calls": len(records),
        "cost": round(sum(r.cost for r in records), 1),
        "errors": sum(1 for r in records if r.error),
        "latency_ms": round(sum(r.latency_ms for r in records), 1),
        "per_task": {
            task: {"calls": slot["calls"], "cost": round(slot["cost"], 1),
                   "errors": slot["errors"],
                   "models": sorted(slot["models"]),
                   "solvers": sorted(slot["solvers"])}
            for task, slot in sorted(per_task.items())
        },
    }


# ---------- текстовый рендер (для чтения глазами до появления HTML) ----------

def render_call(record: CallRecord) -> str:
    """Человекочитаемый блок одного вызова."""
    ctx = record.context
    head = (f"--- {ctx.get('task_id')} | {ctx.get('task_type')} | "
            f"call #{ctx.get('call')} | {ctx.get('model')} | "
            f"{record.latency_ms:.0f} ms | {record.cost:.1f} экв.")
    lines = [head, f"solver:   {ctx.get('solver')}"]
    for i, msg in enumerate(record.request.get("messages", [])):
        role = msg.get("role", "?")
        lines.append(f"[{i}] {role}:")
        lines.append(_cut(str(msg.get("content", ""))))
    lines.append("гиперпараметры: " + json.dumps(
        {k: v for k, v in record.request.items() if k != "messages"},
        ensure_ascii=False))
    if record.error:
        lines.append(f"ОШИБКА: {record.error.get('type')}: "
                     f"{record.error.get('message')}")
    if record.response:
        usage = record.response.get("usage", {})
        lines.append(f"usage: prompt={usage.get('prompt_tokens')} "
                     f"completion={usage.get('completion_tokens')} "
                     f"cost={record.cost:.1f} экв.")
        reasoning = record.response.get("reasoning")
        if reasoning:
            lines.append("рассуждения:")
            lines.append(_cut(reasoning))
        lines.append("выход:")
        lines.append(_cut(record.response.get("text", "")))
    return "\n".join(lines)


def _cut(text: str, limit: int = TEXT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n... [обрезано, всего {len(text)} символов]"
