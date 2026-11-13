# -*- coding: utf-8 -*-
"""Общий каркас компиляции вопросов в артефакты (план/код).

Сознательно тонкий: всё, что тратит токены, идёт через LLMClient (в нём уже
есть бюджетный гейт и учёт usage). Здесь только то, что повторяется для
каждого типа задач: дисциплинированный вызов и извлечение артефакта
(JSON / fenced-блок) из ответа модели.

Специфика типов — промпты, валидация, ремонтные раунды — живёт в
планировщиках (search/planner.py, будущий code/planner.py).
"""

from __future__ import annotations

import json
import re

from solutions.llm import LLMClient, LLMResponse

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)```", re.DOTALL)


def compile_text(client: LLMClient, model: str, messages: list[dict],
                 max_tokens: int = 250, temperature: float = 0.0,
                 json_mode: bool = False) -> str:
    """Вызов LLM с дисциплиной планировщика: temp 0, кап max_tokens.

    json_mode включает response_format json_object (opportunistic: клиент
    сам откатится и запомнит при HTTP 400). Промпт обязан содержать слово
    "JSON" — требование OpenAI-режима.

    Ошибки LLM прокидываются наверх — решение «фолбэкить или сдаваться»
    остаётся за вызывающим (солвером).
    """
    return compile_response(client, model, messages, max_tokens, temperature, json_mode).text


def compile_response(client: LLMClient, model: str, messages: list[dict],
                     max_tokens: int = 250, temperature: float = 0.0,
                     json_mode: bool = False) -> LLMResponse:
    """Тот же вызов с usage/raw для диагностики усечения; capture остаётся в chat."""
    response_format = {"type": "json_object"} if json_mode else None
    return client.chat(model, messages, temperature=temperature,
                       max_tokens=max_tokens, response_format=response_format)


def extract_json(text: str) -> dict | None:
    """Вытащить JSON-объект из ответа модели.

    Порядок попыток: голый JSON -> fenced-блоки (```json и без языка) ->
    первый сбалансированный {...} в тексте. Валиден только dict; массив,
    скаляр или мусор -> None.
    """
    if not text:
        return None
    stripped = text.strip()
    for candidate in _json_candidates(stripped):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return None


def _json_candidates(text: str):
    """Кандидаты на парсинг в порядке убывания надёжности."""
    yield text
    for block in _FENCE_RE.findall(text):
        yield block.strip()
    chunk = _first_balanced_object(text)
    if chunk:
        yield chunk


def _first_balanced_object(text: str) -> str | None:
    """Подстрока первого сбалансированного {...} с учётом строк и escape.

    Грамотно обрабатывает фигурные скобки внутри строковых значений.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None
