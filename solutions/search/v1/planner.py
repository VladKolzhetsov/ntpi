# -*- coding: utf-8 -*-
"""Планировщик search-задач: вопрос + схема KB -> исполняемый JSON-план.

Единственный платный шаг search-пайплайна (~120 output-экв. на задачу):
один вызов LLM (noreason-модель, temp 0, кап max_tokens, opportunistic
json-режим — чистый JSON без фенсов) и максимум один ремонтный раунд,
если план невалиден.

Разделение ответственности:
- планировщик проверяет совместимость вопроса СО СХЕМОЙ (ops/поля/типы/
  ссылки на переменные — всё это было в промпте, валидируем строго,
  регистр полей и типов чиним автоматически);
- существование сущностей и значений проверяет ИСПОЛНИТЕЛЬ: find/match
  толерантны к регистру, пустой/неоднозначный результат -> None -> fallback.

Невыполнимость тоже двухуровневая: вопрос вне схемы (нет нужного поля
нигде) распознаёт планировщик ({"unanswerable": true}); вопрос по схеме,
но данных нет — исполнитель (пустой find/match).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from solutions.llm import LLMClient, LLMError, QWEN_38_27B_NOREASON
from solutions.planning import compile_text, extract_json
from solutions.search.v1.kb import KnowledgeBase

# DSL: допустимые операции плана
OPS = ("find", "get", "match", "count")

_VAR_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")

SYSTEM_PROMPT = """You are a query planner for a small knowledge base (KB).
Convert the question into a JSON query plan. Reply with JSON only: no explanations, no markdown.

Plan format: {"steps": [...], "answer": "<var>"}

Operations:
- {"var": "x", "op": "find", "name": "<entity name>", "type": "<type>"} — the entity named in the question. Returns a node.
- {"var": "y", "op": "get", "from": "x", "field": "<Field>"} — a field of the node/value from step x. If the value is the name of another entity, it is resolved to that entity automatically, so you can read further fields from it.
- {"var": "m", "op": "match", "type": "<type>", "field": "<Field>", "value": "<value>"} — all entities of the type whose field equals the value. Returns a list.
- {"var": "n", "op": "count", "from": "m"} — size of the list from a match step.

Answer formatting is automatic: node -> its name; scalar -> as is; list -> names joined with ", "; count -> the number.
The answer must reference the variable of the step that produces the final answer.

Rules:
- Use only field names from the KB schema; never invent values.
- Copy entity names from the question as-is.
- Chain steps for multi-hop questions (person -> Employer -> Headquarters).
- If the question needs a field that does not exist in the schema, reply {"unanswerable": true}.

Example card:
# Anna Petrova
Born: 1980
Employer: Northwind Ltd
Mentor: Ivan Petrov

