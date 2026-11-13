# -*- coding: utf-8 -*-
"""Исполнение python-программ от LLM: общий раннер код-планировщиков.

Потребители — солверы, которые просят модель написать программу и
исполняют её локально: math/v2 и data_analysis/v2. Здесь только
механика (извлечение кода из ответа, сабпроцесс, чистка stdout), никакой
политики задач: что считать ответом — решает солвер и его `check`.
"""

from __future__ import annotations

import ast
import re
import subprocess

from solutions.task_parser import CHECK_EXACT_INT

_FENCE_RE = re.compile(r"```[a-zA-Z0-9_]*\s*(.*?)```", re.DOTALL)
# число с необязательной дробной частью: «270.0» — ОДНО число, а не «270» и «0»
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_SHELL_RE = re.compile(
    r"^\s*(?:!\s*)?(?:sudo\s+)?(?:bash\s+|sh\s+)?python3?\s+(?:-c\s+)?(.+)$",
    re.DOTALL | re.IGNORECASE)


class Proc:
    """Результат исполнения программы: успех, stdout, текст ошибки."""

    __slots__ = ("ok", "stdout", "error")

    def __init__(self, ok: bool, stdout: str, error: str | None):
        self.ok = ok
        self.stdout = stdout
        self.error = error


def extract_code(text: str) -> str | None:
    """Код из ответа LLM: самый длинный fenced-блок -> весь текст с print(.

    Перед возвратом снимаем bash-обёртку: модели (особенно следуя
    инструкции задачи «compute with python3 -c») иногда оборачивают
    программу в `python3 -c "..."` — исполняем содержимое, не команду.
    """
    fences = _FENCE_RE.findall(text)
    if fences:
        code = max(fences, key=len).strip()
    elif "print(" in text:
        code = text.strip()
    else:
        return None
    code = unwrap_shell(code)
    return code or None


def unwrap_shell(code: str) -> str:
    """Снять `python3 -c "..."` / bash-обёртку, вернув сам код."""
    m = _SHELL_RE.match(code)
    if not m:
        return code
    arg = m.group(1).strip()
    try:
        value = ast.literal_eval(arg)
        if isinstance(value, str):
            return value
    except (ValueError, SyntaxError):
        pass
    # вручную снять парные кавычки (в т.ч. с экранированием внутри)
    for quote in ('"""', "'''", '"', "'", "`"):
        if len(arg) >= 2 * len(quote) and arg.startswith(quote) \
                and arg.endswith(quote):
            return arg[len(quote):-len(quote)]
    return code


def run_python(code: str, timeout: float, cwd: str | None = None) -> Proc:
    """Исполнить программу в изолированном сабпроцессе с таймаутом.

    cwd — рабочий каталог программы (data_analysis: workspace задачи,
    чтобы код мог открыть свой CSV по относительному имени).
    """
    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True,
            timeout=timeout, check=False, cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return Proc(False, "", "timeout")
    except OSError as e:                     # python3 не найден и т.п.
        return Proc(False, "", f"cannot run python3: {e}")
    if proc.returncode != 0:
        return Proc(False, proc.stdout,
                    proc.stderr.strip()[-500:] or "nonzero exit")
    return Proc(True, proc.stdout, None)


def clean_output_answer(stdout: str, check: str | None) -> str | None:
    """Ответ из stdout программы.

    Последний непустой print (модели любят отладочные принты, последний —
    итоговый). Для exact_int — последнее число в этой строке; целое в
    float-записи («270.0») — это 270, а не «270» и «0» (баг пойман
    adversarial-пакетом: ответ '0' вместо '270'). Нецелый float («136.5»)
    не округляем — правилами задачи распоряжается модель, честный провал
    лучше мусора.
    """
    lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
    if not lines:
        return None
    value = lines[-1]
    if check == CHECK_EXACT_INT:
        nums = _NUM_RE.findall(value.replace(",", "").replace(" ", ""))
        if not nums:
            return None
        token = nums[-1]
        if "." in token:
            number = float(token)
            return str(int(number)) if number.is_integer() else str(number)
        return str(int(token))
    for _ in range(3):                       # фенсы/кавычки/точка — как в v1
        value = value.strip("`\"' \t")
        if value.endswith("."):
            value = value[:-1].rstrip()
    return value or None
