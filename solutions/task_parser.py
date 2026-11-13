# -*- coding: utf-8 -*-
"""Парсер task.json — описания одной задачи соревнования.

Структура каталога задачи:

    <task_id>/
    ├── task.json      # метаданные: id, тип, инструкция, reward, expected, check
    └── workspace/     # рабочие файлы задачи (данные, kb/, тесты...)
        └── answer.txt # ответ агента (создаёт харнесс)

Использование:

    from solutions.task_parser import Task

    task = Task.from_dir("tasks/task-dir/task-dir/search_0001")
    print(task.task_id, task.task_type, task.reward, task.instruction)

    for t in Task.load_dir("/data/tasks"):  # весь набор из 100 задач
        print(t.task_id, t.reward)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

# --- Типы задач (task_type) ---
TASK_MATH = "math"
TASK_CODING = "coding"
TASK_DATA_ANALYSIS = "data_analysis"
TASK_SEARCH = "search"

# --- Способы проверки ответа (check) ---
CHECK_EXACT_INT = "exact_int"  # ответ — целое число
CHECK_EXACT_STR = "exact_str"  # ответ — произвольная строка
CHECK_UNITTEST = "unittest"    # проверяются тесты, answer.txt = "DONE"


class Task:
    """Одна задача соревнования: поля task.json + пути к её файлам.

    Обязательные поля: task_id, task_type, instruction, reward.
    Поля expected и check в примерах присутствуют, но в скрытом наборе
    могут отсутствовать — доступны как Optional. Неизвестные поля не
    теряются: доступны через get() / raw.
    """

    FILE_NAME = "task.json"
    ANSWER_NAME = "answer.txt"

    def __init__(self, data: dict[str, Any], task_dir: str | Path | None = None):
        self._data: dict[str, Any] = dict(data)
        self._dir: Path | None = Path(task_dir).resolve() if task_dir else None
        self._validate()

    # ---------- Создание ----------

    @classmethod
    def from_file(cls, path: str | Path) -> "Task":
        """Создать задачу из файла task.json."""
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(data, task_dir=path.parent)

    @classmethod
    def from_dir(cls, task_dir: str | Path) -> "Task":
        """Создать задачу из каталога задачи (внутри ищется task.json)."""
        return cls.from_file(Path(task_dir) / cls.FILE_NAME)

    @classmethod
    def load_dir(cls, tasks_dir: str | Path) -> list["Task"]:
        """Загрузить все задачи каталога (tasks-dir).

        Подкаталоги без task.json (служебные, мусор от архивов) пропускаются.
        Задача с битым task.json не рушит загрузку — печатается предупреждение.
        """
        tasks: list["Task"] = []
        for entry in sorted(Path(tasks_dir).iterdir()):
            if not (entry / cls.FILE_NAME).is_file():
                continue
            try:
                tasks.append(cls.from_dir(entry))
            except (ValueError, json.JSONDecodeError) as e:
                print(f"[task_parser] пропускаю {entry}: {e}", file=sys.stderr)
        return tasks

    # ---------- Поля task.json ----------

    @property
    def task_id(self) -> str:
        """Уникальный id задачи, например 'search_0001'."""
        return self._data["task_id"]

    @property
    def task_type(self) -> str:
        """Тип задачи: math | coding | data_analysis | search."""
        return self._data["task_type"]

    @property
    def instruction(self) -> str:
        """Инструкция для агента: что сделать и куда написать ответ."""
        return self._data["instruction"]

    @property
    def reward(self) -> int | float:
        """Баллы за задачу (расставлены случайно, не связаны со сложностью)."""
        return self._data["reward"]

    @property
    def expected(self) -> str | None:
        """Ожидаемый ответ, если задан. Для exact_int — число строкой."""
        return self._data.get("expected")

    @property
    def check(self) -> str | None:
        """Способ проверки: exact_int | exact_str | unittest."""
        return self._data.get("check")

    # ---------- Пути ----------

    @property
    def dir(self) -> Path | None:
        """Каталог задачи (содержит task.json), если задан."""
        return self._dir

    @property
    def workspace_dir(self) -> Path | None:
        """Рабочий каталог задачи: здесь работают файлы данных и ответ агента."""
        if self._dir is None:
            return None
        return self._dir / "workspace"

    @property
    def answer_candidates(self) -> list[Path]:
        """Возможные расположения answer.txt.

        Регламент требует писать answer.txt «в каталог каждой задачи», а
        инструкция задачи — «в текущем каталоге», где работает агент
        (workspace). Пока поведение проверяющего неизвестно точно, харнесс
        должен писать ответ во все варианты из этого списка.
        """
        if self._dir is None:
            return []
        return [
            self._dir / self.ANSWER_NAME,
            self._dir / "workspace" / self.ANSWER_NAME,
        ]

    def read_answer(self) -> str | None:
        """Прочитать answer.txt (первый найденный из кандидатов), без пробелов."""
        for path in self.answer_candidates:
            if path.is_file():
                return path.read_text(encoding="utf-8").strip()
        return None

    # ---------- Прочее ----------

    @property
    def raw(self) -> dict[str, Any]:
        """Копия исходных полей task.json, включая неизвестные."""
        return dict(self._data)

    def get(self, field: str, default: Any = None) -> Any:
        """Значение произвольного поля task.json (в т.ч. неизвестного)."""
        return self._data.get(field, default)

    def __getitem__(self, field: str) -> Any:
        return self._data[field]

    def __repr__(self) -> str:
        return (f"Task(task_id={self.task_id!r}, task_type={self.task_type!r}, "
                f"reward={self.reward}, check={self.check!r})")

    # ---------- Валидация ----------

    def _validate(self) -> None:
        for field in ("task_id", "task_type", "instruction"):
            value = self._data.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"task.json: поле {field!r} обязательно (непустая строка)")
        reward = self._data.get("reward")
        if isinstance(reward, bool) or not isinstance(reward, (int, float)) or reward < 0:
            raise ValueError(
                f"task {self._data.get('task_id')!r}: reward должен быть числом >= 0")


def main() -> None:
    """Загрузить тренировочный набор и напечатать сводку по задачам."""
    base = Path(__file__).resolve().parent.parent
    tasks_dir = base / "tasks" / "task-dir" / "task-dir"

    tasks = Task.load_dir(tasks_dir)
    print(f"Загружено задач: {len(tasks)} из {tasks_dir}\n")
    for t in tasks:
        answer = t.read_answer()
        print(f"  {t.task_id:<20} {t.task_type:<14} reward={t.reward:<3} "
              f"check={str(t.check):<10} expected={str(t.expected):<10} "
              f"answer={answer!r}")
    print(f"\nСумма баллов: {sum(t.reward for t in tasks)}")


if __name__ == "__main__":
    main()
