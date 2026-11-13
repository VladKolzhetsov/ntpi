# -*- coding: utf-8 -*-
"""Детерминированная граница стоимости math/v2 (cost_worst) — 0 токенов.

Лестница math/v2 устроена проще поисковой, поэтому граница считается
точно из кода, без калибровок:

    solve() = детерминированные фильтры (0 токенов)
            -> ВЫЗОВ 1 : промпт (system + инструкция) -> completion <= cap
            -> максимум ОДИН ремонтный ВЫЗОВ 2 (MAX_REPAIRS=1):
                 промпт 1 + эхо ответа (text[:2000]) + сообщение об ошибке
                 -> completion <= cap
            -> fallback отсутствует (не написан / конвенция v2)

Варьируется ровно одно звено — был ли ответ модели безусловно неуспешен
(нет кода / код упал / stdout без ответа): тогда второе звено существует,
иначе нет. Верхнюю границу берём консервативно — считаем, что второе
звено ЕСТЬ. Никаких вероятностей: API физически не даст completion > cap,
промпты детерминированы, лимит раундов — константа модуля.

    cost_worst = C1 + C2

    C1 = input1/10 + cap
    C2 = (input1 + echo + error_tokens)/10 + cap

где input1 — оценка промпта первого вызова (только для input; сдвиг
оценки даёт ~1% к границе, output-кап — точный максимум, заданный нами).
Предсказание input по символам: OLS 60.289 + 0.236765 * chars (как в
search/v2/tokens; перенос на math даёт MAPE 23.8% — для input-слагаемого
это <= ~4% к cost_worst, на порядок меньше доминирующего output-капа).

Проверка воспроизводимости: `python -m solutions.math.v2.complexity`.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from solutions.task_parser import Task

# Хвост эха ответа модели в ремонтном промпте: solver кладёт text[:2000]
# символов; консервативно считаем это ~500 токенами (как search/v2).
ASSISTANT_ECHO_CHARS = 2000
ASSISTANT_ECHO_TOKENS = 500
# Тело инструктивного сообщения ремонта: "Your program failed: <error>. ...".
REPAIR_ERROR_TOKENS = 60

# Оценка input по символам (единственное приближение в формуле): параметры
# OLS из search/v2/tokens — свой OLS для math не строим, пока не появится
# много данных; смещение оценки мало против выходного капа.
INPUT_BASE = 60.289
INPUT_PER_CHAR = 0.236765


def estimate_input_tokens(chars: int) -> int:
    """Оценка input-токенов по числу символов промпта (не токенизатор)."""
    return math.ceil(INPUT_BASE + INPUT_PER_CHAR * chars)


@dataclass
class MathComplexity:
    """Граница расхода math/v2 на задачу; поля полезны в отчётах."""

    input_chars: int
    input_tokens: int
    cap: int
    repairs: int
    cost_first: float
    cost_worst: float
    reasons: list[str]


def _params() -> tuple[str, int, int]:
    """Константы солвера (лениво — против циклического импорта).

    solver импортирует этот модуль ради cost_worst, поэтому константы
    берём из уже загруженного модуля; при автономном запуске CLI
    импортируем его здесь.
    """
    mod = sys.modules.get("solutions.math.v2.solver")
    if mod is None:
        from solutions.math.v2 import solver as mod        # noqa: PLC0415
    return mod.SYSTEM_PROMPT, mod.MAX_TOKENS, mod.MAX_REPAIRS


def estimate(task: Task) -> MathComplexity:
    """Посчитать cost_worst для одной math-задачи (0 токенов, без LLM).

    expected/reward не читаются: граница не зависит от того, верен ли
    ответ. Читается только instruction (длина промпта) и константы кода.
    """
    if task.task_type != "math":
        raise ValueError("math complexity estimator requires a math task")

    system_prompt, cap, repairs = _params()
    input_chars = len(system_prompt) + len(task.instruction)
    input_tokens = estimate_input_tokens(input_chars)

    echo = min(cap, ASSISTANT_ECHO_TOKENS)       # эхо ответа ограничено cap
    c_first = input_tokens / 10 + cap
    c_repair = (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap
    cost_worst = c_first + (c_repair if repairs else 0.0)

    reasons = [
        f"ladder: 1 call + <= {repairs} repair(s); no fallback",
        f"input {input_chars} chars -> ~{input_tokens} tokens "
        f"(ols {INPUT_BASE:.3f}+{INPUT_PER_CHAR:.6f}*chars)",
        f"output capped by MAX_TOKENS={cap}",
    ]
    if repairs == 0:
        reasons.append("MAX_REPAIRS=0: repair term dropped")
    return MathComplexity(
        input_chars=input_chars, input_tokens=input_tokens, cap=cap,
        repairs=repairs,
        cost_first=round(c_first, 1),
        cost_worst=round(cost_worst, 1),
        reasons=reasons,
    )


def cost_worst(task: Task) -> float:
    """Контракт раннера: верхняя граница стоимости задачи (0 токенов).

    Runner (RunConfig.worst_case) ищет cost_worst(task) в модуле солвера
    и использует её для плотности reward/cost_worst и envelope.
    """
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
        if task.task_type != "math":
            continue
        if args.only and task.task_id not in args.only:
            continue
        c = estimate(task)
        print(json.dumps({"task_id": task.task_id, **asdict(c)},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
