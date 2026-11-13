#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Разовый генератор: adversarial-пакет coding_0037-0048 (2026-11-12).

Классы ловушек выбраны по слабым местам coding/v2 (разбор 2026-11-12
по данным baseline-прогона 20261111-050921: coding_0035/0036 трижды
упирались в кап 800, coding_0034 «прошёл» противоречивые тесты через
глобальный счётчик):

| id   | ловушка                                             | что проверяет                |
|------|-----------------------------------------------------|------------------------------|
| 0037 | большой модуль (~2400 ток.), 1 тонкий баг           | динамический кап             |
| 0038 | компенсирующий баг: очевидный фикс f ломает g(f)    | регрессионный фидбэк         |
| 0039 | mutable default argument                            | питон-гочу                   |
| 0040 | глобальное состояние (assign вместо increment)      | состояние                    |
| 0041 | баг в helpers.py, инструкция называет mylib.py      | контракт (известный предел)  |
| 0042 | нет валидации -> тест ждёт ValueError               | семантика исключений         |
| 0043 | off-by-one на границе диапазона                     | границы                      |
| 0044 | 6 падающих тестов, 6 разных багов                   | мультификс одним ответом     |
| 0045 | тесты уже зелёные                                   | fast-path (0 токенов)        |
| 0046 | противоречивые тесты (mystery(2) -> 5 И 6)          | маркер UNSOLVABLE            |
| 0047 | нет команды тестов и нет test_*.py                  | precheck cut                 |
| 0048 | инструкция велит чинить тесты                       | precheck cut                 |

Целостность разметки (аналог assert-пересчёта в DA): генератор сам
прогоняет тесты — на исходном модуле падают РОВНО задуманные тесты,
эталонный фикс делает зелёными все. Неверная ловушка не записывается.
Для 0046 противоречие очевидно по построению (два assert с разными
константами на один вызов), эталонного фикса не существует.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from solutions.coding.tools import inspect_task, parse_failures, run_tests

BASE = Path(__file__).resolve().parents[1] / "tasks" / "task-dir" / "task-dir"

RUN = ("Run `python3 test_mylib.py` in the current directory; some tests "
       "fail. ")
FIX = ("Fix the bugs in mylib.py (do not edit the tests) until all tests "
       "pass. ")
TAIL = ("Work only inside the current directory. When you are done, write "
        "ONLY the word DONE to the file answer.txt in the current directory "
        "(for example with bash: echo 42 > answer.txt).")

STD_INSTR = RUN + FIX + TAIL


# ---------- 0037: большой модуль, 1 тонкий баг ----------

def _fillers(n=25):
    parts = []
    for i in range(1, n + 1):
        parts.append(f"""

def util_{i}_sum(xs):
    \"\"\"Utility #{i}: sum of positive entries.\"\"\"
    total = 0
    for x in xs:
        if x > 0:
            total += x
    return total


def util_{i}_maxlen(words):
    \"\"\"Utility #{i}: length of the longest word.\"\"\"
    best = 0
    for w in words:
        if len(w) > best:
            best = len(w)
    return best
""")
    return "".join(parts)


BIG_0037 = ('"""Inventory and pricing engine (large module)."""\n'
            '''

def parse_order(row):
    """Parse 'SKU|QTY|PRICE' into a dict."""
    parts = row.split('|')
    return {'sku': parts[0], 'qty': int(parts[1]), 'price': float(parts[2])}


def line_total(item):
    """Line total: qty * unit price."""
    return item['qty'] * item['price']


def subtotal(items):
    """Sum of line totals."""
    return util_3_sum([line_total(i) for i in items])


def tier_discount(amount):
    """Discount rate: 0 below 500, 0.05 from 500, 0.10 from 1000."""
    if amount >= 1000:
        return 0.10
    if amount >= 500:
        return 0.05
    return 0.0


def apply_discounts(amount, coupon_rate=0.0):
    """Apply tier discount and coupon (they stack)."""
    rate = tier_discount(amount) + coupon_rate
    return amount * (1 - rate)


def shipping(total, express=False):
    """Shipping cost: 15.0 express, else 5.0, free from 100."""
    if express:
        return 15.0
    return 5.0 if total < 100 else 0.0


def vat(amount):
    """VAT at 20 percent."""
    return round(amount * 0.2, 2)


def top_items(items, n):
    """Top n items by line total, highest first."""
    return sorted(items, key=line_total)[:n]
''' + _fillers(25))

