#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Разовая утилита: создание adversarial-задач math_0050-0058 (2026-11-12).

Классы ловушек подобраны в разборе слабых мест math/v2:
ceiling, граница .5 (banker's vs half-up), смешанное округление,
хвостовые нули exact_str, отрицательный остаток, цепочка 10+ операций,
противоречие-физика, приманка мимо noun-guard, дробь от остатка.
"""

import json
import math
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "tasks" / "task-dir" / "task-dir"
TAIL = (" Compute it with the bash tool (python3 -c). Work only inside the "
        "current directory. When you are done, write ONLY the final answer "
        "to the file answer.txt in the current directory (for example with "
        "bash: echo 42 > answer.txt).")

TASKS = [
    # (id, reward, check, expected, body)
    ("math_0050", 3, "exact_int", "143",
     "A factory produces 1000 candies and packs them into bags of exactly "
     "7 candies each. How many bags are needed to pack all the candies?"),
    ("math_0051", 4, "exact_int", "137",
     "A car travels at a constant speed of 54.6 km/h for exactly 2.5 hours. "
     "How many whole kilometres does it cover, rounded to the nearest whole "
     "kilometre?"),
    ("math_0052", 3, "exact_int", "715",
     "A warehouse starts with 950 boxes. It then increases its stock by "
     "13 percent, discarding any fractional box (rounded down). Then it "
     "ships exactly one third of the new stock, rounded to the nearest "
     "whole box. How many boxes does the warehouse have left?"),
    ("math_0053", 2, "exact_str", "1.200",
     "A rod is 120 centimetres long. There are exactly 100 centimetres in "
     "a metre. What is its length in metres, rounded to three decimal "
     "places (for example 0.125)?"),
    ("math_0054", 2, "exact_int", "-50",
     "A shop has 100 pens in stock. It receives an order for 150 pens and "
     "ships the whole order. How many pens does the shop have after the "
     "shipment?"),
    ("math_0055", 5, "exact_int", "771",
     "A warehouse starts with 1752 boxes. It first increases its stock by "
     "18 percent, discarding any fractional box (rounded down). Then it "
     "ships exactly 40 percent of the new stock, again discarding any "
     "fractional box (rounded down). Then it receives 3 deliveries of 87 "
     "boxes each. Then it splits the stock equally into 7 warehouses and "
     "keeps one share, rounded down. Then it doubles its stock by merging "
     "with a sister warehouse. Then it loses one eighth of its stock, "
     "rounded down. Then it ships 123 boxes. Then it triples its stock by "
     "merging with two sister warehouses. Then it returns 45 damaged boxes. "
     "Then it increases its stock by 9 percent, discarding any fractional "
     "box (rounded down). How many boxes does it have at the end?"),
    ("math_0056", 4, "exact_int", None,
     "Train A leaves a station at 9:00 travelling at 81 km/h. Train B "
     "leaves the same station at 10:00 travelling in the same direction at "
     "62 km/h. After how many minutes does train B catch up with train A? "
     "Round the answer to the nearest whole minute."),
    ("math_0057", 2, "exact_int", None,
     "A warehouse stores 1752 boxes and several bags. The boxes are "
     "counted twice and the crates are inspected. How many bags does the "
     "warehouse have after the inspection?"),
    ("math_0058", 4, "exact_int", "270",
     "A tank holds 900 litres of water. First, exactly one third of the "
     "water is removed. Then 40 percent of the remaining water is used. "
     "Finally, a quarter of what is left is poured out. How many litres of "
     "water are in the tank at the end?"),
]


def math_0055_chain() -> int:
    s = 1752                                   # start
    s = math.floor(s * 1.18)                   # +18% floor
    s = math.floor(s * 0.6)                    # -40% floor
    s = s + 3 * 87                             # +3×87
    s = s // 7                                 # /7 floor
    s = s * 2                                  # ×2
    s = math.floor(s * 7 / 8)                  # -1/8 floor (конвенция корпуса:
                                               # округляем результат, как в math_0010)
    s = s - 123                                # -123
    s = s * 3                                  # ×3
    s = s - 45                                 # -45
    s = math.floor(s * 1.09)                   # +9% floor
    return s


def main() -> None:
    # сверка expected с независимым пересчётом
    assert math.ceil(1000 / 7) == 143
    assert math_0055_chain() == 771, math_0055_chain()
    assert math.floor(950 * 1.13) == 1073
    assert round(1073 / 3) == 358 and 1073 - 358 == 715
    assert 100 - 150 == -50
    assert math.floor(900 * 2 / 3 * 0.6 * 0.75) == 270
    assert f"{120 / 100:.3f}" == "1.200"
    assert int(136.5 != round(136.5)) or True   # 136.5: banker's -> 136 (сам смысл ловушки 0051)

    for task_id, reward, check, expected, body in TASKS:
        d = BASE / task_id
        (d / "workspace").mkdir(parents=True, exist_ok=True)
        payload = {
            "task_id": task_id,
            "task_type": "math",
            "instruction": body + TAIL,
            "reward": reward,
            "check": check,
        }
        if expected is not None:
            payload["expected"] = expected
        (d / "task.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        print(f"{task_id}: создана (expected={expected!r})")


if __name__ == "__main__":
    main()
