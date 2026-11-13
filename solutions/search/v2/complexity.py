"""Бесплатная эвристика стоимости первого вызова search/v2 (noreason).

Считает операции DSL, НЕ хопы: find + два get = 3 шага. Повторные
упоминания поля считаются отдельно. Это оценка, не семантический парсер
и не доказательство решаемости. Не использует expected. Проверяет цепочку
упомянутых отношений по рёбрам KB с ограничением числа состояний.

Помимо первого вызова считает cost_worst — детерминированную верхнюю
границу ПОЛНОЙ стоимости задачи (план + ремонт + fallback QA): лестница
конечна по коду, caps назначаем мы, дамп KB вычислим без LLM, поэтому
вероятности и перенос калибровки с тренировки не нужны.

Калибровка 2026-11-11: компактный v2, qwen3.8-27b-noreason,
58 успешных одновызовных решений / 20 задач, медиана на задачу.
predicted_cost/cost_low/cost_high — output-эквиваленты ПЕРВОГО вызова;
диапазон эвристический, не доверительный интервал и не бюджет на ремонты.
confidence — сила структурных признаков, не вероятность правильного ответа.
Наблюдались глубины 1,2,3,4,5,14; 6–13 — интерполяция, >14 — экстраполяция.

Запуск из корня (без LLM, без записи ответов):
    .venv/bin/python -m solutions.search.v2.complexity --tasks-dir tasks/task-dir/task-dir
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from collections import Counter

from solutions.question import extract_question
from solutions.search.v2.kb import KnowledgeBase
from solutions.task_parser import Task
from solutions.search.v2.tokens import predict_input, predict_output, recommend_cap


CALIBRATED_MAX_STEPS = 14
CALIBRATED_DEPTHS = (1, 2, 3, 4, 5, 14)
CALIBRATED_MARGIN = 0.10  # task-LOO max 6.13%; запас, не доверительный интервал

# cost_worst: детерминированная верхняя граница ПОЛНОЙ стоимости задачи.
# Лестница search/v2 конечна по коду (план + <=1 ремонт + <=1 QA), каждый
# completion ограничен нашим max_tokens на уровне API, дамп KB строится без
# LLM — вероятности не нужны (P(ветка) <= 1), перенос с тренировки не требуется.
QA_OUTPUT_CAP = 60               # зеркалит _fallback_qa в solver.py
ASSISTANT_ECHO_CAP = 500         # text[:2000] в ремонтном промпте ~ 500 ток
REPAIR_ERROR_TOKENS = 60         # хвост "Invalid plan: ... known fields: ..."
TRUNCATION_NOTICE_TOKENS = 30    # приписка при перегенерации после усечения

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
MAX_GRAPH_STATES = 2000
MAX_GRAPH_DEPTH = 64


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
    relation_path: list[str]
    graph_status: str
    predicted_input_tokens: int
    predicted_output_tokens: int
    recommended_max_tokens: int
    cost_worst: float


def _relation_path(kb: KnowledgeBase, anchor, fields: list[str]) -> tuple[list[str], str]:
    """Порядок ВСЕХ упоминаний, включая повторы; не кратчайший путь.

    Единственность графового пути не доказывает семантику вопроса. Повторное
    посещение ноды разрешено: Father/Father и циклы имеют конечную длину.
    Скаляр разрешён только на последнем шаге. Лимит => unknown, не отказ.
    """
    if len(fields) > MAX_GRAPH_DEPTH:
        return [], "limit"
    stack = [(anchor, Counter(fields), [])]
    paths = []
    states = 0
    while stack:
        states += 1
        if states > MAX_GRAPH_STATES:
            return [], "limit"
        node, remaining, path = stack.pop()
        for field, count in remaining.items():
            if count <= 0 or (value := node.get(field)) is None:
                continue
            next_path = path + [field]
            if len(next_path) == len(fields):
                paths.append(next_path)
                if len(paths) > 1:
                    return [], "ambiguous"
            elif (target := kb.resolve(value)) is not None:
                rest = remaining.copy()
                rest[field] -= 1
                stack.append((target, rest, next_path))
    return (paths[0], "unique") if paths else ([], "no_path")


def _normal(text: str) -> str:
    return text.casefold().replace("’", "'").replace("_", " ")


def _pattern(text: str) -> str:
    return r"(?<!\w)" + r"\s+".join(re.escape(w) for w in text.split()) + r"(?!\w)"


def _cost_worst(input_tokens: int, cap: int, qa_input: int | None) -> float:
    """Верхняя граница расхода лестницы search/v2 на ОДНУ задачу.

    cost_worst = C_plan + max(ветки ремонта) + C_fallback, где:
    - C_plan = input/10 + cap: completion физически <= max_tokens (API);
    - ремонт (взаимоисключающие ветки, берём max):
      семантика: (input + эхо ответа + хвост ошибки)/10 + cap;
      усечение:  (input + приписка)/10 + удвоенный кап (округление как в planner);
    - C_fallback = input(QA)/10 + 60; 0, если QA закрыт гейтом (KB пуста/велика).
    qa_input=None -> QA не оцениваем (нет KB) -> слагаемое 0 и причина в reasons.

    Всё вычислимо до первого вызова: промпты и caps наши, дамп KB
    детерминирован. Единственная оценка — символ->токен для input
    (MAPE 0.58%, максимум 3.3%: ~0.5% от cost_worst). Не ожидание
    и не интервал: граница при достаточном бюджете.
    """
    c_plan = input_tokens / 10 + cap
    echo = min(cap, ASSISTANT_ECHO_CAP)          # ответ в ремонтном промпте
    c_semantic = (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap
    trunc_cap = ((cap * 2 + 31) // 32) * 32      # как в planner.plan
    c_trunc = (input_tokens + TRUNCATION_NOTICE_TOKENS) / 10 + trunc_cap
    c_fallback = qa_input / 10 + QA_OUTPUT_CAP if qa_input is not None else 0.0
    return c_plan + max(c_semantic, c_trunc) + c_fallback


def estimate(question: str, schema: dict[str, list[str]], *,
             kb: KnowledgeBase | None = None) -> SearchComplexity:
    """Оценить первый вызов. KB даёт linking и проверку покрытия отношений.

    Без KB прямой и обратный поиск различаем по форме вопроса. При
    неизвестной структуре возвращаем базовые 2 шага с низкой уверенностью.
    Возвращает отдельные прогнозы input/output и рекомендуемый output-кап.
    Не определяет решаемость и не принимает решение о пропуске задачи.
    """
    question = extract_question(question)
    planner_question = question
    full_question = _normal(question)
    # Контекстные предложения не должны добавлять операции к самому вопросу.
    # Точка в инициалах (I. Petrov) не является границей такого предложения.
    question = re.split(r"[.!?]\s+(?=(?:Who|What|Which|Where|When|How|In which)\b)",
                        question, flags=re.I)[-1]
    text = _normal(question)
    # «Which city did ... arrive from?» — city задаёт тип ответа,
    # а не дополнительный get City. Удаляем только конструкцию с auxiliary.
    text = re.sub(r"^(which|what)\s+\w+\s+(did|does|do)\b", r"\1 \2", text)
    reasons: list[str] = []
    confidence = 0.7
    hits = []
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
        # main road -> road; несколько полей с этим хвостом => ambiguity.
        if canonical.startswith(("main ", "primary ")):
            phrases.setdefault(canonical.split(" ", 1)[1], set()).add(field)
    matches: list[tuple[int, int, str]] = []
    occupied: set[int] = set()
    # Вопросительный оборот разрывает arrived from: arrive ... from.
    arrival_fields = phrases.get("arrived from", set())
    if arrival_fields and re.search(r"\barriv(?:e|ed)\s+from\s*\?", text):
        for match in re.finditer(r"\barriv(?:e|ed)\s+from\b", text):
            matches.append((match.start(), match.end(), sorted(arrival_fields)[0]))
            occupied.update(range(match.start(), match.end()))
    elif arrival_fields and re.search(r"\bfrom\b.*\barriv(?:e|ed)\b", text):
        match = re.search(r"\barriv(?:e|ed)\b", text)
        matches.append((match.start(), match.end(), sorted(arrival_fields)[0]))
        occupied.update(range(match.start(), match.end()))
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
    relation_path, graph_status = [], "not_checked"
    if kb is not None and len(hits) == 1 and strategy == "find_get":
        relation_path, graph_status = _relation_path(kb, hits[0], fields)
        reasons.append(f"graph relation coverage: {graph_status}")
        if graph_status != "unique":
            confidence = min(confidence, 0.3)
    extrapolated = steps > CALIBRATED_MAX_STEPS
    if extrapolated:
        confidence = min(confidence, 0.35)
        reasons.append("extrapolation beyond measured 14 steps; check planner output cap")
    elif steps not in CALIBRATED_DEPTHS:
        confidence = min(confidence, 0.5)
        reasons.append("interpolated depth: no direct calibration samples")
    # Local import avoids coupling the planner to the depth estimator.
    from solutions.search.v2.planner import build_messages
    input_tokens = predict_input(build_messages(planner_question, schema))
    output_tokens = predict_output(steps)
    cost = input_tokens / 10 + output_tokens
    cap = recommend_cap(output_tokens, confidence, strategy)
    margin = CALIBRATED_MARGIN if confidence >= 0.6 else 0.4

    # --- cost_worst: граница полной стоимости (см. _cost_worst) ---
    # Локальный импорт деталей QA: solver импортирует complexity на верхнем
    # уровне, обратный импорт здесь цикла не создаёт (вызов уже после загрузки).
    qa_input: int | None = None
    if kb is None:
        reasons.append("cost_worst: no kb, fallback term not priced")
    else:
        from solutions.search.v2.solver import (MAX_FALLBACK_CARDS,
                                                QA_SYSTEM_PROMPT, _dump_cards)
        if len(kb) > MAX_FALLBACK_CARDS:
            reasons.append(f"cost_worst: kb too big ({len(kb)} > "
                           f"{MAX_FALLBACK_CARDS}), fallback term 0")
        elif not len(kb):
            reasons.append("cost_worst: empty kb, fallback term 0")
        else:
            # Промпт QA строим так же, как _fallback_qa в solver.py.
            qa_input = predict_input([
                {"role": "system", "content": QA_SYSTEM_PROMPT},
                {"role": "user", "content":
                    f"Question: {planner_question}\n\nFacts:\n{_dump_cards(kb)}"},
            ])
    worst = _cost_worst(input_tokens, cap, qa_input)
    reasons.append(f"cost_worst: plan+repair+fallback = {worst:.0f}")
    return SearchComplexity(
        steps, round(cost, 1), round(cost * (1 - margin), 1),
        round(cost * (1 + margin), 1), confidence, reasons, fields, strategy,
        extrapolated,
        relation_path, graph_status,
        input_tokens, output_tokens, cap,
        round(worst, 1),
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
