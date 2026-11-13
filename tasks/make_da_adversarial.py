#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Разовый генератор: adversarial-пакет data_analysis_0034-0045 (2026-11-12).

Классы ловушек выбраны по слабым местам data_analysis/v2 (precheck ->
profile -> программа в cwd=workspace):

| id   | ловушка                                   | что проверяет              |
|------|-------------------------------------------|----------------------------|
| 0034 | nan-ячейки в суммируемой колонке          | nan-политика (skip, не 0)  |
| 0035 | регистр значения ('Widget' vs 'widget')   | нормализация значений      |
| 0036 | разделитель ';' + BOM                     | Sniffer/read_header        |
| 0037 | join двух файлов (регион в customers.csv) | многофайловый вход         |
| 0038 | second highest DISTINCT price             | distinct + порядок         |
| 0039 | «in whole dollars» при дробной сумме      | формат/округление whole    |
| 0040 | объявлена колонка, которой нет в файле    | declared_column_absent     |
| 0041 | месяц словом ('March')                    | парсинг месяцев            |
| 0042 | колонка целиком пустая                    | nan на всю колонку         |
| 0043 | границы months 7 through 8 (включительно) | включительность диапазона  |
| 0044 | нет CSV вообще                            | csv_file_missing           |
| 0045 | процент + точность в exact_str            | формат вывода              |