Example plan for "In which city is the employer of Anna Petrova headquartered?":
{"steps": [{"var": "p", "op": "find", "name": "Anna Petrova"}, {"var": "c", "op": "get", "from": "p", "field": "Employer"}, {"var": "h", "op": "get", "from": "c", "field": "Headquarters"}], "answer": "h"}"""


# ---------- Контракты ----------

@dataclass
class Step:
    var: str            # имя переменной ("p", "c", ...)
    op: str             # find | get | match | count
    args: dict          # аргументы операции (канонизированные)


@dataclass
class Plan:
    steps: list[Step]
    answer: str         # переменная с ответом


@dataclass
class PlanOutcome:
    plan: Plan | None = None   # валидный исполняемый план
    unanswerable: bool = False  # вопрос вне схемы KB
    error: str | None = None    # последняя причина неудачи (для логов)


# ---------- Валидация ----------

def _canon_type(value: str, schema: dict[str, list[str]]) -> str | None:
    low = value.strip().casefold()
    for known in schema:
        if known.casefold() == low:
            return known
    return None


def _canon_field(name: str, allowed: list[str]) -> str | None:
    low = name.strip().casefold()
    for known in allowed:
        if known.casefold() == low:
            return known
    return None


def validate_plan(data: dict,
                  schema: dict[str, list[str]]
                  ) -> tuple[Plan | None, bool, str | None]:
    """Валидация плана против схемы. -> (plan, unanswerable, error).

    Строго там, где планировщик видел данные (ops, поля, типы, ссылки);
    регистр полей/типов нормализуем по схеме — самый частый класс ошибок
    уходит без ремонтного вызова.
    """
    if not isinstance(data, dict):
        return None, False, "plan must be a JSON object"
    if data.get("unanswerable") is True:
        return None, True, None
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        return None, False, "plan must contain a non-empty 'steps' array"
    answer = data.get("answer")
    if not isinstance(answer, str) or not _VAR_RE.match(answer):
        return None, False, "'answer' must be a variable name"

    all_fields = sorted({f for fields in schema.values() for f in fields})
    steps: list[Step] = []
    vars_info: dict[str, tuple[str, str | None]] = {}  # var -> (kind, type)

    for i, item in enumerate(steps_raw):
        where = f"step {i + 1}"
        if not isinstance(item, dict):
            return None, False, f"{where}: must be a JSON object"
        var = item.get("var")
        if not isinstance(var, str) or not _VAR_RE.match(var):
            return None, False, f"{where}: 'var' must be a simple identifier like a, b, c"
        if var in vars_info:
            return None, False, f"{where}: variable {var!r} is already used"
        op = item.get("op")
        if op not in OPS:
            return None, False, f"{where}: unknown op {op!r}; allowed ops: {', '.join(OPS)}"
        if op == "find":
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                return None, False, f"{where}: 'find' requires a non-empty 'name'"
            node_type = _resolve_declared_type(item.get("type"), schema)
            if node_type == _UNKNOWN:
                return None, False, f"{where}: unknown type {item.get('type')!r}; known types: {', '.join(schema)}"
            steps.append(Step(var, "find", {"name": name.strip(), "type": node_type}))
            vars_info[var] = ("node", node_type)
        elif op == "get":
            src = item.get("from")
            if src not in vars_info:
                return None, False, f"{where}: 'from' refers to unknown variable {src!r}; defined: {', '.join(vars_info) or 'none'}"
            kind, src_type = vars_info[src]
            if kind not in ("node", "value"):
                return None, False, f"{where}: 'get' cannot read from a {kind}; it needs a node or a value"
            field_name = item.get("field")
            if not isinstance(field_name, str) or not field_name.strip():
                return None, False, f"{where}: 'get' requires a 'field'"
            allowed = schema[src_type] if src_type else all_fields
            field_canon = _canon_field(field_name, allowed)
            if field_canon is None:
                owner = f" of {src_type}" if src_type else ""
                return None, False, f"{where}: unknown field {field_name!r}{owner}; known fields: {', '.join(allowed)}"
            steps.append(Step(var, "get", {"from": src, "field": field_canon}))
            vars_info[var] = ("value", None)
        elif op == "match":
            node_type = _resolve_declared_type(item.get("type"), schema)
            if node_type == _UNKNOWN:
                return None, False, f"{where}: unknown type {item.get('type')!r}; known types: {', '.join(schema)}"
            field_name = item.get("field")
            if not isinstance(field_name, str) or not field_name.strip():
                return None, False, f"{where}: 'match' requires a 'field'"
            allowed = schema[node_type] if node_type else all_fields
            field_canon = _canon_field(field_name, allowed)
            if field_canon is None:
                return None, False, f"{where}: unknown field {field_name!r}; known fields: {', '.join(allowed)}"
            value = item.get("value")
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                value = str(value)          # числа в JSON -> строка значений KB
            if not isinstance(value, str) or not value:
                return None, False, f"{where}: 'match' requires a 'value'"
            steps.append(Step(var, "match",
                              {"type": node_type, "field": field_canon, "value": value}))
            vars_info[var] = ("list", node_type)
        else:  # count
            src = item.get("from")
            if src not in vars_info:
                return None, False, f"{where}: 'from' refers to unknown variable {src!r}; defined: {', '.join(vars_info) or 'none'}"
            if vars_info[src][0] != "list":
                return None, False, f"{where}: 'count' needs a list from a 'match' step"
            steps.append(Step(var, "count", {"from": src}))
            vars_info[var] = ("number", None)

    if answer not in vars_info:
        return None, False, f"'answer' must reference one of the variables: {', '.join(vars_info)}"
    return Plan(steps=steps, answer=answer), False, None


_UNKNOWN = object()  # маркер «тип объявлен, но не найден в схеме»


def _resolve_declared_type(declared, schema: dict[str, list[str]]):
    """type объявлен -> каноническое имя из схемы; не объявлен -> None;
    объявлен, но неизвестен -> _UNKNOWN."""
    if declared is None:
        return None
    if not isinstance(declared, str):
        return _UNKNOWN
    canon = _canon_type(declared, schema)
    return canon if canon is not None else _UNKNOWN


# ---------- Планировщик ----------

def _user_prompt(question: str, schema: dict[str, list[str]]) -> str:
    lines = ["KB schema:"]
    for entity_type, fields in schema.items():
        lines.append(f"- {entity_type}: {', '.join(fields)}")
    lines.append("")
    lines.append(f"Question: {question}")
    return "\n".join(lines)


class SearchPlanner:
    """question + kb.schema() -> Plan. 1 вызов LLM + <= max_repairs ремонтов."""

    def __init__(self, client: LLMClient,
                 model: str = QWEN_38_27B_NOREASON,
                 max_repairs: int = 1,
                 max_tokens: int = 250):
        self.client = client
        self.model = model
        self.max_repairs = max_repairs
        self.max_tokens = max_tokens

    def plan(self, question: str, kb: KnowledgeBase) -> PlanOutcome:
        schema = kb.schema()
        if not schema:
            return PlanOutcome(error="KB schema is empty")
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _user_prompt(question, schema)},
        ]
        error: str | None = None
        for _ in range(self.max_repairs + 1):
            try:
                text = compile_text(self.client, self.model, messages,
                                    max_tokens=self.max_tokens, json_mode=True)
            except LLMError as e:
                return PlanOutcome(error=f"LLM call failed: {e}")
            data = extract_json(text)
            if data is None:
                error = "response is not a JSON object"
            else:
                plan, unanswerable, error = validate_plan(data, schema)
                if plan is not None or unanswerable:
                    return PlanOutcome(plan=plan, unanswerable=unanswerable)
            # ремонтный раунд: модель видит свой ответ и короткую ошибку
            messages = messages + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content":
                    f"Invalid plan: {error}. Reply with the corrected JSON plan only."},
            ]
        return PlanOutcome(error=error)
