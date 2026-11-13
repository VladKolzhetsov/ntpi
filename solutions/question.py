# -*- coding: utf-8 -*-
"""Обработчик вопроса: чистит instruction задачи до собственно вопроса.

instruction в task.json — не вопрос, а конгломерат:

    [преамбула]. [ВОПРОС?]. [howto: use bash/ls/grep].
    [Work only inside...]. [When you are done, write ONLY ... answer.txt].

Планировщику нужен только вопрос: howto мы реализуем сами, преамбулу kb
заменяет schema() базы. Правило извлечения детерминированное, 0 токенов:

1) срез по последнему "?" — howto-хвост всегда идёт после вопроса;
2) дроп предложений, совпадающих с известными howto-паттернами.
   Паттерны консервативные: контентные преамбулы ("The file sales.csv has
   columns...") НЕ трогаем — для data_analysis это данные;
3) мягкая деградация: нет "?" — берём всё после чистки; если чистка съела
   всё — возвращаем исходник. Не падаем никогда.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Разрез между предложениями: после . ? ! перед пробелом.
_SENT_SPLIT = re.compile(r"(?<=[.?!])\s+")

# Howto-предложения: "как работать" и "куда писать ответ" (в training-наборе
# одинаковы у всех задач). Матчим по началу предложения, case-insensitive.
_BOILERPLATE = [
    re.compile(p, re.IGNORECASE)
    for p in (
        r"^the directory \S+ contains (?:a |an )?(?:small )?knowledge base\b",
        r"^use (?:the )?(?:file tools or )?bash\b.*\b(?:find|compute) it\b",
        r"^compute it with the bash tool\b",
        r"^work only inside the current directory\b",
        r"^when you are done, write only\b.*answer\.txt\b",
    )
]


def split_sentences(text: str) -> list[str]:
    """Разбить текст на предложения (по . ? ! + пробел)."""
    return [s.strip() for s in _SENT_SPLIT.split(text.strip()) if s.strip()]


def strip_howto(text: str) -> str:
    """Убрать howto-предложения («как работать», «куда писать ответ»)."""
    kept = [s for s in split_sentences(text)
            if not any(p.match(s) for p in _BOILERPLATE)]
    return " ".join(kept)


def extract_question(instruction: str) -> str:
    """Собственно вопрос из instruction: срез по последнему "?" + чистка howto."""
    instruction = instruction.strip()
    if not instruction:
        return ""
    # 1) хвост после последнего "?" — howto-boilerplate
    last_q = instruction.rfind("?")
    head = instruction if last_q < 0 else instruction[: last_q + 1]
    # 2) дроп howto-преамбул, попавших до "?"
    question = strip_howto(head)
    # 3) мягкая деградация: вычистили всё -> вернуть исходную голову
    return question or head


def main() -> None:
    """Демо: чистка всех тренировочных инструкций.

    Работает и прямым запуском, и через -m (main сам чинит sys.path):
        .venv/bin/python solutions/question.py
        .venv/bin/python -m solutions.question
    """
    base = Path(__file__).resolve().parents[1]
    if str(base) not in sys.path:
        sys.path.insert(0, str(base))
    from solutions.task_parser import Task

    train = base / "tasks" / "task-dir" / "task-dir"
    for task in Task.load_dir(train):
        print(f"[{task.task_id}] {extract_question(task.instruction)}")


if __name__ == "__main__":
    main()
