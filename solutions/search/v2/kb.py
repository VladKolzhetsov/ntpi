# -*- coding: utf-8 -*-
"""Мини-база знаний: карточки-ноды из kb/ и индексы для linking'а.

Формат карточки (один md-файл = одна нода):

    # Irina Sato          # заголовок -> каноническое имя (опционально)
    Born: 1983            # "Key: value" -> атрибуты
    Employer: Fjord Data
    Mentor: Olga Muller

Нода — generic-сущность: тип (person/company/...) — это label из префикса
имени файла, а не отдельный класс. Граф возникает сам: значение атрибута
("Fjord Data") становится ребром, когда матчится с именем другой ноды
(resolve()).

Парсер никогда не падает: скрытые файлы (._*, .git/) пропускаются, файлы без
ни одного "Key: value" не становятся нодами. Доля распарсенных файлов —
сигнал is_healthy(): если формат в скрытом наборе другой, надо включать
fallback, а не отвечать по пустой базе.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path


def _norm(s: str) -> str:
    """Нормализация для сопоставления имён: NFKC + casefold, пробелы
    схлопнуть, possessive ('s) срезать."""
    s = unicodedata.normalize("NFKC", str(s)).replace("\u2019", "'").casefold().strip()
    s = " ".join(s.split())
    if s.endswith("'s"):
        s = s[:-2].rstrip()
    return s


class Node:
    """Карточка одной сущности: один md-файл из базы знаний."""

    def __init__(self, name: str,
                 type: str,
                 attrs: dict[str, str],
                 raw: str,
                 source: Path,
                 garbage_lines: int = 0):
        self.name = name                    # каноническое имя (строка "# ...")
        self.type = type                    # label из префикса файла: person / company / ...
        self.attrs = attrs                  # {"Born": "1983", ...}
        self.raw = raw                      # исходный текст (для fallback и отладки)
        self.source = source
        self.garbage_lines = garbage_lines  # строки без структуры и без заголовка

    @classmethod
    def from_file(cls, path: Path, type_label: str | None = None) -> "Node | None":
        """Распарсить файл. None, если нет ни одного поля "Key: value"."""
        raw = path.read_text(encoding="utf-8", errors="replace")
        name = ""
        attrs: dict[str, str] = {}
        garbage = 0
        for line in raw.splitlines():
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):           # заголовок -> каноническое имя
                if not name:
                    name = s.lstrip("#").strip()
                continue
            s = s.lstrip("-*").strip()      # допускаем markdown-буллеты
            key, sep, value = s.partition(":")
            if not sep or not (key := key.strip()) or not (value := value.strip()):
                garbage += 1
                continue
            attrs[key] = value              # при дубликатах побеждает последний
        if not attrs:
            return None
        if type_label is None:
            stem = path.stem
            type_label = stem.split("_", 1)[0] if "_" in stem else stem
            type_label = type_label.casefold()
        return cls(name or path.stem, type_label, attrs, raw, path, garbage)

    def get(self, field: str, default: str | None = None) -> str | None:
        """Значение поля: точное совпадение, затем casefold-совпадение."""
        if (field_value := self.attrs.get(field)) is not None:
            return field_value
        want = field.casefold()
        for k, v in self.attrs.items():
            if k.casefold() == want:
                return v
        return default

    def aliases(self) -> list[str]:
        """Варианты имени: заголовок файла и стем без префикса типа
        ("Irina Sato" и "irina sato" для person_irina_sato.md)."""
        stem = self.source.stem
        parts = stem.split("_", 1)
        rest = parts[1] if len(parts) == 2 else stem
        return [self.name, rest.replace("_", " ")]

    def __repr__(self) -> str:
        return f"Node(name={self.name!r}, type={self.type!r}, attrs={self.attrs!r})"


