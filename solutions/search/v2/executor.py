# -*- coding: utf-8 -*-
"""Детерминированное исполнение плана поверх KnowledgeBase. Без LLM.

Шаги идут по порядку; значение-имя авто-резолвится в ноду (так из
find/get-цепочек возникает многохоповый обход графа). Любая неудача —
нет сущности, нет поля, неоднозначность, пустой match — это None,
то есть звонок в fallback-лестницу солвера. Никаких догадок.

Форматирование ответа (вербатим из KB): нода -> каноническое имя;
скаляр -> str; список -> имена через ", "; число -> str(int).
"""

from __future__ import annotations

from typing import Any

from solutions.search.v2.kb import KnowledgeBase, Node
from solutions.search.v2.planner import Plan


def execute(plan: Plan, kb: KnowledgeBase) -> str | None:
    """Исполнить план. Ответ или None (неудача -> fallback)."""
    values: dict[str, Any] = {}
    for step in plan.steps:
        if step.op == "find":
            nodes = kb.find(step.args["name"])
            if step.args.get("type"):
                nodes = [n for n in nodes if n.type == step.args["type"]]
            if len(nodes) != 1:
                return None                 # нет или неоднозначно — не угадываем
            values[step.var] = nodes[0]
        elif step.op == "get":
            src = values.get(step.args["from"])
            node = src if isinstance(src, Node) else _as_node(src, kb)
            if node is None:
                return None
            value = node.get(step.args["field"])
            if value is None:
                return None
            values[step.var] = _as_node(value, kb) or value
        elif step.op == "match":
            values[step.var] = kb.match(step.args["field"], step.args["value"],
                                        type=step.args.get("type"))
        elif step.op == "count":
            src = values.get(step.args["from"])
            if not isinstance(src, list):
                return None
            values[step.var] = len(src)
        else:
            return None                     # недостижимо после валидации
    return _format(values.get(plan.answer))


def _as_node(value: Any, kb: KnowledgeBase) -> Node | None:
    """Значение-имя -> нода (однозначный резолв), иначе None."""
    if not isinstance(value, str):
        return None
    return kb.resolve(value)


def _format(value: Any) -> str | None:
    """Ответ вербатим: нода -> имя, список -> имена через ', ', прочее -> str."""
    if value is None:
        return None
    if isinstance(value, Node):
        return value.name
    if isinstance(value, list):
        if not value:
            return None
        return ", ".join(v.name if isinstance(v, Node) else str(v) for v in value)
    return str(value)
