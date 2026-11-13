# -*- coding: utf-8 -*-
"""Детерминированная граница стоимости data_analysis/v2 (cost_worst) — 0 токенов.

Лестница DA/v2:

    solve() = precheck (0 токенов, детерминированный)
            -> если precheck не прошёл: ВЫЗОВОВ НЕТ, стоимость 0
            -> ВЫЗОВ 1 : system + (инструкция + имя файла + заголовок
                         + профиль колонок) -> completion <= cap
            -> максимум ОДИН ремонтный ВЫЗОВ 2 (MAX_REPAIRS=1):
                 промпт 1 + эхо ответа (text[:2000]) + сообщение об ошибке
                 -> completion <= cap
            -> fallback отсутствует

Отличие от math/v2 — **input не оценивается, а восстанавливается точно**:
user-промпт целиком строится детерминированно из файла задачи (имя,
заголовок, построчный профиль), поэтому `build_user_prompt` вызывается
здесь же. Побайтовое совпадение с солвером закреплено тестом — если формат
промпта изменится, тест упадёт раньше, чем граница станет врать.

Остаётся одна оценка — перевод символов в ТОКЕНЫ (OLS из search/v2/tokens).
Токенизатора под рукой нет, но input — это ~4% границы (output-кап задан
нами точно), так что ошибка оценки даёт доли процента.

    cost_worst = C1 + C2

    C1 = input1/10 + cap
    C2 = (input1 + echo + error_tokens)/10 + cap
    echo = min(cap, 500)

Никаких вероятностей: API физически не даст completion > cap, промпт
детерминирован, число раундов — константа модуля. `expected`/`reward` не
читаются: граница не зависит от того, верен ли ответ.

Проверка воспроизводимости: `python -m solutions.data_analysis.v2.complexity`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from solutions.data_analysis.precheck import precheck
from solutions.task_parser import Task

# Хвост эха ответа модели в ремонтном промпте: solver кладёт text[:2000]
# символов; консервативно считаем это ~500 токенами (как в math/v2).
ASSISTANT_ECHO_CHARS = 2000
ASSISTANT_ECHO_TOKENS = 500

# Задача, отсекаемая precheck'ом: LLM не зовётся, стоимость 0. Но раннер
# делит плотность reward/cost_worst — потому не ровно 0, а минимально
# положительная граница («почти бесплатно, решать первыми»).
EPSILON_COST = 1.0
# Тело инструктивного сообщения ремонта: "Your program failed: <error>. ...".
REPAIR_ERROR_TOKENS = 60

# Оценка input по символам (единственное приближение в формуле): параметры
# OLS из search/v2/tokens.
INPUT_BASE = 60.289
INPUT_PER_CHAR = 0.236765


def estimate_input_tokens(chars: int) -> int:
    """Оценка input-токенов по числу символов промпта (не токенизатор)."""
    return math.ceil(INPUT_BASE + INPUT_PER_CHAR * chars)


@dataclass
class DaComplexity:
    """Граница расхода data_analysis/v2 на задачу; поля полезны в отчётах."""

    input_chars: int
    input_tokens: int
    cap: int
    repairs: int
    cost_first: float
    cost_worst: float
    precheck_ok: bool
    reasons: list[str]


def _params() -> tuple[str, str, int, int]:
    """Константы солвера (лениво — против циклического импорта).

    solver импортирует этот модуль ради cost_worst, поэтому константы
    берём из уже загруженного модуля; при автономном запуске CLI
    импортируем его здесь.
    """
    mod = sys.modules.get("solutions.data_analysis.v2.solver")
    if mod is None:
        from solutions.data_analysis.v2 import solver as mod    # noqa: PLC0415
    return (mod.SYSTEM_PROMPT, mod.build_user_prompt,
            mod.MAX_TOKENS, mod.MAX_REPAIRS)


def estimate(task: Task) -> DaComplexity:
    """Посчитать cost_worst для одной DA-задачи (0 токенов, без LLM).

    Граница включает ДЕТЕРМИНИРОВАННЫЙ precheck: если он отсекает задачу
    (нет файла, нет колонки) — вызовов не будет и стоимость равна 0.
    Это не оптимизация «на удачу»: precheck решает до первого вызова.
    """
    if task.task_type != "data_analysis":
        raise ValueError(
            "data analysis complexity estimator requires a data_analysis task")

    system_prompt, build_prompt, cap, repairs = _params()
    pc = precheck(task.instruction, [task.workspace_dir, task.dir])

    if not pc.ok:
        return DaComplexity(
            input_chars=0, input_tokens=0, cap=cap, repairs=repairs,
            cost_first=0.0, cost_worst=EPSILON_COST, precheck_ok=False,
            reasons=[f"precheck cut the task: {pc.reason}", "no LLM call"])

    user = build_prompt(task.instruction, pc)
    input_chars = len(system_prompt) + len(user)
    input_tokens = estimate_input_tokens(input_chars)

    echo = min(cap, ASSISTANT_ECHO_TOKENS)       # эхо ответа ограничено cap
    c_first = input_tokens / 10 + cap
    c_repair = (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap
    cost = c_first + (c_repair if repairs else 0.0)

    reasons = [
        f"ladder: 1 call + <= {repairs} repair(s); no fallback",
        f"precheck ok: {pc.csv_path.name}, {pc.rows} rows, "
        f"{len(pc.columns)} profiled columns",
        f"input {input_chars} chars -> ~{input_tokens} tokens "
        f"(exact prompt, ols for chars->tokens)",
        f"output capped by MAX_TOKENS={cap}",
    ]
    if repairs == 0:
        reasons.append("MAX_REPAIRS=0: repair term dropped")
    return DaComplexity(
        input_chars=input_chars, input_tokens=input_tokens, cap=cap,
        repairs=repairs, cost_first=round(c_first, 1),
        cost_worst=round(cost, 1), precheck_ok=True, reasons=reasons,
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
        if task.task_type != "data_analysis":
            continue
        if args.only and task.task_id not in args.only:
            continue
        c = estimate(task)
        print(json.dumps({"task_id": task.task_id, **asdict(c)},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