class KnowledgeBase:
    """Все ноды базы + индексы для поиска имён и резолвинга ссылок."""

    def __init__(self, nodes: list[Node], files_seen: int = 0):
        self.nodes = nodes
        self.files_seen = files_seen        # сколько не-скрытых файлов пытались парсить
        # нормализованные алиасы для find / find_in_text
        self._node_aliases: list[tuple[Node, list[str]]] = []
        self._by_alias: dict[str, list[Node]] = {}
        for node in nodes:
            normed: list[str] = []
            for alias in node.aliases():
                if (n := _norm(alias)) and n not in normed:
                    normed.append(n)
            self._node_aliases.append((node, normed))
            for n in normed:
                self._by_alias.setdefault(n, []).append(node)

    @classmethod
    def from_dir(cls, kb_dir: str | Path) -> "KnowledgeBase":
        """Рекурсиво обойти каталог и распарсить все не-скрытые файлы."""
        root = Path(kb_dir)
        nodes: list[Node] = []
        files_seen = 0
        if not root.is_dir():
            return cls([], 0)
        for path in sorted(root.rglob("*")):
            rel = path.relative_to(root)
            if any(part.startswith(".") for part in rel.parts):
                continue                    # ._*, .git/ и прочий мусор
            if not path.is_file():
                continue
            files_seen += 1
            try:
                node = Node.from_file(path)
            except OSError:
                continue                    # нечитаемый файл — просто пропускаем
            if node is not None:
                nodes.append(node)
        return cls(nodes, files_seen)

    # ---------- Поиск ----------

    def find(self, name: str) -> list[Node]:
        """Все ноды с таким именем (заголовок или имя файла). Возможны коллизии."""
        return list(self._by_alias.get(_norm(name), []))

    def find_in_text(self, text: str) -> list[Node]:
        """Ноды, чьё имя упоминается в тексте (например, в вопросе задачи).

        Сортировка: сначала ноды с самым длинным совпадением (более специфичные).
        """
        norm = _norm(text)
        hits: list[tuple[int, Node]] = []
        for node, aliases in self._node_aliases:
            hit = 0
            for alias in aliases:
                if alias and re.search(rf"\b{re.escape(alias)}\b", norm):
                    hit = max(hit, len(alias))
            if hit:
                hits.append((-hit, node))
        hits.sort(key=lambda x: x[0])       # sort стабилен: порядок нод сохраняется
        return [node for _, node in hits]

    def match(self, field: str, value: str,
              type: str | None = None) -> list[Node]:
        """Ноды, у которых поле равно значению (регистр не важен).

        Обратный поиск: «какие компании headquartered in Osaka».
        Точное равенство после нормализации; поиск подстрок — find_in_text.
        """
        want = _norm(value)
        want_type = _norm(type) if type else None
        result: list[Node] = []
        for node in self.nodes:
            if want_type is not None and node.type != want_type:
                continue
            field_value = node.get(field)
            if field_value is not None and _norm(field_value) == want:
                result.append(node)
        return result

    def resolve(self, value: str, prefer_type: str | None = None) -> Node | None:
        """Значение-ссылка ("Fjord Data") -> нода. Ребро графа.

        При коллизии имён можно указать предпочтительный тип (prefer_type);
        остаётся неоднозначной -> None (звонок в fallback).
        """
        matches = self.find(value)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1 and prefer_type is not None:
            typed = [n for n in matches if n.type == _norm(prefer_type)]
            if len(typed) == 1:
                return typed[0]
        return None

    # ---------- Служебное ----------

    def schema(self) -> dict[str, list[str]]:
        """type -> отсортированные поля. Компактное описание для планировщика."""
        result: dict[str, set[str]] = {}
        for node in self.nodes:
            result.setdefault(node.type, set()).update(node.attrs)
        return {t: sorted(f) for t, f in sorted(result.items())}

    def is_healthy(self) -> bool:
        """Распарсилось ли >= 50% файлов. Иначе формат другой -> нужен fallback."""
        return bool(self.nodes) and len(self.nodes) / max(self.files_seen, 1) >= 0.5

    def __len__(self) -> int:
        return len(self.nodes)

    def __iter__(self):
        return iter(self.nodes)


def main() -> None:
    """Демо: разбор тренировочной базы и sanity-check linking'а."""
    # parents[3]: solutions/search/v2/kb.py -> корень проекта
    base = Path(__file__).resolve().parents[3]
    for task in ("search_0001", "search_0005"):
        kb_dir = base / "tasks" / "task-dir" / "task-dir" / task / "workspace" / "kb"
        kb = KnowledgeBase.from_dir(kb_dir)
        print(f"{task}: нод={len(kb)} файлов={kb.files_seen} healthy={kb.is_healthy()}")
        print(f"  схема: {kb.schema()}")
    print()
    for task, question in (
        ("search_0001", "In which year was Irina Smith born?"),
        ("search_0005", "In which city is the headquarters of Sergei Garcia's employer?"),
    ):
        kb = KnowledgeBase.from_dir(
            base / "tasks" / "task-dir" / "task-dir" / task / "workspace" / "kb")
        hits = kb.find_in_text(question)
        print(f"[{task}] {question}\n  упомянуты: {[n.name for n in hits]}")


if __name__ == "__main__":
    main()
