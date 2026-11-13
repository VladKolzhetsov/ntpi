# -*- coding: utf-8 -*-
"""Общий каркас солверов: контракты результата и запись ответа.

Единый для всех типов задач и версий солверов контракт SolveResult +
запись answer.txt в ОБЕ позиции (регламент и инструкция задачи расходятся
в том, где его искать). При решении «сдаться» (unanswerable, полный
провал лестницы) файл НЕ создаём: мусорный ответ гарантированно провалит
проверку, а «нет файла» не портит статистику ложными попытками.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from solutions.task_parser import CHECK_EXACT_INT, Task

# --- Статусы решения ---
ANSWERED = "answered"    # ответ найден и записан
UNANSWERABLE = "unanswerable"  # детектор/планировщик: задача нерешаема
FAILED = "failed"        # полный провал, файла нет


@dataclass
class SolveResult:
    """Итог одной задачи: для лога прогона и тестов."""

    task_id: str
    status: str = FAILED             # answered | unanswerable | failed
    answer: str | None = None        # записанный ответ
    written: list[Path] = field(default_factory=list)  # созданные answer.txt
    cost: float = 0.0                # расход output-экв. на этой задаче
    trace: list[str] = field(default_factory=list)     # ступени и решения

    @property
    def ok(self) -> bool:
        return self.status == ANSWERED


_ANSWER_RE = re.compile(r"^\s*Answer:\s*(.+?)\s*$", re.IGNORECASE)
_INT_RE = re.compile(r"-?\d+")


def extract_answer(text: str, check: str | None) -> str | None:
    """Достать ответ из ответа LLM «в лоб»-солверов (v1).

    Берём ПОСЛЕДНЮЮ строку «Answer: X» (модель может пересчитывать);
    маркера нет — последнюю непустую строку. Далее чистка по типу
    проверки: exact_int -> первое целое в значении (без пробелов/запятых),
    иначе — обрезаем фенсы/кавычки/замыкающую точку.

    Не путать с math/v2 `_extract_answer`: тот чистит stdout программы.
    """
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    value: str | None = None
    for ln in lines:
        m = _ANSWER_RE.match(ln)
        if m:
            value = m.group(1).strip()
    if value is None:
        value = lines[-1].strip()

    if check == CHECK_EXACT_INT:
        compact = value.replace(" ", "").replace(",", "")
        m = _INT_RE.search(compact)
        if not m:
            return None
        return str(int(m.group()))

    # чистка в несколько проходов: кавычка может стоять и после точки
    for _ in range(3):
        value = value.strip("`\"' \t")
        if value.endswith("."):
            value = value[:-1].rstrip()
    return value or None


def write_answer(task: Task, answer: str) -> tuple[list[Path], list[str]]:
    """Записать ответ во ВСЕ позиции answer.txt задачи.

    -> (written, errors): созданные файлы и сообщения об ошибках записи.
    Хвостовой \\n — семантика `echo` из инструкции задачи.
    """
    written: list[Path] = []
    errors: list[str] = []
    for path in task.answer_candidates:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(answer + "\n", encoding="utf-8")
            written.append(path)
        except OSError as e:
            errors.append(f"write: {path.name}: {e}")
    return written, errors


def check_writable(directory: Path | str) -> str | None:
    """Проверить, можно ли писать в каталог; None — можно, иначе причина.

    `os.access` здесь недостаточен: root в контейнере без CAP_DAC_OVERRIDE
    получает от access(2) корректный ответ, но проверка «пробным файлом»
    надёжнее и покрывает read-only ФС, квоты и SELinux. 0 токенов.
    Каталог создаётся при необходимости — так же, как это делает
    write_answer перед записью ответа.

    Нужно до прогона: если отвечать некуда, лучше упасть сразу, чем час
    решать задачи и записать ноль ответов (см. «сдачу ответа» в README).
    """
    directory = Path(directory)
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return f"{directory}: каталог недоступен ({e})"
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".write-probe-",
                                         delete=True):
            pass
    except OSError as e:
        return f"{directory}: нет права на запись ({e})"
    return None