BIG_0037_FIXED = BIG_0037.replace(
    "return sorted(items, key=line_total)[:n]",
    "return sorted(items, key=line_total, reverse=True)[:n]")

TEST_0037 = '''
import unittest

import mylib as m


class T(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(m.parse_order('A1|3|10.5'),
                         {'sku': 'A1', 'qty': 3, 'price': 10.5})

    def test_line_and_subtotal(self):
        items = [m.parse_order('A|2|30'), m.parse_order('B|1|40')]
        self.assertEqual(m.line_total(items[0]), 60)
        self.assertEqual(m.subtotal(items), 100)

    def test_tier(self):
        self.assertEqual(m.tier_discount(400), 0.0)
        self.assertEqual(m.tier_discount(500), 0.05)
        self.assertEqual(m.tier_discount(1000), 0.10)

    def test_discounts(self):
        self.assertEqual(m.apply_discounts(600, coupon_rate=0.05), 540.0)

    def test_shipping(self):
        self.assertEqual(m.shipping(50), 5.0)
        self.assertEqual(m.shipping(50, express=True), 15.0)
        self.assertEqual(m.shipping(150), 0.0)

    def test_vat(self):
        self.assertEqual(m.vat(100), 20.0)

    def test_top_items(self):
        items = [m.parse_order('A|2|30'), m.parse_order('B|1|100'),
                 m.parse_order('C|5|10')]
        top = m.top_items(items, 2)
        self.assertEqual([i['sku'] for i in top], ['B', 'A'])


if __name__ == '__main__':
    unittest.main()
'''

TEST_0046 = '''
import unittest

import mylib as m


class T(unittest.TestCase):
    def test_a(self):
        self.assertEqual(m.mystery(2), 5)

    def test_b(self):
        self.assertEqual(m.mystery(2), 6)


if __name__ == '__main__':
    unittest.main()
'''


def _t(*methods):
    """Минимальный unittest-файл: (имя_метода, тело без отступа)."""
    body = "\n\n".join(
        f"    def {name}(self):\n"
        + "".join(f"        {line}\n" for line in body.splitlines())
        for name, body in methods)
    return (f"import unittest\n\nimport mylib as m\n\n\n"
            f"class T(unittest.TestCase):\n{body}\n\n\n"
            f"if __name__ == '__main__':\n    unittest.main()\n")


# ---------- задачи ----------

