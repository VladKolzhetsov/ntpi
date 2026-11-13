# -*- coding: utf-8 -*-
"""Coding solver v2 — прегон тестов ДО вызова + ремонтный цикл с фидбэком.

Против v1 (один вызов, без фидбэка): suite исполняется ДО обращения к
LLM (бесплатно), и в промпт уходит компактный отчёт прогона — какие
тесты падают и с какими assertion. Модель не тратит дорогой output на
поиск бага «глазами» по ассертам и точнее попадает первым выстрелом
(10 input = 1 output: штраф за отчёт в контексте минимален).

Fast-path: тесты уже зелёные ДО вызова (сломанная задача без багов) —
DONE с нулём LLM-токенов.

Контракт ответа прежний (v1): полный исправленный модуль, ast.parse-гейт
ДО записи (мусор не портит workspace), тесты не трогаем, запись только
target-файла, вердикт — exit 0 полного suite.

Ремонтный цикл (кап 2 раунда, итого ≤ 3 вызовов LLM):
- после каждого фикса — ВЕСЬ suite: фикс, сломавший проходивший тест,
  не принимается никогда;
- в ремонтный промпт идут текущие фейлы, хвост stderr и регрессионная
  подпись «your change BROKE these tests that were passing before» —
  так модель узнаёт о зависимости функций из фидбэка (ловушка
  взаимозависимых функций: f чинится, g(f) ломается);
- детектор осцилляции: множество упавших тестов совпало с любым из
  виденных раньше (A→B→A, «прогресса нет») — цикл обрывается СРАЗУ,
  не сжигая оставшиеся раунды. Это защита от ping-pong между тестами;
  статус failed (повтор множества — наша неконвергенция, а не приговор
  задаче, поэтому НЕ unanswerable).

Кап ответа ДИНАМИЧЕСКИЙ: reply_cap(target) = max(MAX_TOKENS,
1.3 * токены_модуля + 300). Статический кап 800 гарантированно рвал
большие модули (ловушки 0035/0036/0037: ~2400-2900 токенов не влезали,
три раунда в пустоту). При усечении (completion >= cap, модуль невалиден)
ремонт сообщает причину и поднимает кап ×1.5.

Маркер UNSOLVABLE: противоречивые тесты (mystery(2) -> 5 И 6, ловушка
0046) — модель отвечает одной строкой UNSOLVABLE вместо модуля, солвер
принимает отказ в любом раунде (тот же контракт, что в math/v2).

Сознательно НЕ так: «упавшие функции без остального модуля» — экономия
input мизерная (входные файлы ~35 экв. из ~300), а слепота к
взаимодействиям функций — ровно ловушка выше. Частичные diff-патчи —
v3, если в eval появятся большие модули.
"""

from __future__ import annotations

from pathlib import Path

from solutions.base import (
    ANSWERED, FAILED, UNANSWERABLE, SolveResult, write_answer,
)
from solutions.coding.tools import (
    ANSWER_DONE, extract_module, files_text, inspect_task, last_line,
    parse_failures, ran_count, run_tests, tail_text, test_report,
)
from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON, QWEN_38_27B
from solutions.planning import compile_response
from solutions.coding.v2.complexity import cost_worst as estimate_cost_worst
from solutions.search.v2.tokens import INPUT_BASE, INPUT_PER_CHAR
from solutions.task_parser import Task

MAX_TOKENS = 800        # полный исправленный модуль в ответе (нижняя грань)
MAX_REPAIRS = 2         # ремонтных раундов (итого ≤ 3 вызовов LLM)
TEST_TIMEOUT = 20       # секунды на прогон suite
REPLY_CHARS = 6_000     # кап своей предыдущей версии модуля в контексте

# Динамический кап ответа = f(размер target-файла). Ловушки 0035/0036/0037:
# модуль ~2400-2900 токенов НЕВОЗМОЖНО вернуть под статическим капом 800 —
# ответ обрезается, ast-гейт валит, три раунда сгорают за 0 баллов.
# Модель дублирует модуль с докстрингами ~1.3x, потому коэффициент + запас.
CAP_FACTOR = 1.3
CAP_MARGIN = 300

# маркер отказа — тот же контракт, что в math/v2 и DA/v2
UNSOLVABLE = "UNSOLVABLE"

