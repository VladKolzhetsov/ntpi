# -*- coding: utf-8 -*-
"""Детерминированная граница стоимости coding/v2 (cost_worst) — 0 токенов.

Лестница coding/v2:

    solve() = precheck + ПРЕГОН тестов (0 токенов, сабпроцесс)
            -> ошибка precheck: ВЫЗОВОВ НЕТ, стоимость ~0
            -> тесты уже зелёные (fast-path): ВЫЗОВОВ НЕТ, стоимость ~0
            -> цикл <= MAX_REPAIRS+1 вызовов:
                 промпт (system + инструкция + все .py + отчёт прегона)
                 -> completion <= reply_cap(target)
                 ремонт: + эхо ответа (<= REPLY_CHARS) + фидбэк
                         (упавшие тесты + хвост stderr)

Отличия от math/v2 и DA/v2:

1. КАП ДИНАМИЧЕСКИЙ: reply_cap = f(размер target-файла), при усечении —
   одноразовое повышение ×1.5. Худший путь: раунд 0 с капом C, оба
   ремонта с капом C+ = 1.5C + 1.
2. Прогон тестов для cost_worst НЕ делаем (медленный сабпроцесс, а его
   вклад — десятые доли границы): отчёт прегона оцениваем сверху по
   числу тест-методов в файлах. Fast-path (зелёные тесты) отсюда не
   виден — полный цикл покрывает и его (0 <= bound).

    C   = reply_cap(target)
    C+  = int(C * 1.5) + 1                     # после одноразового повышения
    I0  = OLS(system + инструкция + файлы + отчёт)
    echo/feedback — консервативные константы (капы солвера)
    cost_worst = (I0/10 + C) + (I1/10 + C+) + (I2/10 + C+)
    I1 = I0 + echo + feedback;  I2 = I1 + echo + feedback

Задачи, отсекаемые precheck'ом (нет тестов, чинить тесты, нет .py),
стоят ~0 LLM-токенов, но не ровно 0: плотность reward/cost делить на
ноль нельзя, потому граница — минимально положительная EPSILON_COST.

Проверка воспроизводимости: `python -m solutions.coding.v2.complexity`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from solutions.coding.tools import inspect_task
from solutions.search.v2.tokens import INPUT_BASE, INPUT_PER_CHAR
from solutions.task_parser import Task

# Задача, отсекаемая precheck'ом: LLM не зовётся, стоимость 0. Но плотность
# reward/cost_worst должна быть «очень выгодной», а не NaN — потому 1.0.
EPSILON_COST = 1.0

# Ремонтный фидбэк: список упавших + хвост stderr (tail_text 1500) + шапка.
FEEDBACK_CHARS = 2_500


def estimate_tokens(chars: int) -> int:
    """Оценка токенов по символам (OLS из search/v2/tokens)."""
    import math
    return math.ceil(INPUT_BASE + INPUT_PER_CHAR * chars)


@dataclass
class CodingComplexity:
    """Граница расхода coding/v2 на задачу; поля полезны в отчётах."""

    input_chars: int
    input_tokens: int
    cap: int
    cap_raised: int
    repairs: int
    cost_worst: float
    precheck_ok: bool
    reasons: list[str]


def _params():
    """Константы солвера (лениво — против циклического импорта)."""
    mod = sys.modules.get("solutions.coding.v2.solver")
    if mod is None:
        from solutions.coding.v2 import solver as mod      # noqa: PLC0415
    return (mod.SYSTEM_PROMPT, mod.build_user_prompt, mod.reply_cap,
            mod.REPLY_CHARS, mod.MAX_REPAIRS)


def _test_method_count(py_files: list) -> int:
    """Число тест-методов в файлах (для оценки отчёта прогона)."""
    n = 0
    for p in py_files:
        if p.name.startswith("test_") or "test" in p.name:
            try:
                n += len(re.findall(r"\bdef\s+test_\w+", p.read_text(
                    encoding="utf-8", errors="replace")))
            except OSError:
                pass
    return n


def estimate(task: Task) -> CodingComplexity:
    """Посчитать cost_worst для одной coding-задачи (0 токенов, без LLM)."""
    if task.task_type != "coding":
        raise ValueError("coding complexity estimator requires a coding task")

    (system_prompt, build_prompt, reply_cap, reply_chars,
     repairs) = _params()
    py_files, target, _cmd, error = inspect_task(
        task.instruction, task.workspace_dir)

    if error is not None:
        return CodingComplexity(
            input_chars=0, input_tokens=0, cap=0, cap_raised=0,
            repairs=0, cost_worst=EPSILON_COST, precheck_ok=False,
            reasons=[f"precheck cut the task: {error}", "no LLM call"])

    # Отчёт прогона: не исполняем тесты (медленно, вклад ничтожен) —
    # консервативная оценка по числу тест-методов.
    n_tests = _test_method_count(py_files)
    report_chars = 150 + 130 * n_tests
    user_chars = len(build_prompt(task.instruction, py_files, "")) \
        + report_chars
    input_chars = len(system_prompt) + user_chars
    input_tokens = estimate_tokens(input_chars)

    echo = estimate_tokens(reply_chars)          # эхо <= REPLY_CHARS
    feedback = estimate_tokens(FEEDBACK_CHARS)
    cap = reply_cap(target)
    cap_up = int(cap * 1.5) + 1                  # одноразовое повышение

    cost = input_tokens / 10 + cap
    reasons = [
        f"ladder: 1 call + <= {repairs} repair(s); no fallback",
        f"precheck ok: target={target.name}, {n_tests} test method(s)",
        f"input {input_chars} chars -> ~{input_tokens} tokens "
        f"(files + instruction + report bound)",
        f"cap C={cap}, raised C+={cap_up} for repairs after truncation",
    ]
    if repairs:
        i = input_tokens
        for _ in range(repairs):
            i += echo + feedback
            cost += i / 10 + cap_up
        reasons.append(f"echo <= {echo} tok, feedback <= {feedback} tok "
                       "per repair round")
    return CodingComplexity(
        input_chars=input_chars, input_tokens=input_tokens, cap=cap,
        cap_raised=cap_up, repairs=repairs, cost_worst=round(cost, 1),
        precheck_ok=True, reasons=reasons,
    )


def cost_worst(task: Task) -> float:
    """Контракт раннера: верхняя граница стоимости задачи (0 токенов)."""
    return estimate(task).cost_worst


def main() -> None:
    """CLI: граница по тренировочному набору (офлайн, без LLM)."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default="tasks/task-dir/task-dir")
    parser.add_argument("--only", nargs="*", help="task ids to inspect")
    args = parser.parse_args()
    tasks = Task.load_dir(args.tasks_dir)
    if not tasks and not Path(args.tasks_dir).is_dir():
        print(f"нет каталога {args.tasks_dir}", file=sys.stderr)
        return
    for task in tasks:
        if task.task_type != "coding":
            continue
        if args.only and task.task_id not in args.only:
            continue
        c = estimate(task)
        print(json.dumps({"task_id": task.task_id, **asdict(c)},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
