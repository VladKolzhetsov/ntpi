# -*- coding: utf-8 -*-
"""Math solver v2 — код-планировщик: NL -> python-программа -> исполнение.

Против v1 («в лоб», 250–355 экв. на reason-модели): арифметика уходит
в детерминированный python, LLM платит только за трансляцию задачи в код
(noreason, без скрытых reasoning-токенов). Ошибка исполнения -> один
ремонтный раунд: модель видит stderr и исправляет программу.

Поток: instruction -> [LLM: python-код] -> python3 (сабпроцесс, таймаут)
-> stdout -> чистка по check -> answer.txt (обе позиции; при провале
файл не создаём). Fallback при полном провале — нет (модель, не умеющая
запрограммировать арифметическую задачу, не решит её и прямым ответом:
тратиться на v1-ветку бессмысленно).
"""

from __future__ import annotations

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, write_answer,
)
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON
from solutions.math.v2.complexity import cost_worst as estimate_cost_worst
from solutions.math.v2.detectors import detect_unsolvable
from solutions.planning import compile_response
from solutions.pyrun import clean_output_answer, extract_code, run_python
from solutions.task_parser import Task

MAX_TOKENS = 400        # программа + возможный repairs-ответ
MAX_REPAIRS = 1         # раундов с stderr в контексте
CODE_TIMEOUT = 10       # секунды на исполнение программы

# Болтливость (7/143 первых вызовов): модель жжёт весь кап на рассуждениях в
# комментариях, не написав ни одного print(). Ущерб — не потерянные токены,
# а ПОТЕРЯННАЯ ЗАДАЧА (решаемые падают: math_0010 -> FAIL за 446 экв.).
# Лечение: усечение детектируется (completion >= cap и кода нет) — ремонт
# НЕ несёт эхо болтовни (это шум), идёт с пониженным капом и жёстким
# «только код». cost_worst не меняется: обычный ремонт (эхо + кап 400)
# остаётся худшим путём.
TRUNCATION_REPAIR_CAP = 250
# эхо усечённой болтовни: достаточно понять, ЧТО было не так (400 символов
# вместо 2000 — экономия ~35 экв. на ремонте)
_TRUNCATION_ECHO_CHARS = 400

# маркер отказа: данных не хватает / противоречие — задача нерешаема
UNSOLVABLE = "UNSOLVABLE"

SYSTEM_PROMPT = (
    "You are a Python programmer. The user message is a word problem. "
    "Write a Python program that computes the answer and prints it with "
    "print(). Python 3, standard library only, no input(), no network, "
    "no files. Reply with the program only - no markdown fences, no bash. "
    # Диагноз (2026-11-12): 64% символов ответов math — это '#'-комментарии,
    # в которых модель ведёт всю алгебру. У задач-«болтунов» (0043/0010/0055)
    # комментарии съедали 223–344 из 400 токенов, и верный вывод оставался
    # недооформленным: 0043 доходила до «корней нет» и обрезалась на капе,
    # роняя задачу. Запрет повторён и подкреплён примером: модель охотно
    # трактует «не объясняй» как «объясняй кратко».
    "Output the code FIRST: the reply must start with a Python statement "
    "(import/code), not with notes. Write NO comments at all - no '#' "
    "lines, no reasoning in prose, no restating the problem. Example of an "
    "acceptable reply (nothing before the code): n = 1752\\nn = n // 2\\n"
    "print(n). The program is executed directly, so never wrap it in "
    "python3 -c or bash commands. The program must be short and "
    "deterministic. "
    # правила, за которые заплатил adversarial-пакет (2026-11-12):
    "Do the arithmetic in the code - never print a number you guessed. "
    "Rounding rules: 'nearest' means half-up (x.5 away from zero), not "
    "Python's round(); use int(x + 0.5) for positive numbers (and "
    "math.floor/ceil for 'round down/up'). 'Discarding any fractional "
    "part' rounds the RESULT of the operation, not the amount moved: "
    "a stock of n increased by p percent becomes int(n * (1 + p/100)); "
    "a stock of n that ships p percent and discards the fraction becomes "
    "int(n * (1 - p/100)) - compute the amount that REMAINS and floor it, "
    "do not floor the shipped amount and subtract it. " 
    "If the problem fixes the output format (a number of decimal places), "
    "print exactly that many digits: 1.2 formatted to 3 places is "
    "print(f'{value:.3f}') -> 1.200. "
    "If the problem cannot be solved because required data is missing or "
    "contradictory, reply with exactly: " + UNSOLVABLE
)


