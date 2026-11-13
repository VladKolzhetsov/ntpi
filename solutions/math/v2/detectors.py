# -*- coding: utf-8 -*-
"""Детерминированные фильтры нерешаемости math-задач (v2).

Каждая функция — отдельный фильтр: `(instruction) -> reason | None`.
Строка reason = задача детерминированно нерешаема (и почему);
None = фильтр пройден.

`solve()` запускает ВСЕ фильтры из DETECTORS ДО вызова LLM: любой
провал -> status=unanswerable, answer.txt не создаётся, токены не
тратятся. Отключение фильтра = убрать его из DETECTORS (или передать
свой список в detect_unsolvable) — так оценка «живости» фильтра по
реальным данным не требует правок солвера.

Фильтры сознательно консервативны (лучше пропустить ловушку, чем
зарубить решаемую задачу): покрываем только классы, где детерминизм
надёжен. Общий случай (пропущенный операнд, противоречивость данных)
ловит маркер UNSOLVABLE от LLM — см. solver.py; рантайм-отказы — тоже.
"""

from __future__ import annotations

from solutions.text import stems_match, stem, words

# вопросные/служебные слова: их отсутствие в условии ничего не значит
_QUESTION_STOPWORDS = frozenset("""
    how many much what when which who whose where why is are was were do
    does did can could should would will shall may might must have has had
    the a an of in on at to for from with by as and or if then it its his
    her their our your my this that these those there here now all each
    every some any no not none both few more less most least other another
    such same own so than too very just only also
    end final total result answer value number amount left remaining
    remain remains together combined altogether difference sum product
    ratio percent percentage initially eventually finally afterwards
""".split())


def _question_bounds(instruction: str) -> tuple[str, int, int]:
    """Сам вопрос задачи и его границы: (question, start, end).

    '...1752 boxes. How many crates are there? Compute it with bash...'
    -> ('How many crates are there?', start, end)
    """
    end = instruction.rfind("?")
    if end == -1:
        return "", len(instruction), len(instruction)
    start = max(instruction.rfind(".", 0, end),
                instruction.rfind("!", 0, end),
                instruction.rfind("?", 0, end)) + 1
    while start < end and instruction[start].isspace():   # без ведущего пробела
        start += 1
    return instruction[start:end + 1], start, end + 1


def question_noun_absent(instruction: str) -> str | None:
    """Фильтр «подмена сущности»: в вопросе нет НИ ОДНОГО содержательного
    слова из условия.

    Ловит генераторную ловушку «условие про boxes, вопрос про crates».
    Консервативен трижды:
    - служебные слова (how/end/total/...) не считаются сущностями;
    - если хоть одно содержательное слово вопроса есть в условии,
      фильтр пропускает (ловушка с приманкой уходит маркеру UNSOLVABLE);
    - question-only задачи (вопрос = первая же фраза, условия до него
      нет) пропускает целиком: подменять сущность «не из чего», а
      самодостаточные вопросы («How many 5-card hands...») решаемы —
      ловит маркер (баг пойман на math_0015/0016/0038).
    """
    question, start, end = _question_bounds(instruction)
    if not question or start == 0:
        return None                          # вопроса нет или он самодостаточен
    q_nouns = [w for w in words(question)
               if len(w) > 3 and w not in _QUESTION_STOPWORDS]
    if not q_nouns:
        return None                          # нет явных сущностей — пропускаем
    premise = instruction[:start] + instruction[end:]
    premise_stems = [stem(w) for w in words(premise)]
    missing = [noun for noun in q_nouns
               if not any(stems_match(stem(noun), ps)
                          for ps in premise_stems)]
    if len(missing) == len(q_nouns):
        return ("question asks about " + ", ".join(q_nouns) +
                " which never appear in the problem statement")
    return None


DETECTORS = [
    question_noun_absent,
]


def detect_unsolvable(instruction: str, detectors=DETECTORS) -> str | None:
    """Прогнать все фильтры по порядку.

    -> reason (нерешаема) | None (детерминированных возражений нет).
    """
    for detect in detectors:
        reason = detect(instruction)
        if reason:
            return reason
    return None
