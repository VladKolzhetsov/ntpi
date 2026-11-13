# -*- coding: utf-8 -*-
"""Детерминированный precheck data_analysis-задач: 0 токенов до LLM.

Сверяются три источника:

    instruction -> имя файла данных + объявленные колонки (декларация)
    CSV-файл    -> реальный заголовок + profile колонок (типы, nan, диапазоны)
    вопрос      -> упоминания колонок (лексически, без семантики)

Зачем:
1. не пустить к LLM заведомо сломанную задачу (нет файла; колонку
   объявили в условии, о ней спросили, а в файле её нет) — это 0 токенов;
2. дать солверу бесплатный «info» по данным (profile), чтобы промпт не
   гадал про типы, nan и диапазоны.

Порядок авторитетов: **файл (факты) > декларация инструкции (подсказка)**.
Поэтому колонка, которой нет в декларации, но которая есть в файле, —
НЕ повод для отказа: инструкция могла перечислить не всё.

Чего сознательно НЕ делаем: слово вопроса, не совпавшее ни с одной
колонкой, отказом не считаем — это может быть агрегат или вычисляемая
величина (revenue = units * price). Такие случаи решает LLM (маркер
UNSOLVABLE), а не гадание: полная проверка «хватает ли данных» = решение
задачи, т.е. детерминированный солвер без LLM.

Детекторы — реестр DETECTORS; отключение = убрать из списка (или передать
свой список в precheck()).
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from solutions.question import extract_question
from solutions.text import stems_match, stem, words

CSV_NAME_RE = re.compile(r"[\w\-]+\.csv", re.IGNORECASE)

# Декларация схемы в инструкции: "has columns a,b,c (20 rows)",
# "with columns: a, b and c", "columns a;b". Конец — скобка, конец
# предложения или строки (иначе в захват попадёт сам вопрос).
_DECL_RE = re.compile(
    r"\bcolumns?\s*[:\-]?\s*([^()\n]*?)(?:\(|\.\s|\.$|\n|$)", re.IGNORECASE)

# Пустая ячейка / «nan» в любом регистре — пропущенное значение.
NULLS = frozenset({"", "nan", "na", "n/a", "null", "none"})

PROFILE_UNIQUES = 8       # столько уникальных значений показываем в profile
UNIQ_TRACK = 500          # больше -> считаем «много уникальных» (память)
PROFILE_MAX_ROWS = 200_000  # защита от гигантских файлов: дальше не сканируем


# ---------------------------------------------------------------- файл

def find_csv_path(instruction: str,
                  search_dirs: Sequence[Path | None] = ()) -> Path | None:
    """Найти CSV-файл задачи: по имени из инструкции, затем единственный в каталоге.

    Толерантен к неточному имени: инструкция может назвать файл иначе,
    чем он лежит (тогда берём единственный csv в workspace) — отказ был бы
    потерей решаемой задачи.
    """
    dirs = [Path(d) for d in search_dirs if d is not None]
    for name in CSV_NAME_RE.findall(instruction):
        for d in dirs:
            candidate = d / name
            if candidate.is_file():
                return candidate
        low = name.lower()                       # регистр мог отличаться
        for d in dirs:
            if not d.is_dir():
                continue
            for entry in sorted(d.iterdir()):
                if entry.is_file() and entry.name.lower() == low:
                    return entry
    for d in dirs:                               # имя не названо/не найдено
        if not d.is_dir():
            continue
        found = [p for p in sorted(d.glob("*.csv")) if p.is_file()]
        if found:
            return found[0]
    return None


def parse_declared_columns(instruction: str) -> list[str] | None:
    """Колонки, объявленные в инструкции («The file has columns a,b,c»).

    None — декларации нет или она не распарсилась. Это не ошибка задачи:
    декларация вторична, источник фактов — сам файл.
    """
    m = _DECL_RE.search(instruction)
    if not m:
        return None
    chunk = m.group(1)
    names: list[str] = []
    for part in re.split(r",|\band\b|;", chunk):
        name = part.strip().strip("`'\"*").strip()
        if name and re.fullmatch(r"[A-Za-z_][\w\-]*", name):
            names.append(name)
    return names or None


def read_header(path: Path) -> tuple[list[str], str]:
    """Заголовок CSV -> (имена колонок, разделитель).

    Разделитель угадывается `csv.Sniffer` по первым килобайтам, при
    неудаче — запятая. BOM снимается (utf-8-sig).
    """
    if path is None:
        return [], ","
    sample = path.read_text(encoding="utf-8-sig", errors="replace")[:4096]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    header: list[str] = []
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f:
        for row in csv.reader(f, delimiter=delimiter):
            header = [cell.strip() for cell in row if cell.strip()]
            break
    return header, delimiter


# ------------------------------------------------------------- profile

@dataclass
class ColumnStats:
    """Статистика одной колонки: тип, пропуски, диапазон/уникальные."""

    name: str
    kind: str = "unknown"                    # int | float | text | unknown
    nulls: int = 0
    total: int = 0
    min: float | None = None
    max: float | None = None
    _uniq: set[str] | None = field(default_factory=set, repr=False)

    @property
    def uniq_count(self) -> int | None:
        """Число уникальных значений; None — их больше UNIQ_TRACK."""
        return None if self._uniq is None else len(self._uniq)

    def feed(self, cell: str) -> None:
        """Учесть одну ячейку: пропуск, тип, диапазон, уникальные."""
        self.total += 1
        if cell.strip().lower() in NULLS:
            self.nulls += 1
            return
        value, kind = _classify(cell)
        if self.kind == "unknown":
            self.kind = kind
        elif self.kind != kind:
            self.kind = kind if _KIND_ORDER[kind] > _KIND_ORDER[self.kind] else self.kind
        if value is not None and kind in ("int", "float"):
            self.min = value if self.min is None else min(self.min, value)
            self.max = value if self.max is None else max(self.max, value)
        if self._uniq is not None:
            self._uniq.add(cell)
            if len(self._uniq) > UNIQ_TRACK:
                self._uniq = None

    def describe(self) -> str:
        """Одна компактная строка для промпта (или лога)."""
        kind = "empty" if self.kind == "unknown" else self.kind
        parts = [kind, f"nulls {self.nulls}/{self.total}"]
        if self.kind in ("int", "float") and self.min is not None:
            parts.append(f"range {_fmt(self.min)}..{_fmt(self.max)}")
        elif self._uniq is None:
            parts.append(f"{UNIQ_TRACK}+ unique")
        elif self._uniq:
            names = sorted(self._uniq)
            if len(names) <= PROFILE_UNIQUES:
                parts.append(f"{len(names)} unique: " + ", ".join(names))
            else:
                parts.append(f"{len(names)} unique, e.g. " +
                             ", ".join(names[:PROFILE_UNIQUES]))
        return "; ".join(parts)


_KIND_ORDER = {"int": 0, "float": 1, "text": 2}


def _classify(cell: str) -> tuple[float | None, str]:
    try:
        return float(int(cell)), "int"
    except ValueError:
        pass
    try:
        return float(cell), "float"
    except ValueError:
        return None, "text"


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:g}"


def profile_csv(path: Path, header: Sequence[str],
                delimiter: str = ",",
                max_rows: int = PROFILE_MAX_ROWS
                ) -> tuple[dict[str, ColumnStats], int]:
    """Потоковый profile колонок: типы, nan, диапазоны, уникальные.

    Память O(колонок), файл не загружается целиком -> огромный CSV не
    проблема. -> ({имя: ColumnStats}, число учтённых строк данных).
    """
    stats = {name: ColumnStats(name=name) for name in header}
    rows = 0
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f:
        reader = csv.reader(f, delimiter=delimiter)
        try:
            next(reader)                       # заголовок
        except StopIteration:
            return stats, 0
        for row in reader:
            if rows >= max_rows:
                break
            if not any(cell.strip() for cell in row):
                continue                       # пустая строка — не данные
            rows += 1
            for i, name in enumerate(header):
                stats[name].feed(row[i] if i < len(row) else "")
    return stats, rows


def read_csv_text(path: Path | None, max_rows: int = 400,
                  delimiter: str = ",", max_chars: int = 20_000
                  ) -> tuple[str, int]:
    """Текст CSV для «в лоб»-промпта: заголовок + до max_rows строк.

    Два капа (строки и символы) защищают контекст от гигантских файлов
    и от строк-монстров. -> (текст, число выведенных строк данных).
    """
    if path is None:
        return "", 0
    out = io.StringIO()
    writer = csv.writer(out, delimiter=delimiter, lineterminator="\n")
    shown = 0
    with path.open("r", encoding="utf-8-sig", newline="", errors="replace") as f:
        for i, row in enumerate(csv.reader(f, delimiter=delimiter)):
            if i == 0:
                writer.writerow(row)
                continue
            if shown >= max_rows or out.tell() > max_chars:
                break
            writer.writerow(row)
            shown += 1
    return out.getvalue().strip(), shown


# ------------------------------------------------------------ детекторы

@dataclass
class Precheck:
    """Итог precheck: найденные факты о данных + вердикт."""

    csv_path: Path | None = None
    header: list[str] = field(default_factory=list)
    declared: list[str] | None = None
    rows: int = 0
    delimiter: str = ","
    columns: dict[str, ColumnStats] = field(default_factory=dict)
    reason: str | None = None                # не None -> задача нерешаема

    @property
    def ok(self) -> bool:
        return self.reason is None

    def profile_text(self) -> str:
        """Компактный «info» по данным: одна строка на колонку."""
        if not self.columns:
            return ""
        return "\n".join(f"  {name}: {st.describe()}"
                         for name, st in self.columns.items())


def column_in(name: str, header: Sequence[str]) -> bool:
    """Колонка `name` есть в заголовке (без учёта регистра и числа)."""
    target = stem(name.lower())
    return any(stems_match(target, stem(h.lower())) for h in header)


def mentions_column(text: str, column: str) -> bool:
    """Колонка упомянута в тексте (по словам, со стеммингом)."""
    if "_" in column or " " in column:
        flat = column.replace("_", " ").lower()
        low = text.lower()
        return flat in low or column.lower() in low
    target = stem(column.lower())
    return any(stems_match(target, stem(w)) for w in words(text))


def csv_file_missing(pc: Precheck, instruction: str) -> str | None:
    """Детектор: файла данных нет. Самая дешёвая проверка — и самая грубая."""
    if pc.csv_path is not None:
        return None
    named = CSV_NAME_RE.findall(instruction)
    if named:
        return f"data file {named[0]!r} is not present in the task workspace"
    return "no CSV data file found in the task workspace"


def declared_column_absent(pc: Precheck, instruction: str) -> str | None:
    """Детектор: колонку объявили и о ней спросили, а в файле её нет.

    Это ловушка «инструкция обещает quantity, в файле units, вопрос про
    quantity». Обратный случай (в файле есть, в декларации нет) — норма:
    декларация может быть неполной.

    Декларация из текста вопроса вырезается: иначе колонка «упоминала бы
    себя» просто фактом перечисления в инструкции.
    """
    if not pc.declared or not pc.header:
        return None
    question = extract_question(_DECL_RE.sub(" ", instruction))
    for column in pc.declared:
        if column_in(column, pc.header):
            continue
        if mentions_column(question, column):
            return (f"column {column!r} is declared and asked about, but "
                    f"missing from {pc.csv_path.name} "
                    f"(actual columns: {', '.join(pc.header)})")
    return None


DETECTORS = [
    csv_file_missing,
    declared_column_absent,
]


def precheck(instruction: str,
             search_dirs: Sequence[Path | None] = (),
             detectors: Sequence | None = None) -> Precheck:
    """Разобрать задачу до LLM: файл, заголовок, декларация, profile, вердикт.

    search_dirs — где искать CSV (обычно [task.workspace_dir, task.dir]).
    detectors=None -> реестр DETECTORS; [] -> все детекторы отключены.
    """
    path = find_csv_path(instruction, search_dirs)
    declared = parse_declared_columns(instruction)
    header, delimiter = read_header(path) if path else ([], ",")
    columns, rows = (profile_csv(path, header, delimiter)
                     if path and header else ({}, 0))
    pc = Precheck(csv_path=path, header=header, declared=declared,
                  rows=rows, delimiter=delimiter, columns=columns)
    for detect in (DETECTORS if detectors is None else detectors):
        reason = detect(pc, instruction)
        if reason:
            pc.reason = reason
            break
    return pc


def main() -> None:
    """Демо: precheck всех тренировочных data_analysis-задач (без LLM)."""
    from solutions.task_parser import Task

    base = Path(__file__).resolve().parents[2] / "tasks" / "task-dir" / "task-dir"
    for task in Task.load_dir(base):
        if task.task_type != "data_analysis":
            continue
        pc = precheck(task.instruction, [task.workspace_dir, task.dir])
        print(f"[{task.task_id}] ok={pc.ok} file={pc.csv_path.name if pc.csv_path else None} "
              f"rows={pc.rows} reason={pc.reason}")
        print(f"  declared: {pc.declared}")
        print(f"  header:   {pc.header}")
        print(pc.profile_text())


if __name__ == "__main__":
    main()
