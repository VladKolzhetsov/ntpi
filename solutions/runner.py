# -*- coding: utf-8 -*-
"""Диспетчер задач соревнования: типы -> солверы, бюджет, триаж.

Роли разделены так, чтобы решение «тратить или нет» принималось до
первого вызова LLM (0 токенов):

- СОРТИРОВКА ПО ПЛОТНОСТИ. Баллы (reward) в регламенте случайны
  относительно сложности, поэтому «жадный рюкзак» приближаем отношением
  баллов к ожидаемой стоимости типа: priority = reward / EXPECTED_COST.
  Если бюджет кончится, потеряем наименее выгодные задачи, а не случайные.

- PER-TASK ENVELOPE. Каждой задаче выдаётся кап расхода («allowance») =
  EXPECTED_COST[type] * CAP_FACTOR, ограниченный остатком бюджета. Солвер
  получает его как бюджет СВОЕГО BudgetTracker (ScopedBudget): при
  превышении LLMClient сам отказывает в вызове (LLMBudgetError), и солвер
  аккуратно сворачивается в failed, вместо того чтобы выжечь весь общий
  бюджет одной задачей (риск реален у coding-циклов и у fallback-QA).
  Оговорка: reward НЕ расширяет envelope (reward случаен относительно
  стоимости); на «стоит ли» работает порядок сортировки.

- PER-TASK COST_WORST (RunConfig.worst_case). Солвер может опубликовать
  cost_worst(task) -> float: детерминированную верхнюю границу расхода на
  задачу, вычислимую до первого вызова (0 токенов). Тогда envelope =
  cost_worst (CAP_FACTOR не нужен: граница уже покрывает всю лестницу
  солвера), а плотность = reward / (профиль × worst / median(worst типа)).
  Нормировка обязательна: cost_worst — ГРАНИЦА (план+ремонт+QA), профиль —
  ОЖИДАНИЕ, в одной плотности они несопоставимы (сырая граница search ~595
  против ожиданий 80/260/460 задвигала бы search в хвост: при B=5000
  симуляция давала 0 решённых search против 8). Монотонная нормировка
  сохраняет порядок внутри типа (эффект A/B 2026-11-12: +6.7%) и делает
  значения сопоставимыми между типами. Медиана берётся по батчу прогона.
  Флаг per-type: True — все типы с хуком, {типы} — перечисленные,
  False/пустое — выключен. Публикуют: search/v2, math/v2,
  data_analysis/v2, coding/v2 (complexity.py).

- РАННИЕ ОТКАЗЫ. Детекторы нерешаемости всех солверов (noun-guard,
  precheck data_analysis/coding, UNSOLVABLE-маркер) срабатывают до LLM
  (cost = 0) — бюджет на них не тратится.

- ДЕФИЦИТ БЮДЖЕТА. Перед задачей проверяется остаток: меньше
  MIN_VIABLE[type] (минимум, при котором задача имеет шанс) — задача
  помечается skipped, answer.txt не создаётся. Так как список уже
  отсортирован по плотности, «хвост» отпадает первым.

- ДЕДЛАЙН. Отдельно от бюджета: после --time-limit остаток набора
  помечается skipped (регламент — ~1 час на прогон).

Прогон не падает целиком: исключение в солвере ловится, задача помечается
failed, идём дальше; результат пишется после каждой задачи (JSONL) — как
требует регламент («решение сохранять после каждой задачи»).
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from solutions.base import ANSWERED, FAILED, UNANSWERABLE, SolveResult
from solutions.llm import BudgetTracker, LLMClient, Usage
from solutions.task_parser import (
    CHECK_EXACT_INT, CHECK_UNITTEST, TASK_CODING, TASK_DATA_ANALYSIS,
    TASK_MATH, TASK_SEARCH, Task,
)

_INT_RE = re.compile(r"-?\d+")

# ---------- Статусы (плюс SKIPPED к общим из base) ----------
SKIPPED = "skipped"          # задача не запускалась: бюджет/дедлайн/тип

# ---------- Профиль стоимости по типам (live-медианы, output-экв.) ----------
# Источники: search ~114-141, math/v2 47-79, data_analysis/v2 188-408,
# coding/v2 266-514 (медианы 477/272). Ожидаемая стоимость нужна только
# для сортировки и решений о дефиците — точность ±30% здесь достаточна.
EXPECTED_COST: dict[str, float] = {
    TASK_SEARCH: 140.0,
    TASK_MATH: 80.0,
    TASK_DATA_ANALYSIS: 260.0,
    TASK_CODING: 460.0,
}
DEFAULT_EXPECTED_COST = 260.0

# Минимум, при котором задача вообще имеет шанс (один вызов LLM + запас).
MIN_VIABLE: dict[str, float] = {
    TASK_SEARCH: 110.0,        # планировщик (~115) или fallback-QA
    TASK_MATH: 75.0,           # одна трансляция в код (факт: медиана 80)
    TASK_DATA_ANALYSIS: 90.0,  # profile мотивирует более длинный код
    TASK_CODING: 140.0,        # один вызов + полный модуль в ответе
}
DEFAULT_MIN_VIABLE = 90.0

CAP_FACTOR = 3.0               # envelope = EXPECTED_COST * CAP_FACTOR

# Эксперимент с мягким гейтом на ремонт (2026-11-12) ОТКАЧЕН: гейт не
# предотвращал трату (первый вызов успевал сжечь кап), а добивал обычно
# решаемые задачи (math_0010 -> FAIL за 446 экв.). Защита от болтливости
# живёт в солвере math/v2: при усечении ответа без кода ремонт идёт с
# меньшим капом и требованием «только код». Фактор оставлен для справки —
# включать осознанно, поверх ScopedBudget(soft_limit=...).
SOFT_GATE_FACTOR = 3.0

# ---------- Версии солверов по умолчанию ----------
DEFAULT_VERSIONS: dict[str, str | None] = {
    # v2 с 2026-11-13: та же точность, что v1, −15% расход, решает
    # search_0034 (14 хопов: v1 с капом 250 не влезает) и публикует
    # cost_worst для worst-case триажа. v1 остаётся доступной:
    # --versions search=v1
    TASK_SEARCH: "v2",          # kb + planner + executor + solver + cost_worst
    TASK_MATH: "v2",            # код-планировщик
    TASK_DATA_ANALYSIS: "v2",   # код-планировщик + precheck
    TASK_CODING: "v2",          # прегон тестов + ремонтный цикл
}

_SOLVER_CACHE: dict[tuple[str, str | None], Callable[..., SolveResult]] = {}


# ---------- Итоги ----------

@dataclass
class TaskOutcome:
    """Итог одной задачи в прогоне."""

    task_id: str
    task_type: str
    reward: float
    status: str                        # answered | unanswerable | failed | skipped
    answer: str | None = None
    cost: float = 0.0
    verified: bool | None = None       # локальная проверка по expected (None = неизвестно)
    seconds: float = 0.0
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "task_type": self.task_type,
            "reward": self.reward, "status": self.status, "answer": self.answer,
            "cost": round(self.cost, 1), "verified": self.verified,
            "seconds": round(self.seconds, 1), "trace": self.trace,
        }


@dataclass
class RunReport:
    """Сводка прогона: для лога, JSON-отчёта и тестов."""

    outcomes: list[TaskOutcome] = field(default_factory=list)
    budget: float | None = None
    spent: float = 0.0
    calls: int = 0
    failed_calls: int = 0
    seconds: float = 0.0
    workers: int = 1
    model: str = ""

    @property
    def answered(self) -> int:
        return sum(1 for o in self.outcomes if o.status == ANSWERED)

    @property
    def unanswerable(self) -> int:
        return sum(1 for o in self.outcomes if o.status == UNANSWERABLE)

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == FAILED)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.status == SKIPPED)

    @property
    def reward_total(self) -> float:
        """Все баллы набора (верхняя граница)."""
        return sum(o.reward for o in self.outcomes)

    @property
    def reward_earned(self) -> float:
        """Баллы, подтверждённые локальной проверкой (скрытый набор -> 0)."""
        return sum(o.reward for o in self.outcomes if o.verified is True)

    @property
    def reward_answered(self) -> float:
        """Баллы задач, на которые мы записали ответ (не подтверждённые)."""
        return sum(o.reward for o in self.outcomes if o.status == ANSWERED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget, "spent": round(self.spent, 1),
            "calls": self.calls, "failed_calls": self.failed_calls,
            "seconds": round(self.seconds, 1), "workers": self.workers,
            "model": self.model,
            "counts": {
                "answered": self.answered, "unanswerable": self.unanswerable,
                "failed": self.failed, "skipped": self.skipped,
                "total": len(self.outcomes),
            },
            "reward": {
                "total": self.reward_total,
                "answered": self.reward_answered,
                "earned": self.reward_earned,
            },
            "tasks": [o.to_dict() for o in self.outcomes],
        }


# ---------- Бюджет задачи ----------

class ScopedBudget(BudgetTracker):
    """Бюджет ОДНОЙ задачи: свой кап + зеркалирование в глобальный трекер.

    Солвер считает стоимость как дельту на своём tracker'е (см. solve()),
    поэтому подмена client.tracker на этот объект даёт одновременно и
    per-task кап (LLMBudgetError по достижении), и честный глобальный учёт.

    soft_limit — резерв под механизм «не углубляться»; сейчас НЕ
    используется раннером: эксперимент показал, что гейт на ремонт не
    предотвращает трату (ущерб наносит первый вызов, сжигающий кап),
    а лишь добивает задачи, которые обычно решаются (math_0010 ->
    FAIL за 446 экв.). Защита от болтливости перенесена в солвер math/v2
    (усечение + требование «только код»). Поле оставлено, чтобы механизм
    можно было включить осознанно и отдельно.
    """

    def __init__(self, limit: float | None, mirror: BudgetTracker,
                 soft_limit: float | None = None):
        super().__init__(budget=limit)
        self._mirror = mirror
        self._soft_limit = float(soft_limit) if soft_limit is not None else None
        self.soft_hits = 0                  # сколько раз упёрлись в резерв

    @property
    def soft_limit(self) -> float | None:
        return self._soft_limit

    def soft_exceeded(self) -> bool:
        """Задача уже потратила больше ожидаемого уровня (если резерв задан)."""
        return self._soft_limit is not None and self.spent >= self._soft_limit

    def can_spend(self) -> bool:
        """Резерв (если включён) ИЛИ жёсткий кап запрещают новый вызов."""
        if self.soft_exceeded():
            self.soft_hits += 1
            return False
        return super().can_spend()

    def record(self, usage: Usage) -> None:
        super().record(usage)
        self._mirror.record(usage)

    def note_failure(self) -> None:
        super().note_failure()
        self._mirror.note_failure()


# ---------- Триаж ----------

def expected_cost(task_type: str) -> float:
    return EXPECTED_COST.get(task_type, DEFAULT_EXPECTED_COST)


def min_viable(task_type: str) -> float:
    return MIN_VIABLE.get(task_type, DEFAULT_MIN_VIABLE)


def worst_enabled(worst_case: "bool | set[str] | frozenset[str]",
                  task_type: str) -> bool:
    """Включён ли per-task cost_worst для типа задачи.

    False или пустое множество — выключен везде; True — все типы
    (у солвера может не быть хука — тогда типовой профиль); множество —
    только перечисленные типы.
    """
    if not worst_case:
        return False
    if worst_case is True:
        return True
    return task_type in worst_case


def parse_worst_case(raw: str) -> "bool | frozenset[str]":
    """Строка CLI -> значение RunConfig.worst_case.

    'search,math' -> {'search','math'}; 'all' -> True; 'none'/'' -> False.
    Неизвестный тип задачи — ValueError (опечатка в флаге).
    """
    raw = (raw or "").strip().casefold()
    if not raw or raw in ("none", "off", "no"):
        return False
    if raw in ("all", "*"):
        return True
    types = frozenset(t.strip() for t in raw.split(",") if t.strip())
    unknown = types - set(DEFAULT_VERSIONS)
    if unknown:
        raise ValueError(
            "неизвестные типы задач: " + ", ".join(sorted(unknown)))
    return types


def fmt_worst(worst: "bool | set[str] | frozenset[str]") -> str:
    """Печать режима worst-case в заголовке прогона."""
    if not worst:
        return "нет"
    if worst is True:
        return "все типы"
    return ",".join(sorted(worst))


def priority(task: Task, cost: float | None = None) -> float:
    """Плотность «баллы на output-эквивалент». reward 0 -> 0 (в конец).

    cost: per-task оценка для ПОРЯДКА (нормированный cost_worst солвера);
    иначе типовой EXPECTED_COST.
    """
    if cost is None:
        cost = expected_cost(task.task_type)
    return float(task.reward) / cost


def order_tasks(tasks: list[Task],
                costs: dict[str, float | None] | None = None) -> list[Task]:
    """Порядок обхода: плотность убыв., при равенстве — дешевле первым.

    costs: per-task ГРАНИЦЫ стоимости (cost_worst солверов, сырые) по
    task_id. Для порядка они нормируются на профиль типа (см.
    _order_costs): граница — не ожидание, сырые значения несопоставимы
    между типами. Задачи без границы сортируются по типовому профилю.
    0 токенов, вызывается до первого LLM-запроса.
    """
    order_costs = _order_costs(tasks, costs or {})
    return sorted(tasks, key=lambda t: (-priority(t, order_costs.get(t.task_id)),
                                        order_costs.get(t.task_id)
                                        or expected_cost(t.task_type),
                                        t.task_id))


def _order_costs(tasks: list[Task],
                 costs: dict[str, float | None]) -> dict[str, float]:
    """Стоимость для ПОРЯДКА: cost_worst, нормированный на профиль типа.

    cost_worst — ГРАНИЦА полной лестницы (план+ремонт+QA, search ~595),
    EXPECTED_COST — ОЖИДАНИЕ (80/260/460). Делить reward на сырую границу
    в смешанном триаже нельзя: search уезжает в хвост (при B=5000
    симуляция 2026-11-13 давала 0 решённых search против 8 у профилей).
    Монотонная нормировка profile × worst / median(worst типа):
    - порядок ВНУТРИ типа не меняется (тот же, что в однотипном A/B
      2026-11-12: +6.7% эффективности);
    - значения сопоставимы МЕЖДУ типами (типовой search ≈ профиль,
      дорогой — кратно дороже);
    - медиана по батчу прогона — самоадаптация к челленджному набору.
    Границы <= 0 не нормируем (не бывает, но защита от деления).
    """
    per_type: dict[str, list[float]] = {}
    for task in tasks:
        value = costs.get(task.task_id)
        if value is not None:
            per_type.setdefault(task.task_type, []).append(value)
    medians = {tt: statistics.median(values)
               for tt, values in per_type.items()}
    out: dict[str, float] = {}
    for task in tasks:
        value = costs.get(task.task_id)
        if value is None:
            continue
        median = medians.get(task.task_type)
        if median and median > 0:
            value = expected_cost(task.task_type) * value / median
        out[task.task_id] = float(value)
    return out


def allowance_for(task_type: str, remaining: float | None,
                   worst: float | None = None) -> float | None:
    """Envelope задачи, не больше остатка.

    worst: детерминированная per-task граница (cost_worst солвера) —
    CAP_FACTOR не нужен: граница уже покрывает всю лестницу солвера.
    remaining is None -> бюджет не задан (локальная разработка) -> без капа.
    """
    if remaining is None:
        return None
    envelope = (worst if worst is not None
                else expected_cost(task_type) * CAP_FACTOR)
    return max(min_viable(task_type), min(remaining, envelope))


def cost_worst_for(task: Task, config: "RunConfig") -> float | None:
    """Per-task граница стоимости, если солвер её публикует (0 токенов).

    Контракт: модуль солвера может экспортировать cost_worst(task) -> float —
    детерминированную верхнюю границу расхода на задачу, вычислимую до
    первого LLM-вызова. Per-type: тип должен быть включён в
    RunConfig.worst_case (True = все, множество = перечисленные).
    Любая ошибка или отсутствие хука -> None -> типовой профиль
    EXPECTED_COST. Граница используется для envelope как есть, а для
    порядка — нормированной (_order_costs).
    """
    if not worst_enabled(config.worst_case, task.task_type):
        return None
    version = config.versions.get(task.task_type,
                                  DEFAULT_VERSIONS.get(task.task_type))
    if not version:
        return None
    try:
        module = importlib.import_module(_module_for(task.task_type, version))
        fn = getattr(module, "cost_worst", None)
        value = fn(task) if fn is not None else None
    except Exception:                                # noqa: BLE001 — оценка не роняет прогон
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def task_costs(tasks: list[Task],
               config: "RunConfig") -> dict[str, float]:
    """cost_worst всех задач по контракту солверов (0 токенов).

    Пер-типовой: учитываются только типы из RunConfig.worst_case
    (True = все). Задачи без границы (тип выключен/нет хука/ошибка)
    в словарь не попадают: отсутствие ключа = типовой профиль.
    """
    if not config.worst_case:
        return {}
    out: dict[str, float] = {}
    for task in tasks:
        value = cost_worst_for(task, config)
        if value is not None:
            out[task.task_id] = value
    return out


def resolve_solver(task_type: str, version: str | None,
                   ) -> Callable[..., SolveResult]:
    """Найти solve() нужной версии солвера (ленивый import + кеш).

    По соглашению проекта: solutions/<type>/<version>/solver.py::solve;
    у search версий нет — solutions/search/solver.py::solve.
    """
    key = (task_type, version)
    if key not in _SOLVER_CACHE:
        module = _module_for(task_type, version)
        _SOLVER_CACHE[key] = getattr(
            importlib.import_module(module), "solve")
    return _SOLVER_CACHE[key]


def _module_for(task_type: str, version: str | None) -> str:
    """Модуль солвера: solutions/<type>/<version>/solver.py (унифицировано)."""
    if task_type not in DEFAULT_VERSIONS:
        raise KeyError(f"неизвестный тип задачи: {task_type!r}")
    if not version:
        raise KeyError(f"для типа {task_type!r} версия не задана")
    return f"solutions.{task_type}.{version}.solver"


def verify(task: Task, result: SolveResult) -> bool | None:
    """Локальная проверка ответа по task.json: True/False или None.

    None — проверить нечем (скрытый набор без expected). На поведение не
    влияет: только диагностика прогона и подсчёт баллов.

    Факт записи файла проверяет раннер (ANSWERED без written -> FAILED):
    здесь только сравнение значения, поэтому функция работает и в
    --sandbox, и с заглушками в тестах.
    """
    if result.status != ANSWERED:
        return False
    if task.check == CHECK_UNITTEST:
        # exit 0 полного suite = тот же критерий, что у скрытой проверки
        return True
    expected = task.expected
    if expected is None:
        return None
    answer = (result.answer or "").strip()
    if task.check == CHECK_EXACT_INT:
        compact = answer.replace(" ", "").replace(",", "")
        m = _INT_RE.search(compact)
        if not m:
            return False
        try:
            return int(m.group()) == int(expected)
        except (TypeError, ValueError):
            return False
    return answer == str(expected).strip()


# ---------- Конфигурация прогона ----------

@dataclass
class RunConfig:
    model: str
    budget: float | None = None
    versions: dict[str, str | None] = field(default_factory=dict)
    models: dict[str, str] = field(default_factory=dict)   # модель по типу (A/B)
    solvers: dict[str, Callable[..., SolveResult]] = field(default_factory=dict)
    workers: int = 1
    deadline: float | None = None      # time.time()-метка
    sandbox_dir: Path | None = None    # копировать задачи сюда (тренировка)
    out_dir: Path | None = None
    verbose: bool = False              # печатать trace решения
    # Per-task cost_worst: True — все типы с хуком; {типы} — только они
    # (run_agent.py по умолчанию передаёт {'search'}); False/пусто — выключен.
    worst_case: bool | set[str] | frozenset[str] = False
    on_task: Callable[[LLMClient, Task], None] | None = None
    log: Callable[[str], None] = print

    def solver_for(self, task_type: str) -> Callable[..., SolveResult]:
        if task_type in self.solvers:
            return self.solvers[task_type]
        version = self.versions.get(task_type, DEFAULT_VERSIONS.get(task_type))
        return resolve_solver(task_type, version)

    def model_for(self, task_type: str) -> str:
        """Модель задачи: per-type переопределение или общая."""
        return self.models.get(task_type, self.model)


# ---------- Прогон ----------

def run(tasks: list[Task], make_client: Callable[[], LLMClient],
        config: RunConfig, dry_run: bool = False) -> RunReport:
    """Решить набор задач в порядке плотности, соблюдая бюджет и дедлайн.

    make_client — фабрика клиента: свой клиент на каждого воркера (у клиента
    мутируется tracker под задачу, поэтому делить клиент между потоками
    нельзя). Клиенты закрываются в конце.
    """
    report = RunReport(budget=config.budget, workers=config.workers,
                       model=config.model)
    start = time.time()
    worst = task_costs(tasks, config)
    ordered = order_tasks(tasks, worst)

    if dry_run:
        _print_plan(ordered, config, worst)
        report.seconds = time.time() - start
        return report

    if config.out_dir is not None:
        config.out_dir.mkdir(parents=True, exist_ok=True)

    tracker = BudgetTracker(budget=config.budget)
    # RLock: log() берёт этот же лок, а вызывается из solve_one уже под
    # ним (та же грабля №6, что была у BudgetTracker.spent).
    lock = threading.RLock()
    log_path = (config.out_dir / "run_log.jsonl"
                if config.out_dir is not None else None)
    local = threading.local()
    made_clients: list[LLMClient] = []

    def log(msg: str) -> None:
        with lock:
            config.log(msg)

    def client_for_worker() -> LLMClient:
        client = getattr(local, "client", None)
        if client is None:
            client = make_client()
            local.client = client
            with lock:
                made_clients.append(client)
        return client

    def solve_one(task: Task) -> TaskOutcome:
        outcome = _solve_one(task, client_for_worker(), tracker, config, worst)
        with lock:
            _append_log_line(log_path, outcome)
            report.outcomes.append(outcome)
            log(_format_outcome(outcome, config.verbose))
        return outcome

    try:
        if config.workers > 1:
            with ThreadPoolExecutor(max_workers=config.workers) as pool:
                list(pool.map(solve_one, ordered))
        else:
            for task in ordered:
                solve_one(task)
    finally:
        for client in made_clients:
            session = getattr(client, "_session", None)
            if session is not None:
                try:
                    session.close()
                except Exception:                  # noqa: BLE001 — не мешаем отчёту
                    pass

    # порядок в отчёте — как в триаже, независимо от порядка завершения
    positions = {t.task_id: i for i, t in enumerate(ordered)}
    report.outcomes.sort(key=lambda o: positions.get(o.task_id, 1_000_000))
    snapshot = tracker.snapshot()
    report.spent = snapshot["spent_output_equivalents"]
    report.calls = snapshot["calls"]
    report.failed_calls = snapshot["failed_calls"]
    report.seconds = time.time() - start
    if config.out_dir is not None:
        (config.out_dir / "run_report.json").write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8")
    return report


def _solve_one(task: Task, client: LLMClient, tracker: BudgetTracker,
               config: RunConfig,
               worst: dict[str, float | None] | None = None) -> TaskOutcome:
    """Одна задача: гейты (бюджет/дедлайн/тип) -> солвер -> результат."""
    t0 = time.time()
    outcome = TaskOutcome(task_id=task.task_id, task_type=task.task_type,
                          reward=float(task.reward), status=FAILED)

    # --- гейт: дедлайн ---
    if config.deadline is not None and time.time() > config.deadline:
        outcome.status = SKIPPED
        outcome.trace.append("skip: time limit reached")
        outcome.seconds = time.time() - t0
        return outcome

    # --- гейт: бюджет на задачу ---
    remaining = tracker.remaining
    if remaining is not None and remaining < min_viable(task.task_type):
        outcome.status = SKIPPED
        outcome.trace.append(
            f"skip: budget left {remaining:.0f} < min viable "
            f"{min_viable(task.task_type):.0f}")
        outcome.seconds = time.time() - t0
        return outcome

    # --- гейт: тип/версия ---
    try:
        solver = config.solver_for(task.task_type)
    except (KeyError, ImportError, AttributeError) as e:
        outcome.status = SKIPPED
        outcome.trace.append(f"skip: no solver ({e})")
        outcome.seconds = time.time() - t0
        return outcome

    # --- рабочая копия (тренировка: не трогаем сам набор) ---
    work_task = task
    if config.sandbox_dir is not None:
        try:
            dest = config.sandbox_dir / task.task_id
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(task.dir, dest)
            work_task = Task.from_dir(dest)
        except OSError as e:
            outcome.trace.append(f"sandbox: {e}")
            outcome.seconds = time.time() - t0
            return outcome

    # --- envelope + вызов солвера ---
    client.tracker = ScopedBudget(
        allowance_for(task.task_type, remaining,
                      (worst or {}).get(task.task_id)), tracker)
    # хук контекста задачи (логгер эксперимента); раннер агностичен к нему
    if config.on_task is not None:
        try:
            config.on_task(client, task)
        except Exception as e:                      # noqa: BLE001 — не роняем задачу
            outcome.trace.append(f"on_task hook: {type(e).__name__}: {e}")
    try:
        result = solver(work_task, client, config.model_for(task.task_type))
    except Exception as e:                          # noqa: BLE001 — задача не роняет прогон
        client.tracker = tracker
        client.call_logger = None                   # чужой контекст не должен «протухнуть»
        outcome.status = FAILED
        outcome.trace.append(f"solver: unexpected {type(e).__name__}: {e}")
        outcome.seconds = time.time() - t0
        return outcome
    client.tracker = tracker
    client.call_logger = None

    outcome.status = result.status
    outcome.answer = result.answer
    outcome.cost = result.cost
    outcome.trace = list(result.trace)
    # «answered» — это не только решение, но и записанный файл: при
    # неwritable-томе (Docker: --cap-drop ALL снимает CAP_DAC_OVERRIDE,
    # root подчиняется правам файлов) солвер вернёт answer, но файла не
    # будет — баллов за него тоже. Молча считать это успехом нельзя:
    # отчёт обязан совпадать с тем, что видит проверяющий.
    if outcome.status == ANSWERED and not result.written:
        outcome.status = FAILED
        outcome.trace.append(
            "answer NOT written (все позиции answer.txt недоступны — "
            "проверьте права на каталог задач)")
    outcome.verified = verify(task, result)         # проверяем ОРИГИНАЛ
    outcome.seconds = time.time() - t0
    return outcome


# ---------- Вывод ----------

def _format_outcome(outcome: TaskOutcome, verbose: bool = False) -> str:
    kind = {"answered": "OK  ", "unanswerable": "N/A ", "failed": "FAIL",
            "skipped": "SKIP"}.get(outcome.status, "????")
    head = (f"{outcome.task_id}: {kind} status={outcome.status} "
            f"answer={outcome.answer!r} cost={outcome.cost:.1f} "
            f"reward={outcome.reward:g}")
    if outcome.verified is not None:
        head += f" verified={outcome.verified}"
    if verbose and outcome.trace:
        head += "\n" + "\n".join(f"    {line}" for line in outcome.trace)
    return head


def _append_log_line(path: Path | None, outcome: TaskOutcome) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(outcome.to_dict(), ensure_ascii=False) + "\n")


def _print_plan(ordered: list[Task], config: RunConfig,
                costs: dict[str, float | None] | None = None) -> None:
    """--dry-run: порядок триажа и ожидаемая стоимость, 0 токенов.

    cost~ — стоимость для ПОРЯДКА: cost_worst, нормированный на профиль
    типа (см. _order_costs); она же управляет сортировкой. Envelope же
    (кап задачи в run) остаётся сырой границей cost_worst.
    """
    costs = _order_costs(ordered, costs or {})
    total = 0.0
    print(f"{'task':<20}{'type':<15}{'reward':>7}{'cost~':>8}"
          f"{'cum':>8}  version")
    for task in ordered:
        cost = costs.get(task.task_id) or expected_cost(task.task_type)
        total += cost
        version = config.versions.get(task.task_type,
                                      DEFAULT_VERSIONS.get(task.task_type))
        marker = "*" if task.task_id in costs else " "
        print(f"{task.task_id + marker:<20}{task.task_type:<15}"
              f"{task.reward:>7g}{cost:>8.0f}{total:>8.0f}  {version or '-'}")
    if costs:
        print("\n* — нормированный cost_worst (управляет порядком); "
              "кап задачи в прогоне = сырая граница cost_worst")
    budget = config.budget
    print(f"\nзадач: {len(ordered)}, ожидаемая суммарная стоимость "
          f"~{total:.0f} экв., бюджет "
          f"{'не задан (без лимита)' if budget is None else f'{budget:.0f}'}")
