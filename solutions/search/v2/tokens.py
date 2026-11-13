"""Приближённый token policy для компактного v2/noreason; не токенизатор.

OLS по медианам 20 задач (58 успешных первых вызовов), 2026-11-11.
Input по символам фактических messages; на ремонтах/QA — экстраполяция.
Бюджетный гейт использует запас 10% input, но не является атомарной
резервацией глобального бюджета между параллельными воркерами.
"""

import math

OUTPUT_BASE = 11.0
OUTPUT_PER_STEP = 16.7671
INPUT_BASE = 60.289
INPUT_PER_CHAR = 0.236765
UNKNOWN_CAP = 400
MIN_CAP = 100
INPUT_RESERVE_FACTOR = 1.10


def predict_input(messages: list[dict]) -> int:
    return math.ceil(INPUT_BASE + INPUT_PER_CHAR * sum(len(m['content']) for m in messages))


def predict_output(steps: int) -> int:
    return math.ceil(OUTPUT_BASE + OUTPUT_PER_STEP * steps)


def recommend_cap(output: int, confidence: float, strategy: str) -> int:
    margin = 0.30 if confidence >= 0.6 else 0.60
    cap = math.ceil(max(MIN_CAP, output * (1 + margin) + 16) / 32) * 32
    return max(UNKNOWN_CAP, cap) if strategy == 'unknown' else cap


def affordable_cap(requested: int, minimum: int, messages: list[dict], remaining: float | None) -> int | None:
    """Не сужать ниже ожидаемого output (или предыдущего капа при усечении)."""
    cap = requested
    if remaining is not None:
        reserve = predict_input(messages) * INPUT_RESERVE_FACTOR / 10
        cap = min(cap, math.floor(remaining - reserve))
    return cap if cap >= minimum and cap > 0 else None
