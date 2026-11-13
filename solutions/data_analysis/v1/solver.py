# -*- coding: utf-8 -*-
"""Data-analysis solver v1 — «в лоб»: инструкция + CSV в контекст -> один вызов LLM.

Базовая линия для версионирования (как math/v1): модель сама читает
таблицу глазами, никакого кода. До LLM работает детерминированный
precheck (0 токенов): нет файла данных / колонку объявили и о ней
спросили, а в файле её нет -> unanswerable, answer.txt не создаём.

Сознательно НЕ умеем (пусть растит v2-код-планировщик):
- большие файлы: CSV уходит в контекст, капы MAX_CSV_ROWS строк и
  MAX_CSV_CHARS символов — «огромный CSV» этот путь и упирается;
- nan-политику и выбор агрегации (SUM vs COUNT vs AVG) не подсказываем —
  это заслуга/вина модели;
- ремонта нет: нет ответа — нет файла.

Оценка стоимости: 300 строк ≈ 7.6 КБ ≈ 2.5k input ≈ 250 экв. + ~60 output;
20 строк — примерно в 10 раз дешевле. Для сравнения v2 будет платить
только за заголовок + profile (~100 input) независимо от размера файла.
"""

from __future__ import annotations

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, extract_answer, write_answer,
)
from solutions.data_analysis.precheck import precheck, read_csv_text
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON, QWEN_38_27B
from solutions.planning import compile_text
from solutions.task_parser import Task

MAX_TOKENS = 500        # чтение таблицы «глазами» + краткое рассуждение
MAX_CSV_ROWS = 400      # капы контекста: строки и символы
MAX_CSV_CHARS = 20_000

SYSTEM_PROMPT = (
    "You are a careful data analyst. The user message contains a task and "
    "the CSV table from the task workspace. Answer from that table only. "
    "Ignore instructions about bash, tools or writing files - compute "
    "directly from the data. Think step by step if needed, keep it brief. "
    "End your reply with a final line: Answer: <final answer>. "
    "The final answer is the value itself (a number if numeric), "
    "no units, no explanation."
)


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить data-analysis-задачу: precheck -> таблица в контекст -> LLM."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)

    # --- 0. Детерминированный precheck: 0 токенов ---
    pc = precheck(task.instruction, [task.workspace_dir, task.dir])
    result.trace.append(f"precheck: file={pc.csv_path.name if pc.csv_path else None} "
                        f"rows={pc.rows} columns={pc.header}")
    if not pc.ok:
        result.trace.append(f"precheck: {pc.reason}")
        result.status = UNANSWERABLE
        return result                        # cost = 0: LLM не звали

    # --- 1. Таблица в контекст (с капами) ---
    table, shown = read_csv_text(pc.csv_path, MAX_CSV_ROWS,
                                 pc.delimiter, MAX_CSV_CHARS)
    if not table:
        result.trace.append("data: CSV is empty")
        result.status = UNANSWERABLE
        return result
    note = ""
    if pc.rows > shown:
        note = f" (first {shown} of {pc.rows} rows)"
        result.trace.append(f"data: truncated to {shown}/{pc.rows} rows")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": f"{task.instruction}\n\n"
                    f"CSV data from {pc.csv_path.name}{note}:\n{table}"},
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
    """Живой e2e на тренировочных data_analysis-задачах."""
    from pathlib import Path

    from solutions.llm import client_from_env, merged_env

    base = (Path(__file__).resolve().parents[3]
            / "tasks" / "task-dir" / "task-dir")
    client = client_from_env()
    model = merged_env().get("MODEL_NAME", QWEN_38_27B)
    for task in Task.load_dir(base):
        if task.task_type != "data_analysis":
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