SYSTEM_PROMPT = (
    "You are a Python debugging expert. The user message contains a task, "
    "the Python files from the task workspace (a module with bugs and its "
    "test file) and the output of a test run showing which tests fail and "
    "why. The tests are correct and must not be changed. Fix the module so "
    "that ALL tests pass. A change in one function may affect other "
    "functions and their tests - recheck the whole module before replying. "
    "Reply with the full corrected content of the module file only - no "
    "explanations, no markdown fences, no bash. If the tests are "
    "contradictory (no Python module can make them all pass), reply with "
    "the single line UNSOLVABLE instead of a module."
)


def reply_cap(target: Path) -> int:
    """Кап ответа = max(MAX_TOKENS, CAP_FACTOR * размер_модуля + запас).

    Детерминирован (размер файла известен до первого вызова) — потому
    остаётся честным членом cost_worst. Оценка размера — та же OLS
    символы->токены, что и в search/v2/tokens.
    """
    try:
        chars = len(target.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        chars = 0
    module_tokens = INPUT_BASE + INPUT_PER_CHAR * chars
    return max(MAX_TOKENS, int(module_tokens * CAP_FACTOR) + CAP_MARGIN)


def _is_unsolvable_reply(text: str) -> bool:
    """Модель отказалась: первая строка ответа — маркер UNSOLVABLE.

    Терпим короткое пояснение после маркера; длинный текст (который может
    содержать код) отказом не считается — тот же контракт, что в math/v2.
    """
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped.splitlines()[0].strip().upper()
    return first.startswith(UNSOLVABLE) and len(first) <= len(UNSOLVABLE) + 40


def build_user_prompt(instruction: str, py_files: list, report: str) -> str:
    """User-промпт солвера: инструкция + все файлы + отчёт прогона.

    Вынесено в функцию ради cost_worst: complexity.py восстанавливает
    промпт с той же структурой, побайтовое совпадение закреплено тестом.
    """
    return (f"{instruction}\n\nWorkspace files:\n"
            f"{files_text(py_files)}\n\n{report}")


def cost_worst(task: Task) -> float:
    """Контракт раннера: верхняя граница стоимости задачи (0 токенов).

    Runner (RunConfig.worst_case) ищет cost_worst(task) в модуле солвера
    и использует границу для плотности reward/cost_worst и envelope.
    Задачи, отсекаемые precheck'ом, стоят почти ноль — это часть границы.
    """
    return estimate_cost_worst(task)


def solve(task: Task, client: LLMClient,
          model: str = QWEN_38_27B_NOREASON) -> SolveResult:
    """Решить coding-задачу: прегон тестов → фикс → цикл с фидбэком."""
    spent0 = client.tracker.spent            # дельта = расход на этой задаче
    result = SolveResult(task_id=task.task_id)

    # --- 0. Precheck + ПРЕГОН тестов: 0 токенов ---
    py_files, target, cmd, error = inspect_task(task.instruction,
                                                task.workspace_dir)
    if error is not None:
        result.trace.append(f"precheck: {error}")
        result.status = UNANSWERABLE
        return result
    result.trace.append(f"precheck: target={target.name} "
                        f"tests=\"{' '.join(cmd)}\"")

    proc, err = run_tests(cmd, target.parent, TEST_TIMEOUT)
    if err:
        result.trace.append(f"tests: {err}")
        return _finish_failed(result, client, spent0)
    if proc.returncode == 0:
        # «until all tests pass» выполнено до первого токена
        result.trace.append(f"precheck: all tests already pass "
                            f"({last_line(proc.stderr or proc.stdout)})")
        return _finish_done(result, task, client, spent0)

    failures = parse_failures(proc.stderr)
    result.trace.append(f"precheck: {len(failures)} test(s) fail: "
                        f"{sorted(failures)}")
    report = (test_report(failures, ran_count(proc.stderr)) if failures
              else f"Test run output:\n{tail_text(proc.stderr)}")

    # --- 1. Цикл: вызов → ast-гейт → запись → полный suite ---
    fail_sets: list[frozenset[str]] = [frozenset(failures)]
    cap = reply_cap(target)
    cap_raised = False                   # одноразовое повышение при усечении
    result.trace.append(f"cap={cap}")
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(
            task.instruction, py_files, report)},
    ]

    for round_no in range(MAX_REPAIRS + 1):
        try:
            resp = compile_response(client, model, messages,
                                    max_tokens=cap)
            text = resp.text
        except LLMError as e:
            result.trace.append(f"llm: {e}")
            return _finish_failed(result, client, spent0)

        # --- отказ модели: тесты противоречивы ---
        if _is_unsolvable_reply(text):
            result.trace.append(f"round{round_no}: {text.strip()[:80]!r}")
            result.status = UNANSWERABLE
            result.cost = client.tracker.spent - spent0
            return result

        code = extract_module(text)
        if code is None:
            cut_off = (resp.usage.completion_tokens or 0) >= cap
            if cut_off:
                # усечение: сообщаем ПРИЧИНУ и поднимаем кап следующему
                # раунду — иначе модель повторит ту же ошибку (ловушка
                # 0037: три идентичных «not a valid module»). Повышение
                # ОДНОРАЗОВОЕ: повторное усечение при капе 1.5x от размера
                # модуля — болтовня, её лечит «compactly», а не новый кап.
                error = (f"your reply was CUT OFF at {cap} tokens before "
                         "the full module fit. Reply with the complete "
                         "module, compactly (shorter docstrings).")
                if not cap_raised:
                    old_cap = cap
                    cap = int(cap * 1.5) + 1
                    cap_raised = True
                    result.trace.append(
                        f"round{round_no}: cut off at {old_cap} tokens, "
                        f"cap -> {cap}")
                else:
                    result.trace.append(
                        f"round{round_no}: cut off at {cap} tokens again")
            else:
                error = "the reply is not a valid Python module"
                result.trace.append(f"round{round_no}: {error}")
            feedback = (f"Your reply is not a valid Python module ({error}; "
                        f"it must be the full content of {target.name} and "
                        f"parse as Python 3). Reply with the corrected "
                        f"module only - no explanations, no markdown "
                        f"fences, no bash.")
        else:
            target.write_text(code, encoding="utf-8")

            proc, err = run_tests(cmd, target.parent, TEST_TIMEOUT)
            if err:
                result.trace.append(f"round{round_no}: tests: {err}")
                return _finish_failed(result, client, spent0)
            if proc.returncode == 0:
                result.trace.append(
                    f"round{round_no}: tests OK "
                    f"({last_line(proc.stderr or proc.stdout)})")
                return _finish_done(result, task, client, spent0)

            current_map = parse_failures(proc.stderr)
            current = frozenset(current_map)
            result.trace.append(
                f"round{round_no}: failing: {sorted(current)}")

            # осцилляция: множество упавших уже видели — прогресса нет
            if current in fail_sets:
                seen = fail_sets.index(current)
                whom = ("the initial run" if seen == 0
                        else f"round {seen - 1}")
                result.trace.append(
                    f"round{round_no}: oscillation - same failing set as "
                    f"{whom}, stop early")
                return _finish_failed(result, client, spent0)

            regressions = current - fail_sets[-1]
            fail_sets.append(current)
            feedback = _repair_feedback(current_map, regressions,
                                        proc.stderr, target.name)

        if round_no == MAX_REPAIRS:
            result.trace.append(f"round{round_no}: repair budget exhausted")
            break
        messages = messages + [
            {"role": "assistant", "content": text[:REPLY_CHARS]},
            {"role": "user", "content": feedback},
        ]

    return _finish_failed(result, client, spent0)


