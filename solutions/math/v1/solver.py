# -*- coding: utf-8 -*-
"""Math solver v1 — «решение в лоб»: инструкция задачи -> один вызов LLM.

Первая версия фиксирует базовую линию для версионирования: промпт =
полная инструкция задачи + просьба закончить строкой «Answer: <значение>»,
затем детерминированное извлечение ответа и запись answer.txt.

Сознательно НЕ умеем (пусть растит следующие версии):
- исполнять код (python3) — работаем силами самой модели;
- читать файлы workspace (в math их нет);
- ретраи при неудачном извлечении ответа — нет ответа, нет файла.

Оценка стоимости: instruction ~80 ток. + system ~60 + ответ ~120-250
output ≈ 90-110 экв. на задачу.
"""

from __future__ import annotations

from solutions.base import (
    ANSWERED, FAILED, SolveResult, extract_answer, write_answer,
)
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON, QWEN_38_27B
from solutions.planning import compile_text
from solutions.task_parser import Task

MAX_TOKENS = 400        # рассуждения краткие, ответ — короткая строка

SYSTEM_PROMPT = (
    "You are a careful problem solver. Solve the task in the user message. "
    "Ignore instructions about bash, tools or writing files - solve it "
    "directly. Think step by step if needed, keep it brief. "
    "End your reply with a final line: Answer: <final answer>. "
    "The final answer is the value itself (a number if numeric), "
    "no units, no explanation."
)


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить math-задачу одним вызовом LLM и записать ответ."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task.instruction},
    ]
    try:
        text = compile_text(client, model, messages, max_tokens=MAX_TOKENS)
    except LLMError as e:
        result.trace.append(f"llm: {e}")
        result.cost = client.tracker.spent - spent0
        return result

    answer = extract_answer(text, task.check)
    if answer is None:
        result.trace.append("extract: no answer")
        result.cost = client.tracker.spent - spent0
        return result

    result.status = ANSWERED
    result.answer = answer
    result.trace.append(f"extract: {answer!r}")
    written, errors = write_answer(task, answer)
    result.written = written
    result.trace.extend(errors)
    result.cost = client.tracker.spent - spent0
    return result


def _main_demo() -> None:
    """Живой e2e на тренировочных math-задачах."""
    from pathlib import Path

    from solutions.llm import client_from_env, merged_env

    base = (Path(__file__).resolve().parents[3]
            / "tasks" / "task-dir" / "task-dir")
    client = client_from_env()
    model = merged_env().get("MODEL_NAME", QWEN_38_27B)
    for task in Task.load_dir(base):
        if task.task_type != "math":
            continue
        res = solve(task, client, model)
        verdict = "OK  " if res.answer == task.expected else (
            "MISS" if res.status == ANSWERED else res.status.upper())
        print(f"{res.task_id}: {verdict} status={res.status} "
              f"answer={res.answer!r} expected={task.expected!r} "
              f"cost={res.cost:.1f}")
        for line in res.trace:
            print(f"    {line}")


if __name__ == "__main__":
    _main_demo()
