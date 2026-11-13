# -*- coding: utf-8 -*-
"""Тесты solutions/planning.py — извлечение JSON из ответов LLM. Без сети.

Запуск из корня проекта:

    .venv/bin/python -m unittest solutions.tests.test_planning -v
"""

import unittest

from solutions.planning import extract_json


class ExtractJsonTest(unittest.TestCase):
    def test_bare_object(self):
        self.assertEqual(extract_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self):
        text = 'Here is the plan:\n```json\n{"steps": []}\n```\nthanks'
        self.assertEqual(extract_json(text), {"steps": []})

    def test_fenced_plain(self):
        self.assertEqual(extract_json('```\n{"unanswerable": true}\n```'),
                         {"unanswerable": True})

    def test_second_fence_used(self):
        text = "```json\nnot json at all\n```\n```json\n{\"ok\": 1}\n```"
        self.assertEqual(extract_json(text), {"ok": 1})

    def test_surrounding_text(self):
        self.assertEqual(extract_json('The answer is {"a": {"b": 2}} hope it helps'),
                         {"a": {"b": 2}})

    def test_trailing_garbage(self):
        self.assertEqual(extract_json('{"a": 1} trailing words'), {"a": 1})

    def test_braces_inside_strings(self):
        # сбалансированный скан не должен обманываться скобкой внутри строки
        self.assertEqual(extract_json('{"note": "literal } brace"}'),
                         {"note": "literal } brace"})

    def test_escaped_quote(self):
        self.assertEqual(extract_json('x {"s": "a\\"b"} y'), {"s": 'a"b'})

    def test_array_is_not_a_plan(self):
        self.assertIsNone(extract_json('[1, 2, 3]'))

    def test_scalar_is_not_a_plan(self):
        self.assertIsNone(extract_json('"just a string"'))

    def test_garbage_is_none(self):
        self.assertIsNone(extract_json("no json here at all"))

    def test_empty(self):
        self.assertIsNone(extract_json(""))


if __name__ == "__main__":
    unittest.main()