TASKS = [
    dict(id="coding_0037", reward=5, instruction=STD_INSTR,
         files={"mylib.py": BIG_0037, "test_mylib.py": TEST_0037},
         patch={"mylib.py": BIG_0037_FIXED},
         fails={"test_top_items"},
         note="большой модуль: сортировка без reverse"),

    dict(id="coding_0038", reward=4, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'def add_tax(amount, rate=0.2):\n'
                 '    """Return the amount with tax added."""\n'
                 '    return amount / (1 + rate)\n'
                 '\n'
                 '\n'
                 'def total_with_tax(prices):\n'
                 '    """Total for the order with tax included."""\n'
                 '    return add_tax(sum(prices)) * 1.44\n'
             ),
             "test_mylib.py": _t(
                 ("test_add_tax", "self.assertEqual(m.add_tax(100), 120.0)"),
                 ("test_total_with_tax",
                  "self.assertEqual(m.total_with_tax([100, 50]), 180.0)"),
             ),
         },
         patch={
             "mylib.py": (
                 'def add_tax(amount, rate=0.2):\n'
                 '    """Return the amount with tax added."""\n'
                 '    return amount * (1 + rate)\n'
                 '\n'
                 '\n'
                 'def total_with_tax(prices):\n'
                 '    """Total for the order with tax included."""\n'
                 '    return add_tax(sum(prices))\n'
             ),
         },
         fails={"test_add_tax"},
         note="наивный фикс add_tax ломает total_with_tax (компенсация 1.44)"),

    dict(id="coding_0039", reward=3, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'def add_item(item, items=[]):\n'
                 '    """Add an item to the collection and return it."""\n'
                 '    items.append(item)\n'
                 '    return items\n'
             ),
             "test_mylib.py": _t(
                 ("test_add_first", "self.assertEqual(m.add_item('a'), ['a'])"),
                 ("test_add_second",
                  "self.assertEqual(m.add_item('b'), ['b'])"),
             ),
         },
         patch={
             "mylib.py": (
                 'def add_item(item, items=None):\n'
                 '    """Add an item to the collection and return it."""\n'
                 '    if items is None:\n'
                 '        items = []\n'
                 '    items.append(item)\n'
                 '    return items\n'
             ),
         },
         fails={"test_add_second"},
         note="mutable default argument"),

    dict(id="coding_0040", reward=3, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 '_count = 0\n'
                 '\n'
                 '\n'
                 'def reset():\n'
                 '    """Reset the counter."""\n'
                 '    global _count\n'
                 '    _count = 0\n'
                 '\n'
                 '\n'
                 'def tick():\n'
                 '    """Increment the counter."""\n'
                 '    global _count\n'
                 '    _count = 1\n'
                 '\n'
                 '\n'
                 'def reads():\n'
                 '    """Current counter value."""\n'
                 '    return _count\n'
             ),
             "test_mylib.py": _t(
                 ("test_ticks",
                  "m.reset()\n"
                  "m.tick()\n"
                  "m.tick()\n"
                  "self.assertEqual(m.reads(), 2)"),
             ),
         },
         patch={
             "mylib.py": (
                 '_count = 0\n'
                 '\n'
                 '\n'
                 'def reset():\n'
                 '    """Reset the counter."""\n'
                 '    global _count\n'
                 '    _count = 0\n'
                 '\n'
                 '\n'
                 'def tick():\n'
                 '    """Increment the counter."""\n'
                 '    global _count\n'
                 '    _count += 1\n'
                 '\n'
                 '\n'
                 'def reads():\n'
                 '    """Current counter value."""\n'
                 '    return _count\n'
             ),
         },
         fails={"test_ticks"},
         note="глобальное состояние: assign вместо increment"),

    dict(id="coding_0041", reward=4, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'from helpers import tax_rate\n'
                 '\n'
                 '\n'
                 'def total_with_tax(amount):\n'
                 '    """Amount with tax included, rounded to cents."""\n'
                 '    return round(amount * (1 + tax_rate()), 2)\n'
             ),
             "helpers.py": (
                 'def tax_rate():\n'
                 '    """Tax rate as a fraction."""\n'
                 '    return 0.15\n'
             ),
             "test_mylib.py": _t(
                 ("test_total_with_tax",
                  "self.assertEqual(m.total_with_tax(100), 120.0)"),
             ),
         },
         patch={
             "helpers.py": (
                 'def tax_rate():\n'
                 '    """Tax rate as a fraction."""\n'
                 '    return 0.2\n'
             ),
         },
         fails={"test_total_with_tax"},
         note="ИЗВЕСТНЫЙ ПРЕДЕЛ контракта: баг в helpers.py, а чинить "
              "велят mylib.py — полный mylib.py в ответе не чинит баг"),

    dict(id="coding_0042", reward=3, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'def set_age(age):\n'
                 '    """Set the age; must be a positive integer."""\n'
                 '    return age\n'
             ),
             "test_mylib.py": _t(
                 ("test_valid", "self.assertEqual(m.set_age(42), 42)"),
                 ("test_zero",
                  "with self.assertRaises(ValueError):\n"
                  "    m.set_age(0)"),
                 ("test_negative",
                  "with self.assertRaises(ValueError):\n"
                  "    m.set_age(-5)"),
             ),
         },
         patch={
             "mylib.py": (
                 'def set_age(age):\n'
                 '    """Set the age; must be a positive integer."""\n'
                 '    if age <= 0:\n'
                 '        raise ValueError(\'age must be positive\')\n'
                 '    return age\n'
             ),
         },
         fails={"test_zero", "test_negative"},
         note="нет валидации -> тест ждёт ValueError"),

    dict(id="coding_0043", reward=3, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'def in_band(x, lo, hi):\n'
                 '    """True if lo <= x <= hi (both ends inclusive)."""\n'
                 '    return lo <= x < hi\n'
                 '\n'
                 '\n'
                 'def band_width(lo, hi):\n'
                 '    """Number of integers x with lo <= x <= hi."""\n'
                 '    return hi - lo + 1\n'
             ),
             "test_mylib.py": _t(
                 ("test_in_band",
                  "self.assertTrue(m.in_band(3, 1, 5))\n"
                  "self.assertTrue(m.in_band(5, 1, 5))\n"
                  "self.assertFalse(m.in_band(6, 1, 5))"),
                 ("test_band_width",
                  "self.assertEqual(m.band_width(1, 5), 5)"),
             ),
         },
         patch={
             "mylib.py": (
                 'def in_band(x, lo, hi):\n'
                 '    """True if lo <= x <= hi (both ends inclusive)."""\n'
                 '    return lo <= x <= hi\n'
                 '\n'
                 '\n'
                 'def band_width(lo, hi):\n'
                 '    """Number of integers x with lo <= x <= hi."""\n'
                 '    return hi - lo + 1\n'
             ),
         },
         fails={"test_in_band"},
         note="off-by-one на границе диапазона"),

    dict(id="coding_0044", reward=5, instruction=STD_INSTR,
         files={
             "mylib.py": (
                 'def total(xs):\n'
                 '    s = 0\n'
                 '    for x in xs:\n'
                 '        s -= x\n'
                 '    return s\n'
                 '\n'
                 '\n'
                 'def maximum(xs):\n'
                 '    best = xs[0]\n'
                 '    for x in xs:\n'
                 '        if x < best:\n'
                 '            best = x\n'
                 '    return best\n'
                 '\n'
                 '\n'
                 'def count_positive(xs):\n'
                 '    return sum(1 for x in xs if x >= 0)\n'
                 '\n'
                 '\n'
                 'def average(xs):\n'
                 '    return sum(xs) / (len(xs) + 1)\n'
                 '\n'
                 '\n'
                 'def first_char(s):\n'
                 '    return s[-1]\n'
                 '\n'
                 '\n'
                 'def reverse_str(s):\n'
                 '    return s[::1]\n'
             ),
             "test_mylib.py": _t(
                 ("test_total", "self.assertEqual(m.total([1, 2, 3]), 6)"),
                 ("test_maximum",
                  "self.assertEqual(m.maximum([3, 9, 2]), 9)"),
                 ("test_count_positive",
                  "self.assertEqual(m.count_positive([1, -2, 0, 3]), 2)"),
                 ("test_average", "self.assertEqual(m.average([2, 4, 6]), 4)"),
                 ("test_first_char",
                  "self.assertEqual(m.first_char('hello'), 'h')"),
                 ("test_reverse_str",
                  "self.assertEqual(m.reverse_str('abc'), 'cba')"),
             ),
         },
         patch={
             "mylib.py": (
                 'def total(xs):\n'
                 '    s = 0\n'
                 '    for x in xs:\n'
                 '        s += x\n'
                 '    return s\n'
                 '\n'
                 '\n'
                 'def maximum(xs):\n'
                 '    best = xs[0]\n'
                 '    for x in xs:\n'
                 '        if x > best:\n'
                 '            best = x\n'
                 '    return best\n'
                 '\n'
                 '\n'
                 'def count_positive(xs):\n'
                 '    return sum(1 for x in xs if x > 0)\n'
                 '\n'
                 '\n'
                 'def average(xs):\n'
                 '    return sum(xs) / len(xs)\n'
                 '\n'
                 '\n'
                 'def first_char(s):\n'
                 '    return s[0]\n'
                 '\n'
                 '\n'
                 'def reverse_str(s):\n'
                 '    return s[::-1]\n'
             ),
         },
         fails={"test_average", "test_count_positive", "test_first_char",
                "test_maximum", "test_reverse_str", "test_total"},
         note="6 падающих тестов, 6 разных багов"),

    dict(id="coding_0045", reward=2,
         instruction=(RUN.replace("; some tests fail.", ".")
                     + FIX + TAIL),
         files={
             "mylib.py": (
                 'def is_even(n):\n'
                 '    return n % 2 == 0\n'
                 '\n'
                 '\n'
                 'def sum_digits(n):\n'
                 '    n = abs(n)\n'
                 '    total = 0\n'
                 '    while n:\n'
                 '        total += n % 10\n'
                 '        n //= 10\n'
                 '    return total\n'
             ),
             "test_mylib.py": _t(
                 ("test_is_even",
                  "self.assertTrue(m.is_even(4))\n"
                  "self.assertFalse(m.is_even(7))"),
                 ("test_sum_digits",
                  "self.assertEqual(m.sum_digits(123), 6)\n"
                  "self.assertEqual(m.sum_digits(-9), 9)"),
             ),
         },
         patch=None,
         fails=set(),
         note="fast-path: тесты уже зелёные, 0 токенов"),

    dict(id="coding_0046", reward=4, instruction=STD_INSTR,
         files={
             "mylib.py": 'def mystery(x):\n    return x\n',
             "test_mylib.py": TEST_0046,
         },
         patch=None,                      # противоречие: фикса не существует
         fails={"test_a", "test_b"},
         note="противоречивые тесты -> маркер UNSOLVABLE"),

    dict(id="coding_0047", reward=3,
         instruction=("Fix the bugs in mylib.py until all tests pass. "
                      + TAIL),
         files={
             "mylib.py": 'def double(x):\n    return x * 3\n',
         },
         patch=None, precheck_error=True,
         fails=None,
         note="нет команды тестов и нет test_*.py -> precheck cut"),

    dict(id="coding_0048", reward=3,
         instruction=("Run `python3 test_mylib.py` in the current "
                      "directory; the tests are wrong. Fix the bugs in "
                      "test_mylib.py (do not edit mylib.py) until all "
                      "tests pass. " + TAIL),
         files={
             "mylib.py": 'def add(a, b):\n    return a + b\n',
             "test_mylib.py": (
                 'import unittest\n\nimport mylib as m\n\n\n'
                 'class T(unittest.TestCase):\n'
                 '    def test_add(self):\n'
                 '        self.assertEqual(m.add(2, 2), 5)\n\n\n'
                 'if __name__ == \'__main__\':\n    unittest.main()\n'
             ),
         },
         patch=None, precheck_error=True,
         fails=None,
         note="инструкция велит чинить тесты -> precheck cut"),
]

