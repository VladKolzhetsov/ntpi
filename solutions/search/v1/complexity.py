"""Бесплатная эвристика стоимости первого вызова search/v1 (noreason).

Считает операции DSL, НЕ хопы: find + два get = 3 шага. Повторные
упоминания поля считаются отдельно. Это оценка, не семантический парсер
и не доказательство решаемости. Не использует expected и не обходит рёбра KB.

Калибровка: эксперименты 2026-11-11, планы длиной 1–4 операции.
predicted_cost/cost_low/cost_high — output-эквиваленты ПЕРВОГО вызова;
диапазон эвристический, не доверительный интервал и не бюджет на ремонты.
confidence — сила структурных признаков, не вероятность правильного ответа.
За пределами 1–4 шагов прогноз является экстраполяцией.

Запуск из корня (без LLM, без записи ответов):
    .venv/bin/python -m solutions.search.v1.complexity --tasks-dir tasks/task-dir/task-dir
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass

from solutions.question import extract_question
from solutions.search.v1.kb import KnowledgeBase
from solutions.task_parser import Task


BASE_COST = 77.0
STEP_COST = 21.5
CALIBRATED_MAX_STEPS = 4

# Только синонимы полей, присутствующих в текущей схеме. Словарь расширяем
# по отдельным примерам, не по id задач. Поля с одинаковыми синонимами
# считаются одним упоминанием, но снижают уверенность.
FIELD_ALIASES = {
    "born": ("birth", "birth year", "year of birth"),
    "birth": ("born", "birth year", "year of birth"),
    "headquarters": ("headquartered",),
    "employer": ("works for", "work for", "employed by"),
}
TYPE_ALIASES = {"person": ("people", "persons"), "company": ("companies",)}


@dataclass
class SearchComplexity:
    predicted_steps: int
    predicted_cost: float
    cost_low: float
    cost_high: float
    confidence: float
    reasons: list[str]
    fields: list[str]
    strategy: str
    extrapolated: bool


def _normal(text: str) -> str:
    return text.casefold().replace("’", "'").replace("_", " ")


def _pattern(text: str) -> str:
    return r"(?<!\w)" + r"\s+".join(re.escape(w) for w in text.split()) + r"(?!\w)"


def estimate(question: str, schema: dict[str, list[str]], *,
             kb: KnowledgeBase | None = None) -> SearchComplexity:
    """Оценить первый вызов. Опциональная KB нужна только для linking имён.

    Без KB прямой и обратный поиск различаем по форме вопроса. При
    неизвестной структуре возвращаем базовые 2 шага с низкой уверенностью.
    Это НЕ рекомендация пропустить задачу или ограничить max_tokens.
    """
    question = extract_question(question)
    full_question = _normal(question)
    # Контекстные предложения не должны добавлять операции к самому вопросу.
    # Точка в инициалах (I. Petrov) не является границей такого предложения.
    question = re.split(r"[.!?]\s+(?=(?:Who|What|Which|Where|When|How|In which)\b)",
                        question, flags=re.I)[-1]
    text = _normal(question)
    reasons: list[str] = []
    confidence = 0.7
    if kb is not None:
        hits = kb.find_in_text(question)
        reasons.append(f"entity matches: {len(hits)}")
        if len(hits) != 1:
            confidence = 0.4
            reasons.append("missing/ambiguous anchor; fallback risk not priced")
        # Имена могут содержать Father, Born и другие названия полей.
        aliases = {_normal(a) for node in hits for a in node.aliases() if a.strip()}
        for alias in sorted(aliases, key=len, reverse=True):
            text = re.sub(_pattern(alias), lambda m: " " * len(m.group()), text)
        if not kb.is_healthy():
            confidence = 0.1
            reasons.append("unhealthy KB: planner may be bypassed; estimate not applicable")

    phrases: dict[str, set[str]] = {}
    for field in sorted({f for fields in schema.values() for f in fields}):
        canonical = _normal(field)
        for phrase in (canonical, *FIELD_ALIASES.get(canonical, ())):
            phrases.setdefault(phrase, set()).add(field)
    matches: list[tuple[int, int, str]] = []
    occupied: set[int] = set()
    # Самое длинное совпадение побеждает: Birth year не два поля Birth+Year.
    for phrase in sorted(phrases, key=lambda p: (-len(p), p)):
        for match in re.finditer(_pattern(phrase), text):
            span = set(range(match.start(), match.end()))
            if span & occupied:
                continue
            occupied.update(span)
            fields = sorted(phrases[phrase])
            matches.append((match.start(), match.end(), fields[0]))
            if len(fields) > 1:
                confidence = min(confidence, 0.4)
                reasons.append(f"ambiguous field alias: {phrase} -> {fields}")
    fields = [field for _, _, field in sorted(matches)]
    reasons.append(f"field mentions (text order, not traversal): {fields}")

    type_names = set()
    for label in schema:
        label = _normal(label)
        type_names.update((label, label + "s", *TYPE_ALIASES.get(label, ())))
    subject = "|".join(re.escape(t) for t in sorted(type_names, key=len, reverse=True))
    count = bool(re.match(r"^how many\b", text))
    reverse = bool(subject and re.match(
        rf"^(?:(?:which|what)\s+(?:{subject})\b|which person\b)", text))
    if count and len(fields) == 1:
        strategy, steps = "match_count", 2
        reasons.append("match + count; no extra output operation")
    elif reverse and len(fields) == 1:
        strategy, steps = "match", 1
        reasons.append("match returns names directly; list length does not enlarge the plan")
    elif fields and not count and not reverse:
        strategy, steps = "find_get", 1 + len(fields)
        reasons.append("one find + one get per field occurrence")
    else:
        strategy, steps = "unknown", max(2, 1 + len(fields))
        confidence = min(confidence, 0.25)
        reasons.append("unsupported/unknown structure; conservative step-count guess")

    if re.search(r"\b(?:and|or|not|except|most|least|every|until)\b", text):
        confidence = min(confidence, 0.3)
        reasons.append("coordination/filter/iteration may require a different plan")
    if re.search(r"\b\d+\s+(?:times|steps|generations)\b|\b(?:ancestor|descendant)\b", full_question):
        confidence = min(confidence, 0.2)
        reasons.append("implicit depth: counting field mentions may underestimate steps")
    extrapolated = steps > CALIBRATED_MAX_STEPS
    if extrapolated:
        confidence = min(confidence, 0.35)
        reasons.append("extrapolation beyond measured 1–4 steps; check planner output cap")
    cost = BASE_COST + STEP_COST * steps
    margin = 0.15 if confidence >= 0.6 else 0.4
    return SearchComplexity(
        steps, round(cost, 1), round(cost * (1 - margin), 1),
        round(cost * (1 + margin), 1), confidence, reasons, fields, strategy,
        extrapolated,
    )


def estimate_task(task: Task) -> SearchComplexity:
    """Удобный вход для экспериментов; expected/reward не читаются."""
    if task.task_type != "search":
        raise ValueError("search complexity estimator requires a search task")
    kb_dir = task.workspace_dir / "kb"
    if not kb_dir.is_dir():
        kb_dir = task.dir / "kb"
    kb = KnowledgeBase.from_dir(kb_dir)
    return estimate(task.instruction, kb.schema(), kb=kb)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks-dir", default="tasks/task-dir/task-dir")
    parser.add_argument("--only", nargs="*", help="task ids to inspect")
    args = parser.parse_args()
    for task in Task.load_dir(args.tasks_dir):
        if task.task_type == "search" and (not args.only or task.task_id in args.only):
            print(json.dumps({"task_id": task.task_id, "task_dir": str(task.dir),
                              **asdict(estimate_task(task))},
                             ensure_ascii=False))


if __name__ == "__main__":
    main()
