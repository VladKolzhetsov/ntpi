# -*- coding: utf-8 -*-
"""Сборка search-пайплайна: Task -> answer.txt (ОБЕ позиции).

Лестница:

    1. plan      — SearchPlanner: вопрос + kb.schema() -> JSON-план (LLM).
       unanswerable -> сдаёмся (файл не создаём).
    2. execute   — детерминированный обход KB. None/исключение -> fallback.
    3. fallback  — single-shot QA по компактному дампу всей базы (LLM):
                   модель видит все имена сущностей и решает задачу линкинга
                   сама. Ответ NOT_FOUND / дамп сверх порога -> сдаёмся.

Ответ пишется в <task_dir>/answer.txt и <task_dir>/workspace/answer.txt
(обе позиции: регламент и инструкция задачи расходятся в том, где искать).
При unanswerable и полном провале файл НЕ создаём (решение команды):
пустой/мусорный ответ гарантированно провалит проверку, а «нет файла»
в отличие от него не портит статистику ложными попытками.
"""

from __future__ import annotations

import os
from pathlib import Path

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, write_answer,
)
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON
from solutions.planning import compile_text
from solutions.question import extract_question
from solutions.search.v2.executor import execute
from solutions.search.v2.kb import KnowledgeBase
from solutions.search.v2.planner import MAX_PLAN_TOKENS, SearchPlanner
from solutions.task_parser import Task
from solutions.search.v2.complexity import estimate, estimate_task
from solutions.search.v2.tokens import affordable_cap

NOT_FOUND = "NOT_FOUND"        # маркер «в фактах нет ответа» из fallback-QA

# Дамп базы больше этого числа карточек -> fallback-QA не вызываем
# (промпт O(размер базы); на тренировочных базах ~38 карточек).
# Порог поднимем/заменим на отбор, когда появятся большие базы.
MAX_FALLBACK_CARDS = 400

QA_SYSTEM_PROMPT = (
    "Answer the question using ONLY the facts below. "
    "Reply with the exact answer value only - no explanation, no punctuation, "
    "no full sentence. If the facts do not contain the answer, "
    "reply exactly: NOT_FOUND"
)


