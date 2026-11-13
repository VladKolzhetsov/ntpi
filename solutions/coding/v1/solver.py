# -*- coding: utf-8 -*-
"""Coding solver v1 — «в лоб»: инструкция + файлы workspace -> один вызов LLM.

Базовая линия для версионирования: модель видит задание и все .py-файлы
workspace (модуль с багами + корректные тесты) и должна вернуть полный
исправленный текст модуля. Никаких итераций, никакого фидбэка тестов —
чистый замер «чинит ли noreason баги по исходникам и ассертам за раз».

Детерминированные шаги (0 токенов, механика — в coding/tools.py):
- цель фикса и команда тестов парсятся из инструкции с fallback'ом на
  содержимое workspace («Run `python3 test_mylib.py`», «Fix the bugs in
  mylib.py»);
- отсутствие модуля/тестов/файла, названного в инструкции, -> задача
  сломана -> unanswerable до LLM;
- ответ валидируется ast.parse ДО записи: мусор не портит workspace;
- тесты исполняются НАШИМ сабпроцессом (cwd=workspace) — вердикт по
  exit-коду: 0 -> answer.txt = DONE (обе позиции), иначе failed и файла
  ответа нет. Тесты не трогаем никогда («do not edit the tests»).

Сознательно НЕ умеем (это сюжет v2): прегон тестов до вызова, ремонтный
цикл с фидбэком, детектор осцилляции, частичные патчи.

Оценка стоимости: instruction ~90 + 2 файла ~250 ток. + system ~70
input, полный модуль в ответе ~250-590 output ≈ 300-630 экв./задача.
"""

from __future__ import annotations

from pathlib import Path

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, write_answer,
)
from solutions.coding.tools import (
    ANSWER_DONE, extract_module, files_text, inspect_task, last_line,
    run_tests,
)
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON, QWEN_38_27B
from solutions.planning import compile_text
from solutions.task_parser import Task

MAX_TOKENS = 800          # полный исправленный модуль в ответе
TEST_TIMEOUT = 20         # секунды на прогон тестов

SYSTEM_PROMPT = (
    "You are a Python debugging expert. The user message contains a task "
    "and the Python files from the task workspace: a module with bugs and "
    "its test file. The tests are correct and must not be changed. "
    "Fix the module so that all its tests pass. "
    "Reply with the full corrected content of the module file only - no "
    "explanations, no markdown fences, no bash."
)


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить coding-задачу: один вызов LLM -> запись модуля -> тесты."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)

    # --- 0. Детерминированный precheck: 0 токенов ---
    py_files, target, cmd, error = inspect_task(task.instruction,
                                                 task.workspace_dir)
    if error is not None:
        result.trace.append(f"precheck: {error}")
        result.status = UNANSWERABLE
        return result
    result.trace.append(f"precheck: target={target.name} "
                        f"tests=\"{' '.join(cmd)}\"")

    # --- 1. Один вызов: инструкция + все .py-файлы в контексте ---
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content":
            f"{task.instruction}\n\nWorkspace files:\n"
            f"{files_text(py_files)}"},
    ]
    try:
        text = compile_text(client, model, messages, max_tokens=MAX_TOKENS)
    except LLMError as e:
        result.trace.append(f"llm: {e}")
        result.cost = client.tracker.spent - spent0
        return result

    # --- 2. Извлечь модуль, проверить синтаксис, записать ---
    code = extract_module(text)
    if code is None:
        result.trace.append("extract: reply is not a valid python module")
        result.cost = client.tracker.spent - spent0
        return result                      # мусор не пишем, workspace цел
    target.write_text(code, encoding="utf-8")
    result.trace.append(f"write: {target.name} ({len(code)} chars)")

    # --- 3. Тесты: наш сабпроцесс, вердикт по exit-коду ---
    proc, error = run_tests(cmd, target.parent, TEST_TIMEOUT)
    if error:
        result.trace.append(f"tests: {error}")
        result.cost = client.tracker.spent - spent0
        return result
    tail = last_line(proc.stderr or proc.stdout)
    if proc.returncode == 0:
        result.trace.append(f"tests: OK ({tail})")
        result.status = ANSWERED
        result.answer = ANSWER_DONE
        written, errors = write_answer(task, ANSWER_DONE)
        result.written = written
        result.trace.extend(errors)
    else:
        result.trace.append(f"tests: exit={proc.returncode} ({tail})")
        # ответа нет; mylib.py уже перезаписан нашей (недостаточной)
        # правкой — исходник был buggy, хуже не стало
    result.cost = client.tracker.spent - spent0
    return result


def _main_demo() -> None:
    """Живой e2e на тренировочных coding-задачах.

    Работаем на КОПИИ задачи в tmpdir: солвер перезаписывает mylib.py,
    а тренировочный набор должен оставаться воспроизводимым.
    """
    import shutil
    import tempfile

    from solutions.llm import client_from_env, merged_env

    base = (Path(__file__).resolve().parents[3]
            / "tasks" / "task-dir" / "task-dir")
    client = client_from_env()
    model = merged_env().get("MODEL_NAME", QWEN_38_27B)
    for task in Task.load_dir(base):
        if task.task_type != "coding":
            continue
        tmp = Path(tempfile.mkdtemp(prefix=f"coding_{task.task_id}_"))
        try:
            shutil.copytree(task.dir, tmp / task.task_id)
            task = Task.from_dir(tmp / task.task_id)
            res = solve(task, client, model)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        verdict = "OK  " if res.status == ANSWERED else res.status.upper()
        print(f"{res.task_id}: {verdict} status={res.status} "
              f"answer={res.answer!r} expected={task.expected!r} "
              f"cost={res.cost:.1f}")
        for line in res.trace:
            print(f"    {line}")


if __name__ == "__main__":
    _main_demo()