def _is_unsolvable_reply(text: str) -> bool:
    """Модель отказалась: первая строка ответа — маркер UNSOLVABLE.

    Терпим короткое пояснение после маркера («UNSOLVABLE: missing days»);
    длинный текст с кодом (print() есть) отказом не считается.
    """
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.splitlines()[0].strip().upper()
    return first.startswith(UNSOLVABLE) and len(first) <= len(UNSOLVABLE) + 40


def cost_worst(task: Task) -> float:
    """Верхняя граница полной стоимости задачи (контракт раннера).

    Runner (RunConfig.worst_case) ищет cost_worst(task) в модуле солвера
    и использует границу для плотности reward/cost_worst и envelope.
    Детерминированная, до первого вызова, 0 токенов; формула — в
    complexity.py, валидация — solutions/math/README.md.
    """
    return estimate_cost_worst(task)


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить math-задачу: фильтры -> программа от LLM -> исполнение."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)
    result.trace.append(f"worst≈{estimate_cost_worst(task):.1f}")

    # --- 0. Детерминированные фильтры нерешаемости ---
    reason = detect_unsolvable(task.instruction)
    if reason:
        result.trace.append(f"detector: {reason}")
        result.status = UNANSWERABLE
        return result                        # cost = 0: LLM не звали

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": task.instruction},
    ]
    error: str | None = None
    truncation_repair = False       # раунд после болтовни: кап ниже, без эха
    for round_no in range(MAX_REPAIRS + 1):
        cap = TRUNCATION_REPAIR_CAP if truncation_repair else MAX_TOKENS
        try:
            resp = compile_response(client, model, messages, max_tokens=cap)
            text = resp.text
        except LLMError as e:
            result.trace.append(f"llm: {e}")
            result.cost = client.tracker.spent - spent0
            return result

        # --- отказ модели: данных не хватает ---
        if _is_unsolvable_reply(text):
            result.trace.append(f"round{round_no}: {text.strip()[:80]!r}")
            result.status = UNANSWERABLE
            result.cost = client.tracker.spent - spent0
            return result

        code = _extract_code(text)
        rambling = (code is None
                    and (resp.usage.completion_tokens or 0) >= cap)
        if code is None:
            # точная формулировка: программа может быть, но без print() —
            # ремонт должен сказать, ЧТО именно не так
            error = ("the reply contains no Python program that prints "
                     "the answer")
            result.trace.append(
                f"round{round_no}: no code"
                + (" (cap hit - rambling)" if rambling else ""))
        else:
            proc = _run_code(code)
            answer = _extract_answer(proc.stdout, task.check) if proc.ok \
                else None
            # отказ бывает ВНУТРИ программы (print("UNSOLVABLE")) — тот же
            # маркер: не ремонтим и не принимаем маркер за ответ (exact_str)
            if proc.ok and (
                    answer is None
                    or answer.upper().startswith(UNSOLVABLE)
            ) and _is_unsolvable_reply(proc.stdout):
                result.trace.append(f"round{round_no}: stdout marker")
                result.status = UNANSWERABLE
                result.cost = client.tracker.spent - spent0
                return result
            if answer:
                result.trace.append(f"round{round_no}: stdout={answer!r}")
                return _finish(result, task, answer, client, spent0)
            if not proc.ok:
                error = proc.error or "execution failed"
            else:
                error = "stdout has no answer value"
            result.trace.append(f"round{round_no}: {error}")

        # --- ремонт ---
        if rambling:
            # болтовня вместо кода: эхо — чистый шум, не тащим его целиком;
            # кап ниже (программа короткая), требование жёстче
            truncation_repair = True
            messages = messages + [
                {"role": "assistant",
                 "content": text[:_TRUNCATION_ECHO_CHARS]},
                {"role": "user", "content":
                    f"Your previous reply was cut off at the token limit "
                    f"({cap}) before any program appeared. Reply with the "
                    f"Python program ONLY - no comments, no explanations, "
                    f"no reasoning. Start with the code."},
            ]
        else:
            # обычный ремонт: модель видит свой код и причину неудачи
            messages = messages + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content":
                    f"Your program failed: {error}. "
                    f"Reply with the corrected program only."},
            ]

    result.status = FAILED
    result.cost = client.tracker.spent - spent0
    return result


# ---------- Внутреннее ----------

# механика исполнения — общая с data_analysis/v2 (solutions/pyrun.py)
_extract_code = extract_code
_extract_answer = clean_output_answer


def _run_code(code: str):
    return run_python(code, CODE_TIMEOUT)


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


def _main_demo() -> None:
    """Живой e2e на тренировочных math-задачах."""
    from pathlib import Path

    from solutions.llm import client_from_env, merged_env

    base = (Path(__file__).resolve().parents[3]
            / "tasks" / "task-dir" / "task-dir")
    client = client_from_env()
    model = merged_env().get("MODEL_NAME", QWEN_38_27B_NOREASON)
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