def cost_worst(task: Task) -> float:
    """Верхняя граница полной стоимости задачи (контракт раннера).

    Runner (RunConfig.worst_case) ищет cost_worst(task) в модуле солвера
    и использует границу для плотности reward/cost_worst и envelope.
    Детерминированная, до первого вызова, 0 токенов; формула — в
    complexity._cost_worst, валидация — CALIBRATION.md.
    """
    return estimate_task(task).cost_worst


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON, *,
           max_tokens: int | None = None) -> SolveResult:
    """Прогнать одну search-задачу через лестницу и записать ответ."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)
    question = extract_question(task.instruction)
    result.trace.append(f"question: {question!r}")

    # --- 0. База знаний ---
    kb_dir = (task.workspace_dir / "kb") if task.workspace_dir else None
    kb = KnowledgeBase.from_dir(kb_dir) if kb_dir else KnowledgeBase([])
    result.trace.append(f"kb: {len(kb)} nodes, healthy={kb.is_healthy()}")

    if kb.is_healthy():
        # --- 1. Планировщик ---
        prediction = estimate(question, kb.schema(), kb=kb)
        override = max_tokens is not None
        static_mode = os.environ.get("SEARCH_V2_CAP_MODE", "dynamic").casefold() == "static"
        if override:
            cap, cap_source = max_tokens, "override"
        elif static_mode:
            cap, cap_source = MAX_PLAN_TOKENS, "static"
        else:
            cap, cap_source = prediction.recommended_max_tokens, "complexity"
        result.trace.append(
            f"complexity: steps={prediction.predicted_steps}, input≈{prediction.predicted_input_tokens}, "
            f"output≈{prediction.predicted_output_tokens}, cost≈{prediction.predicted_cost}, "
            f"worst≈{prediction.cost_worst}, confidence={prediction.confidence}, "
            f"max_tokens={cap}, source={cap_source}")
        outcome = SearchPlanner(client, model, max_tokens=cap,
                                predicted_output_tokens=prediction.predicted_output_tokens).plan(question, kb)
        result.trace.extend(outcome.trace)
        if outcome.unanswerable:
            result.trace.append("plan: unanswerable")
            result.status = UNANSWERABLE
            result.cost = client.tracker.spent - spent0
            return result
        if outcome.plan is not None:
            result.trace.append(f"plan: {len(outcome.plan.steps)} steps")
            # --- 2. Исполнитель ---
            try:
                answer = execute(outcome.plan, kb)
            except Exception:                    # noqa: BLE001 — fallback на любой сбой
                result.trace.append("executor: exception")
                answer = None
            if answer is not None:
                result.trace.append(f"executor: answer={answer!r}")
                return _finish(result, task, answer, client, spent0)
            result.trace.append("executor: no answer")
        else:
            result.trace.append(f"plan: failed ({outcome.error})")

    # --- 3. Fallback: single-shot QA по дампу базы ---
    if not len(kb):
        result.trace.append("fallback: empty kb, nothing to ask")
        result.status = FAILED
        result.cost = client.tracker.spent - spent0
        return result
    if len(kb) > MAX_FALLBACK_CARDS:
        result.trace.append(
            f"fallback: kb too big ({len(kb)} > {MAX_FALLBACK_CARDS})")
        result.status = FAILED
        result.cost = client.tracker.spent - spent0
        return result

    try:
        answer = _fallback_qa(client, model, question, kb)
    except LLMError as e:
        result.trace.append(f"fallback: llm failed ({e})")
        answer = None
    if answer is None:
        result.trace.append("fallback: no usable answer")
    elif answer == NOT_FOUND:
        result.trace.append("fallback: NOT_FOUND")
    else:
        result.trace.append(f"fallback: answer={answer!r}")
        return _finish(result, task, answer, client, spent0)

    result.status = FAILED
    result.cost = client.tracker.spent - spent0
    return result


# ---------- Внутреннее ----------

def _finish(result: SolveResult, task: Task, answer: str,
            client: LLMClient, spent0: float) -> SolveResult:
    """Записать ответ в ОБЕ позиции answer.txt и закрыть результат."""
    result.status = ANSWERED
    result.answer = answer
    written, errors = write_answer(task, answer)
    result.written = written
    result.trace.extend(errors)
    result.cost = client.tracker.spent - spent0
    return result


def _fallback_qa(client: LLMClient, model: str,
                 question: str, kb: KnowledgeBase) -> str | None:
    """Single-shot QA по компактному дампу всех карточек.

    Возвращает очищенный ответ, NOT_FOUND или None (LLM не ответила).
    """
    dump = _dump_cards(kb)
    messages = [
        {"role": "system", "content": QA_SYSTEM_PROMPT},
        {"role": "user", "content":
            f"Question: {question}\n\nFacts:\n{dump}"},
    ]
    cap = affordable_cap(60, 60, messages, client.tracker.remaining)
    if cap is None:
        return None
    text = compile_text(client, model, messages, max_tokens=cap)
    answer = text.strip().strip("`\"' \t")
    # замыкающую точку убираем: модели часто добавляют её к короткому ответу.
    # (трейд-офф: значение, genuinely оканчивающееся на '.', редки — "Inc.")
    for _ in range(3):                    # кавычка может стоять и после точки
        answer = answer.strip("`\"' \t")
        if answer.endswith("."):
            answer = answer[:-1].rstrip()
    if not answer:
        return None
    if NOT_FOUND in answer and len(answer) <= len(NOT_FOUND) + 8:
        return NOT_FOUND
    return answer


def _dump_cards(kb: KnowledgeBase) -> str:
    """Компактный дамп базы для fallback-QA: одна строка на карточку.

    person: Irina Smith | Born=1983, Employer=Fjord Data
    """
    lines: list[str] = []
    for node in kb:
        attrs = ", ".join(f"{k}={v}" for k, v in sorted(node.attrs.items()))
        lines.append(f"{node.type}: {node.name} | {attrs}" if attrs
                     else f"{node.type}: {node.name}")
    return "\n".join(lines)


def _main_demo() -> None:
    """Живой e2e-прогон на тренировочных задачах."""
    from solutions.llm import client_from_env, merged_env

    # parents[3]: solutions/search/v2/solver.py -> корень проекта
    base = (Path(__file__).resolve().parents[3]
            / "tasks" / "task-dir" / "task-dir")
    client = client_from_env()
    model = merged_env().get("MODEL_NAME", QWEN_38_27B_NOREASON)
    for task in Task.load_dir(base):
        if task.task_type != "search":
            continue
        res = solve(task, client, model)
        verdict = "OK  " if (res.answer == task.expected) else (
            "MISS" if res.status == ANSWERED else res.status.upper())
        print(f"{res.task_id}: {verdict} status={res.status} "
              f"answer={res.answer!r} cost={res.cost:.1f} "
              f"written={len(res.written)}")
        for line in res.trace:
            print(f"    {line}")


if __name__ == "__main__":
    _main_demo()