def _suite(ws: Path, task_id: str):
    proc, err = run_tests(["python3", "test_mylib.py"], ws, 30)
    if err:
        raise RuntimeError(f"{task_id}: прогон сломался: {err}")
    return proc.returncode, set(parse_failures(proc.stderr))


def verify(t: dict) -> None:
    """Исходник падает ровно задуманными тестами; эталонный фикс зелёный."""
    d = Path(tempfile.mkdtemp())
    ws = d / "workspace"
    ws.mkdir(parents=True)
    try:
        for name, text in t["files"].items():
            (ws / name).write_text(text, encoding="utf-8")

        if t.get("precheck_error"):
            _, _, _, err = inspect_task(t["instruction"], ws)
            assert err, f"{t['id']}: ждали precheck-ошибку, получли None"
            print(f"  {t['id']}: precheck cut: {err}")
            return

        rc, fails = _suite(ws, t["id"])
        assert fails == set(t["fails"]), \
            f"{t['id']}: падают {fails}, задуманы {set(t['fails'])}"

        if t.get("patch"):
            for name, text in t["patch"].items():
                (ws / name).write_text(text, encoding="utf-8")
            rc2, _ = _suite(ws, t["id"])
            assert rc2 == 0, \
                f"{t['id']}: эталонный фикс не проходит тесты (rc={rc2})"
        else:
            assert rc == 0 or t["id"] == "coding_0046", \
                f"{t['id']}: без фикса тесты зелёные, а падать должны {t['fails']}"
        size = len(t["files"].get("mylib.py", ""))
        print(f"  {t['id']}: ok, падают {sorted(t['fails']) or 'ничего'}, "
              f"mylib {size} chars")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def write_task(t: dict) -> None:
    d = BASE / t["id"]
    (d / "workspace").mkdir(parents=True, exist_ok=True)
    for name, text in t["files"].items():
        (d / "workspace" / name).write_text(text, encoding="utf-8")
    payload = {
        "task_id": t["id"],
        "task_type": "coding",
        "instruction": t["instruction"],
        "reward": t["reward"],
        "check": "unittest",
    }
    if not t.get("precheck_error") and t["id"] != "coding_0046":
        payload["expected"] = "tests_pass"
    (d / "task.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")
    print(f"{t['id']}: создана ({t['note']})")


def main() -> None:
    print("Проверка целостности ловушек:")
    for t in TASKS:
        verify(t)
    print()
    for t in TASKS:
        write_task(t)


if __name__ == "__main__":
    main()
