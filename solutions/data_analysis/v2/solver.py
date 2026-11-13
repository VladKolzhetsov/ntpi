# -*- coding: utf-8 -*-
"""Data-analysis solver v2 — код-планировщик: профиль данных -> python-программа.

Против v1 («в лоб», 345-1000 экв. и обрыв на 300 строках): таблица НЕ
уходит в контекст. LLM платит только за:
- инструкцию задачи,
- заголовок CSV + profile колонок (типы, nan, диапазоны/уникальные) —
  ~15 токенов на колонку, независимо от размера файла («огромный CSV»
  этой версии не страшен);
- написание короткой программы (~100 output).

Программа исполняется с cwd=workspace (открывает свой CSV сама, по
относительному имени), с таймаутом. Ошибка исполнения -> один ремонтный
раунд, как в math/v2.

nan-политика в промпте: пустые ячейки и nan — пропущенные значения;
агрегаты их пропускают (sum/count по непустым), заполнение нулями
запрещено (меняет ответ). Выбор агрегата не угадываем жёстко — но
подсказываем главное соответствие «how many units» -> SUM(units),
«how many orders/rows» -> COUNT.

Детекция нерешаемости:
- precheck ДО LLM (нет файла; колонку объявили и о ней спросили, а её
  нет в файле) — 0 токенов;
- маркер UNSOLVABLE в том же вызове, что и программа (данных не хватает,
  вопрос вне схемы) — как в math/v2.
"""

from __future__ import annotations

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, write_answer,
)
from solutions.data_analysis.precheck import precheck
from solutions.data_analysis.v2.complexity import cost_worst as estimate_cost_worst
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON, QWEN_38_27B
from solutions.planning import compile_text
from solutions.pyrun import clean_output_answer, extract_code, run_python
from solutions.task_parser import Task

MAX_TOKENS = 600        # программа с фильтрами/агрегацией длиннее math
MAX_REPAIRS = 1         # раундов с stderr в контексте
CODE_TIMEOUT = 20       # секунды: миллион строк csv.reader читает 1-2с

# маркер отказа — тот же контракт, что в math/v2
UNSOLVABLE = "UNSOLVABLE"

SYSTEM_PROMPT = (
    "You are a Python data analyst. The user message contains a task, "
    "the CSV file name, its header and a column profile (types, null "
    "counts, ranges). Write a Python program that reads the CSV with the "
    "csv module and prints the answer with print(). Python 3, standard "
    "library only, no input(), no network, no third-party packages. "
    "Reply with the program only - no explanations, no markdown fences, "
    "no bash. The program runs directly in the CSV's directory, so open "
    "the file by its name. Keep it short and deterministic. "
    "Empty cells and nan are missing values: aggregate functions must "
    "skip them (sum/count over non-null values); never fill them with "
    "zeros - it changes the answer. "
    "'How many units' means SUM over the units column; 'how many "
    "orders/rows' means COUNT of matching rows. "
    "If the task cannot be answered from the described data, reply with "
    "exactly: " + UNSOLVABLE
)


def build_user_prompt(instruction: str, pc) -> str:
    """User-промпт солвера: инструкция + имя файла + заголовок + профиль.

    Вынесено в функцию ради cost_worst: complexity.py восстанавливает
    промпт ТОЧНО (не оценивает), а побайтовое совпадение закреплено
    тестом — дрейф формула/промпт ловится сразу.

    Разделитель раскрываем всегда, кроме запятой (ловушка 0036: без
    этой строки модель парсила ';' как ',' — и либо падала на KeyError,
    либо «чинила» его в уверенно неверный ответ '0').
    """
    if pc.delimiter != ",":
        delim = f"CSV file: {pc.csv_path.name} (delimiter {pc.delimiter!r})\n"
    else:
        delim = f"CSV file: {pc.csv_path.name}\n"
    return (f"{instruction}\n\n"
            f"{delim}"
            f"Header: {','.join(pc.header)}\n"
            f"{pc.rows} data rows. Columns:\n{pc.profile_text()}")


def solve(task: Task, client: LLMClient,
           model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить data-analysis-задачу: precheck -> программа -> исполнение."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)
    result.trace.append(f"worst≈{estimate_cost_worst(task):.1f}")

    # --- 0. Детерминированный precheck: 0 токенов ---
    pc = precheck(task.instruction, [task.workspace_dir, task.dir])
    result.trace.append(f"precheck: file={pc.csv_path.name if pc.csv_path else None} "
                        f"rows={pc.rows} columns={pc.header}")
    if not pc.ok:
        result.trace.append(f"precheck: {pc.reason}")
        result.status = UNANSWERABLE
        return result                        # cost = 0: LLM не звали

    # --- 1. Компактный профиль данных вместо самой таблицы ---
    user = build_user_prompt(task.instruction, pc)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]
    error: str | None = None
    for round_no in range(MAX_REPAIRS + 1):
        try:
            text = compile_text(client, model, messages,
                                max_tokens=MAX_TOKENS)
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

        code = extract_code(text)
        if code is None:
            # точная формулировка: программа может быть, но без print() —
            # ремонт должен сказать, ЧТО именно не так, иначе модель
            # повторяет тот же ответ (наблюдалось на live-прогоне)
            error = ("the reply contains no Python program that prints "
                     "the answer")
            result.trace.append(f"round{round_no}: no code")
        else:
            proc = run_python(code, CODE_TIMEOUT, cwd=str(pc.csv_path.parent))
            answer = clean_output_answer(proc.stdout, task.check) if proc.ok \
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

        # ремонт: модель видит свой код и причину неудачи
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

def _is_unsolvable_reply(text: str) -> bool:
    """Модель отказалась: первая строка ответа — маркер UNSOLVABLE.

    Терпим короткое пояснение после маркера («UNSOLVABLE: no such
    column»); ответ с print( — это код, а не отказ.
    """
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.splitlines()[0].strip().upper()
    return first.startswith(UNSOLVABLE) and len(first) <= len(UNSOLVABLE) + 40


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


def cost_worst(task: Task) -> float:
    """Контракт раннера: верхняя граница стоимости задачи (0 токенов).

    Runner (RunConfig.worst_case) ищет cost_worst(task) в модуле солвера
    и использует границу для плотности reward/cost_worst и envelope.
    Задачи, отсекаемые precheck'ом, стоят 0 — это часть границы.
    """
    return estimate_cost_worst(task)


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
