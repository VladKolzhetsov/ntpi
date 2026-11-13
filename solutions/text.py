# -*- coding: utf-8 -*-
"""Общие текстовые хелперы: токенизация и наивная морфология.

Пока потребители — детерминированные фильтры (math/v2/detectors.py,
data_analysis/precheck.py). Здесь только чистые функции над текстом:
никакой файловой системы, LLM и состояния.
"""

from __future__ import annotations

import re

# единицы: вопрос может называть их длинно, условие — коротко
_STEM_ALIASES = {
    "kilometer": "km", "kilometre": "km",
    "meter": "m", "metre": "m",
    "liter": "l", "litre": "l",
    "gram": "g", "kilogram": "kg",
}

_WORD_RE = re.compile(r"[a-z]+")


def words(text: str) -> list[str]:
    """Слова текста в нижнем регистре (цифры и пунктуация отброшены)."""
    return _WORD_RE.findall(text.lower())


def stem(word: str) -> str:
    """Наивная нормализация формы: apples/apple, crates/crate, cherries/cherry.

    Сначала суффикс (только 's' — иначе crates -> crat), затем алиас
    единиц (kilometers -> km).
    """
    base = word
    if base.endswith("ies") and len(base) >= 5:
        base = base[:-3] + "y"
    elif base.endswith("s") and len(base) >= 4:
        base = base[:-1]
    return _STEM_ALIASES.get(base, base)


def stems_match(a: str, b: str) -> bool:
    """Совпадение стемов с допуском обрезанного хвоста ≤ 2 символов
    (боковые эффекты наивного среза: boxe/box)."""
    if a == b:
        return True
    if abs(len(a) - len(b)) <= 2 and (a.startswith(b) or b.startswith(a)):
        return True
    return False
