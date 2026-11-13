# -*- coding: utf-8 -*-
"""Общие инструменты coding-солверов (v1, v2): разбор задачи и тесты.

Здесь только детерминированная механика, 0 токенов:
- цель фикса и команда тестов из инструкции (с fallback'ом на workspace);
- чтение .py-файлов workspace в промпт;
- извлечение модуля из ответа LLM с ast-гейтой (мусор не пишем);
- прогон тестов сабпроцессом (cwd=workspace) и разбор stderr юниттеста
  (какие тесты упали и с каким assertion).

Никакой политики итераций/бюджета — это решает солвер своей версии.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from pathlib import Path

# ответ unittest-задач по регламенту (инструкция: write ONLY the word DONE)
ANSWER_DONE = "DONE"

MAX_FILE_CHARS = 20_000   # кап контекста на один файл workspace

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_]*\s*(.*?)```", re.DOTALL)
_TARGET_RE = re.compile(
    r"\bfix(?:\s+the)?\s+(?:bugs?|code|module|implementation)\s+in\s+"
    r"([\w\-]+\.py)", re.IGNORECASE)
_CMD_RE = re.compile(r"`([^`]*)`")
_FAIL_HEAD_RE = re.compile(r"^(?:FAIL|ERROR):\s+(\S+)")
_RAN_RE = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)
# строка итогов юниттеста — конец блока падения
_SUMMARY_RE = re.compile(r"^(?:Ran \d+ tests?|OK\b|FAILED\b)")


def target_file(instruction: str, module_files: list[Path],
                test_files: list[Path]) -> Path | None:
    """Файл, который велят чинить; fallback — единственный не-test .py.

    None = задача сломана: названный файл отсутствует, модулей нет вовсе
    или инструкция велит чинить тесты (противоречит «do not edit»).
    """
    m = _TARGET_RE.search(instruction)
    if m:
        name = m.group(1)
        if any(p.name == name for p in test_files):
            return None
        for p in module_files:
            if p.name == name:
                return p
        return None
    return module_files[0] if module_files else None


def test_command(instruction: str, test_files: list[Path],
                 ws: Path | None) -> list[str] | None:
    """Команда тестов из бэктиков инструкции («Run `python3 test_x.py`»),
    с whitelist'ом и проверкой существования файла; fallback —
    единственный test_*.py в workspace."""
    for m in _CMD_RE.finditer(instruction):
        parts = m.group(1).strip().split()
        if (len(parts) == 2 and parts[0] in ("python3", "python")
                and parts[1].endswith(".py")
                and "/" not in parts[1] and ".." not in parts[1]
                and ws is not None and (ws / parts[1]).is_file()):
            return parts
    if test_files:
        return ["python3", test_files[0].name]
    return None


def inspect_task(instruction: str, workspace_dir: Path | None
                 ) -> tuple[list[Path], Path | None, list[str] | None, str | None]:
    """Детерминированный precheck coding-задачи (0 токенов).

    -> (py_files, target, cmd, error):
    - py_files — все .py workspace (для контекста);
    - target — файл, который чиним;
    - cmd — команда тестов;
    - error — причина «задача сломана» (None = всё в порядке):
      нет .py-файлов, названный файл отсутствует, велит чинить тесты,
      нет тестовой команды. Все солверы обязаны гейтиться этим до LLM.

    Трейс-строки формируют солверы (своим префиксом «precheck: ...»).
    """
    py_files = (sorted(p for p in workspace_dir.glob("*.py"))
                if workspace_dir else [])
    test_files = [p for p in py_files if p.name.startswith("test_")]
    module_files = [p for p in py_files if p not in test_files]
    if not py_files:
        return [], None, None, "no python files in the workspace"
    target = target_file(instruction, module_files, test_files)
    if target is None:
        return py_files, None, None, "no module file to fix"
    cmd = test_command(instruction, test_files, workspace_dir)
    if cmd is None:
        return py_files, target, None, "no test file/command found"
    return py_files, target, cmd, None


def files_text(py_files: list[Path], max_chars: int = MAX_FILE_CHARS) -> str:
    """Все .py-файлы в один блок с разделителями; кап на файл."""
    parts = []
    for p in py_files:
        content = p.read_text(encoding="utf-8", errors="replace")
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"
        parts.append(f"--- {p.name} ---\n{content}")
    return "\n\n".join(parts)


def extract_module(text: str) -> str | None:
    """Модуль из ответа: самый длинный fenced-блок, иначе весь ответ.

    Гарантия: содержимое парсится ast.parse — иначе None (не пишем мусор).
    """
    fences = _FENCE_RE.findall(text)
    code = (max(fences, key=len).strip() if fences else text.strip())
    if not code:
        return None
    try:
        ast.parse(code)
    except SyntaxError:
        return None
    return code


def run_tests(cmd: list[str], ws: Path, timeout: float):
    """Прогон тестов в сабпроцессе. -> (CompletedProcess | None, error).

    Чистим `__pycache__`/`*.pyc` и добавляем `-B` перед прогоном. Причина:
    Python инвалидирует bytecode-кеш по паре (mtime в СЕКУНДАХ, размер
    файла), поэтому модуль той же длины, перезаписанный в ту же секунду,
    исполняется из СТАРОГО кеша — на живом прогоне тестов это выглядело
    как «фикс x / 2 -> x * 2 не подействовал» (одинаковый размер, раунды
    внутри одной секунды). Тесты обязаны видеть текущий исходник.
    """
    for cache_dir in ws.glob("**/__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for stale in ws.glob("**/*.pyc"):
        stale.unlink(missing_ok=True)
    if cmd and cmd[0] in ("python3", "python"):       # -B: не писать кеш
        cmd = [cmd[0], "-B", *cmd[1:]]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, check=False, cwd=str(ws))
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError as e:
        return None, f"cannot run tests: {e}"
    return proc, None


def last_line(stream: str) -> str:
    """Последняя непустая строка потока (для трейса)."""
    lines = [ln.strip() for ln in (stream or "").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def parse_failures(stderr: str) -> dict[str, str]:
    """Упавшие тесты из stderr юниттеста: {имя: assertion-строка}.

    stderr юниттеста — блоки, разделённые строками «======»; блок с
    заголовком FAIL/ERROR — упавший тест. Последняя содержательная строка
    блока (до итогов «Ran .../FAILED ...», минуя разделители «------») —
    суть падения. Итоговый блок заголовка не имеет и отбрасывается.

        FF.
        ======
        FAIL: test_total (test_mylib.T.test_total)
        ------
        Traceback ...
        AssertionError: -6 != 6
        ======
        ------
        Ran 2 tests in 0.001s
        FAILED (failures=2)
    """
    failures: dict[str, str] = {}
    chunks = re.split(r"^={10,}\s*$", stderr or "", flags=re.MULTILINE)
    for chunk in chunks:
        lines = chunk.splitlines()
        head_line = next((ln for ln in lines if ln.strip()), "")
        head = _FAIL_HEAD_RE.match(head_line)
        if not head:
            continue
        tail = ""
        in_block = False
        for ln in lines:
            stripped = ln.strip()
            if not in_block:                     # ждём строку-заголовок
                if stripped and _FAIL_HEAD_RE.match(stripped):
                    in_block = True
                continue
            if _SUMMARY_RE.match(stripped):      # начались итоги прогона
                break
            if stripped and not set(stripped) <= {"-"}:
                tail = stripped
        failures[head.group(1)] = tail
    return failures


def ran_count(stderr: str) -> int | None:
    """Сколько тестов прогнал юниттест («Ran 6 tests in ...»)."""
    m = _RAN_RE.search(stderr or "")
    return int(m.group(1)) if m else None


def test_report(failures: dict[str, str], total: int | None) -> str:
    """Компактный отчёт прогона для промпта."""
    if not failures:
        return "Test run output: all tests pass."
    head = "Test run output:"
    if total is not None:
        head += f" {total - len(failures)} of {total} tests pass," \
                f" {len(failures)} fail:"
    else:
        head += f" {len(failures)} test(s) fail:"
    lines = [head] + [f"- {name}: {tail}" for name, tail in failures.items()]
    return "\n".join(lines)


def tail_text(stream: str, limit: int = 1500) -> str:
    """Хвост потока с гарантией лимита (для ремонтного контекста)."""
    text = (stream or "").strip()
    return text[-limit:] if len(text) > limit else text