# ---------- Внутреннее ----------

def _repair_feedback(current: dict[str, str], regressions: frozenset[str],
                     stderr: str, module_name: str) -> str:
    """Ремонтный промпт: что падает сейчас + что сломано + вывод тестов."""
    lines = ["Your module still fails the tests. Failing tests now:"]
    lines += [f"- {name}: {tail}" for name, tail in sorted(current.items())]
    if regressions:
        lines.append("Your change BROKE these tests that were passing "
                     f"before: {', '.join(sorted(regressions))}.")
    lines.append(f"Full test output:\n{tail_text(stderr)}")
    lines.append(
        f"Reply with the full corrected content of {module_name} only - "
        "no explanations, no markdown fences, no bash.")
    return "\n".join(lines)


def _finish_done(result: SolveResult, task: Task, client: LLMClient,
                 spent0: float) -> SolveResult:
    """DONE в обе позиции answer.txt, закрыть результат."""
    result.status = ANSWERED
    result.answer = ANSWER_DONE
    written, errors = write_answer(task, ANSWER_DONE)
    result.written = written
    result.trace.extend(errors)
    result.cost = client.tracker.spent - spent0
    return result


def _finish_failed(result: SolveResult, client: LLMClient,
                   spent0: float) -> SolveResult:
    """Провал: файла ответа нет; mylib.py остаётся в последнем состоянии."""
    result.status = FAILED
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