Каждый expected пересчитывается независимым кодом ниже и сверяется
assert'ом до записи задачи: неверная разметка ломает калибровку.
"""

import csv
import io
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "tasks" / "task-dir" / "task-dir"

TAIL = (" Use bash with python3 to compute it. Work only inside the current "
        "directory. When you are done, write ONLY the final answer to the "
        "file answer.txt in the current directory (for example with bash: "
        "echo 42 > answer.txt).")

HEADER = ["order_id", "region", "product", "month", "units", "price"]


def rows_text(header, rows, sep=",") -> str:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=sep, lineterminator="\n")
    w.writerow(header)
    w.writerows(rows)
    return buf.getvalue()


# ---------- данные задач (и независимый пересчёт expected) ----------

# 0034: nan в units (пустые ячейки) — сумма идёт только по числам
R0034 = [
    [1, "west", "widget", 5, 10, 2.5],
    [2, "west", "widget", 6, "", 2.5],      # nan
    [3, "east", "widget", 7, 7, 2.5],
    [4, "east", "widget", 8, "", 2.5],      # nan
    [5, "north", "widget", 9, 5, 2.5],
]
EXP0034 = 10 + 7 + 5

# 0035: регистр значения — 'Widget' и 'widget' это один продукт
R0035 = [
    [1, "west", "Widget", 3, 4, 1.0],
    [2, "east", "widget", 3, 6, 1.0],
    [3, "north", "WIDGET", 3, 5, 1.0],
    [4, "south", "gadget", 3, 9, 1.0],
]
EXP0035 = 4 + 6 + 5

# 0036: разделитель ';' — сумма по units
R0036 = [
    [1, "west", "bolt", 4, 12, 3.0],
    [2, "east", "bolt", 4, 8, 3.0],
    [3, "north", "bolt", 4, 5, 3.0],
]
EXP0036 = 12 + 8 + 5

# 0037: join — units в orders.csv, регион в customers.csv
ORDERS0037 = [
    [1, 10, 5],
    [2, 11, 7],
    [3, 10, 3],
    [4, 12, 9],
]
CUSTOMERS0037 = [
    [10, "Ann", "east"],
    [11, "Bob", "west"],
    [12, "Cid", "east"],
]
EXP0037 = 5 + 3 + 9            # customer_id с регионом 'east': 10, 12

# 0038: second highest DISTINCT price (повторы не считаются)
R0038 = [
    [1, "west", "bolt", 1, 1, 25],
    [2, "west", "bolt", 1, 1, 40],
    [3, "west", "bolt", 1, 1, 25],   # повтор 25
    [4, "west", "bolt", 1, 1, 12],
    [5, "west", "bolt", 1, 1, 40],   # повтор 40
]
EXP0038 = 25                   # distinct: 12, 25, 40 -> второй 25

# 0039: сумма price дробная -> «in whole dollars» (округление, не отбрасывание)
R0039 = [
    [1, "west", "bolt", 1, 1, 10.4],
    [2, "west", "bolt", 1, 1, 20.4],
    [3, "west", "bolt", 1, 1, 5.4],
]
EXP0039 = 36                   # 36.2 -> 36 (half-up -> 36)

# 0041: месяц словом
R0041 = [
    [1, "west", "bolt", "January", 10, 1.0],
    [2, "west", "bolt", "March", 7, 1.0],
    [3, "west", "bolt", "March", 5, 1.0],
    [4, "west", "bolt", "May", 9, 1.0],
]
EXP0041 = 7 + 5

# 0042: колонка discount целиком пустая -> сумма 0
R0042 = [
    [1, "west", "bolt", 1, 1, 10.0, ""],
    [2, "west", "bolt", 1, 1, 20.0, ""],
]
EXP0042 = 0

# 0043: диапазон месяцев включительно (7 и 8 входят, 9 нет)
R0043 = [
    [1, "west", "widget", 6, 100, 1.0],
    [2, "west", "widget", 7, 11, 1.0],
    [3, "west", "widget", 8, 22, 1.0],
    [4, "west", "widget", 9, 100, 1.0],
]
EXP0043 = 11 + 22

# 0045: процент с точностью (exact_str)
R0045 = [
    [1, "east", "bolt", 1, 20, 1.0],
    [2, "west", "bolt", 1, 30, 1.0],
    [3, "north", "bolt", 1, 50, 1.0],
]
EXP0045 = "20.0"               # 20/100 = 20% -> ровно 20.0


def check_expected() -> None:
    """Независимый пересчёт всех expected: разметка не должна врать."""
    assert sum(r[4] for r in R0034 if r[4] != "") == EXP0034, EXP0034
    assert sum(r[4] for r in R0035
               if r[2].strip().lower() == "widget") == EXP0035
    assert sum(r[4] for r in R0036) == EXP0036
    east = {c[0] for c in CUSTOMERS0037 if c[2] == "east"}
    assert sum(o[2] for o in ORDERS0037 if o[1] in east) == EXP0037
    dist = sorted({r[5] for r in R0038}, reverse=True)
    assert dist[1] == EXP0038, dist
    total = sum(r[5] for r in R0039)
    assert round(total) == EXP0039 and total != EXP0039, total
    assert sum(r[4] for r in R0041 if r[3] == "March") == EXP0041
    assert sum(0 for _ in R0042) == EXP0042
    assert sum(r[4] for r in R0043 if 7 <= r[3] <= 8) == EXP0043
    assert f"{100 * 20 / sum(r[4] for r in R0045):.1f}" == EXP0045


def write_task(task_id, reward, check, expected, body, files: dict[str, str]):
    d = BASE / task_id
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (d / "workspace" / name).write_text(text, encoding="utf-8")
    payload = {
        "task_id": task_id,
        "task_type": "data_analysis",
        "instruction": body + TAIL,
        "reward": reward,
        "check": check,
    }
    if expected is not None:
        payload["expected"] = expected
    (d / "task.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"{task_id}: создана (expected={expected!r}, files={list(files)})")


def decl(header, rows) -> str:
    return (f"The file sales.csv has columns {','.join(header)} "
            f"({len(rows)} rows). ")


def main() -> None:
    check_expected()

    write_task(
        "data_analysis_0034", 4, "exact_int", str(EXP0034),
        decl(HEADER, R0034) +
        "How many units of product 'widget' were sold in months 5 through "
        "9 inclusive? Some cells in the units column are empty.",
        {"sales.csv": rows_text(HEADER, R0034)})

    write_task(
        "data_analysis_0035", 3, "exact_int", str(EXP0035),
        decl(HEADER, R0035) +
        "How many units of product 'widget' were sold in month 3?",
        {"sales.csv": rows_text(HEADER, R0035)})

    write_task(
        "data_analysis_0036", 3, "exact_int", str(EXP0036),
        "The file sales.csv has columns order_id,region,product,month,"
        "units,price (3 rows). How many units of product 'bolt' were sold "
        "in total? Note: this file may use a different delimiter.",
        {"sales.csv": rows_text(HEADER, R0036, sep=";")})

    write_task(
        "data_analysis_0037", 5, "exact_int", str(EXP0037),
        "The file orders.csv has columns order_id,customer_id,units "
        f"({len(ORDERS0037)} rows). The file customers.csv has columns "
        f"customer_id,name,region ({len(CUSTOMERS0037)} rows). How many "
        "units were ordered by customers from region 'east'?",
        {"orders.csv": rows_text(["order_id", "customer_id", "units"],
                                 ORDERS0037),
         "customers.csv": rows_text(["customer_id", "name", "region"],
                                    CUSTOMERS0037)})

    write_task(
        "data_analysis_0038", 4, "exact_int", str(EXP0038),
        decl(HEADER, R0038) +
        "What is the second highest distinct price in the price column, "
        "in whole dollars?",
        {"sales.csv": rows_text(HEADER, R0038)})

    write_task(
        "data_analysis_0039", 3, "exact_int", str(EXP0039),
        decl(HEADER, R0039) +
        "What is the total price of all orders, in whole dollars?",
        {"sales.csv": rows_text(HEADER, R0039)})

    write_task(
        "data_analysis_0040", 4, "exact_int", None,
        "The file sales.csv has columns order_id,region,product,month,"
        "units,price,discount (4 rows). What is the total discount given "
        "across all orders?",
        {"sales.csv": rows_text(HEADER, R0043[:4])})   # колонки discount нет

    write_task(
        "data_analysis_0041", 3, "exact_int", str(EXP0041),
        decl(HEADER, R0041) +
        "How many units of product 'bolt' were sold in March?",
        {"sales.csv": rows_text(HEADER, R0041)})

    write_task(
        "data_analysis_0042", 3, "exact_int", str(EXP0042),
        "The file sales.csv has columns order_id,region,product,month,"
        f"units,price,discount ({len(R0042)} rows). What is the total "
        "discount given across all orders?",
        {"sales.csv": rows_text(HEADER + ["discount"], R0042)})

    write_task(
        "data_analysis_0043", 4, "exact_int", str(EXP0043),
        decl(HEADER, R0043) +
        "How many units of product 'widget' were sold in months 7 through "
        "8 inclusive?",
        {"sales.csv": rows_text(HEADER, R0043)})

    write_task(
        "data_analysis_0044", 3, "exact_int", None,
        "The file sales.csv has columns order_id,region,product,month,"
        "units,price (50 rows). How many units were sold in month 5?",
        {})                                            # файла нет

    write_task(
        "data_analysis_0045", 3, "exact_str", EXP0045,
        decl(HEADER, R0045) +
        "What percentage of all units sold was sold in region 'east'? "
        "Round to one decimal place.",
        {"sales.csv": rows_text(HEADER, R0045)})


if __name__ == "__main__":
    main()
