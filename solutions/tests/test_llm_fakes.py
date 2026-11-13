# -*- coding: utf-8 -*-
"""Фейки HTTP-транспорта для тестов solutions/llm.py — без сети."""

import copy
import json


class FakeResponse:
    """Похоже на requests.Response настолько, насколько нужно клиенту."""

    def __init__(self, status_code=200, data=None, text=""):
        self.status_code = status_code
        self._data = data
        if text:
            self.text = text
        elif data is not None:
            self.text = json.dumps(data)
        else:
            self.text = ""

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


class FakeSession:
    """Снимает вызовы post(); выдаёт ответы/исключения по очереди.

    Payload хранится глубокой копией — как в реальном HTTP, где тело
    сериализуется в момент отправки и не меняется при позднейших правках
    словаря у вызывающего.
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": copy.deepcopy(json),
                           "headers": headers, "timeout": timeout})
        if not self.responses:
            raise AssertionError("FakeSession: unexpected extra request")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
