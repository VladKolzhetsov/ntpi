#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Точка входа харнесса — то, что запускает регламент:

    python /app/run_agent.py --tasks-dir /data/tasks

Читает окружение прогона (LLM_BASE_URL / LLM_API_KEY / опц. LLM_BUDGET,
TASKS, OUT) и переменные .env при локальной разработке; см.
solutions/llm.py::client_from_env.

Вся логика — в solutions/runner.py (диспетчер типов, бюджет, триаж);
здесь только разбор аргументов и печать сводки.

Полезные флаги для отладки:
    --dry-run           показать порядок триажа и ожидаемую стоимость, 0 токенов;
    --sandbox DIR       решать копии задач в DIR (тренировочный набор не портится);
    --versions math=v1,coding=v1   A/B-сравнение версий солверов;
    --worst-case [TYPES]  worst-case триаж (по умолчанию search; all — все,
                        none — выключить);
    --only coding_0003,math_0000   прогнать только эти задачи;
    --limit 2           первые N задач порядка триажа.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from solutions.base import ANSWERED, FAILED, SolveResult, check_writable
from solutions.llm import (LLMError, merged_env, QWEN_38_27B_NOREASON,
                           client_from_env)
from solutions.runner import (
    DEFAULT_VERSIONS, RunConfig, fmt_worst, parse_worst_case, run,
)
from solutions.task_parser import Task


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    env = merged_env()
    parser = argparse.ArgumentParser(
        description="Харнесс openJiuwen Challenge: решить набор задач.")
    parser.add_argument(
        "--tasks-dir", default=env.get("TASKS") or "/data/tasks",
        help="каталог с задачами (по умолчанию $TASKS или /data/tasks)")
    parser.add_argument(
        "--out", default=env.get("OUT") or "/tmp/run_agent_out",
        help="каталог отчёта и лога (по умолчанию $OUT или /tmp/run_agent_out)")
    parser.add_argument(
        "--budget", type=float, default=None,
        help="бюджет в output-эквивалентах (по умолчанию $LLM_BUDGET; "
             "не задан — без лимита)")
    parser.add_argument(
        "--model", default=env.get("MODEL_NAME") or QWEN_38_27B_NOREASON,
        help="модель прокси")
    parser.add_argument(
        "--workers", type=int, default=1,
        help="параллельных сессий (регламент: до 5)")
    parser.add_argument(
        "--time-limit", type=float, default=3600.0,
        help="секунд на весь прогон (регламент ~1 час); 0 — без лимита")
    parser.add_argument(
        "--versions", default="",
        help="переопределение версий солверов: type=vN через запятую")
    parser.add_argument(
        "--models", default="",
        help="модели по типам: type=model через запятую "
             "(по умолчанию одна --model на все типы)")
    parser.add_argument(
        "--only", default="",
        help="прогнать только перечисленные task_id (через запятую)")
    parser.add_argument(
        "--limit", type=int, default=0,
        help="ограничить число задач (после сортировки по плотности)")
    parser.add_argument(
        "--sandbox", default="",
        help="решать копии задач в этом каталоге (тренировочный набор цел)")
    parser.add_argument(
        "--worst-case", nargs="?", const="all", default="search",
        metavar="TYPES",
        help="worst-case триаж per-task cost_worst: типы через запятую "
             "(по умолчанию search); без значения — все типы; none — "
             "выключить. Порядок — по нормированной границе, кап задачи — "
             "по сырой")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="только порядок триажа и ожидаемая стоимость (0 токенов)")
    return parser.parse_args(argv)


def parse_versions(raw: str) -> dict[str, str | None]:
    """'math=v1,coding=v1' -> {'math': 'v1', 'coding': 'v1'}."""
    versions: dict[str, str | None] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        key, _, value = chunk.partition("=")
        versions[key.strip()] = value.strip() or None
    return versions


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    tasks_dir = Path(args.tasks_dir)
    if not tasks_dir.is_dir():
        print(f"нет каталога задач: {tasks_dir}", file=sys.stderr)
        return 2

    tasks = Task.load_dir(tasks_dir)
    if args.only:
        wanted = {t.strip() for t in args.only.split(",") if t.strip()}
        tasks = [t for t in tasks if t.task_id in wanted]

    env = merged_env()
    budget = args.budget
    if budget is None and env.get("LLM_BUDGET"):
        budget = float(env["LLM_BUDGET"])

    workers = max(1, min(5, args.workers))      # регламент: до 5 сессий
    deadline = (time.time() + args.time_limit) if args.time_limit > 0 else None
    sandbox = Path(args.sandbox) if args.sandbox else None
    if sandbox is not None:
        sandbox.mkdir(parents=True, exist_ok=True)

    try:
        worst_case = parse_worst_case(args.worst_case)
    except ValueError as e:
        print(f"ОШИБКА: --worst-case: {e}", file=sys.stderr)
        return 2

    config = RunConfig(
        model=args.model,
        budget=budget,
        versions=parse_versions(args.versions),
        models={k: v for k, v in parse_versions(args.models).items() if v},
        workers=workers,
        deadline=deadline,
        sandbox_dir=sandbox,
        out_dir=Path(args.out),
        worst_case=worst_case,
    )
    if args.limit > 0:
        from solutions.runner import order_tasks, task_costs
        tasks = order_tasks(tasks, task_costs(tasks, config))[:args.limit]
    if not tasks:
        print(f"задачи не найдены в {tasks_dir}", file=sys.stderr)
        return 2

    print(f"задач: {len(tasks)} | модель: {args.model} "
          f"{'+' + str(parse_versions(args.models)) if args.models else ''} "
          f"| воркеров: {workers} "
          f"| бюджет: {'без лимита' if budget is None else f'{budget:.0f}'} "
          f"| worst-case: {fmt_worst(config.worst_case)} | версии: "
          f"{ {t: config.versions.get(t, DEFAULT_VERSIONS.get(t)) for t in DEFAULT_VERSIONS} }")

    if args.dry_run:
        run(tasks, make_client=None, config=config, dry_run=True)  # type: ignore[arg-type]
        return 0

    # Предстартовая проверка записи (0 токенов) — ПОСЛЕ dry-run (тот не
    # пишет). Ответы обязаны лечь рядом с задачами; при неwritable-томе
    # прогон молча вернул бы ноль баллов с зелёным отчётом. Docker:
    # --cap-drop ALL снимает CAP_DAC_OVERRIDE, поэтому root подчиняется
    # правам файлов и смонтированный $TASKS без chmod 777 не пишется.
    # Проверяем корень и каталоги самих ответов (права могут расходиться).
    probe_dirs = [sandbox if sandbox is not None else tasks_dir]
    for task in tasks[:3]:                     # выборка: дорогие квоты видны сразу
        probe_dirs.extend(p.parent for p in task.answer_candidates)
    for directory in probe_dirs:
        unwritable = check_writable(directory)
        if unwritable is not None:
            print(f"ОШИБКА: ответы некуда записать — {unwritable}\n"
                  f"Проверьте права на каталог задач (в Docker — chmod 777 "
                  f"смонтированного $TASKS).", file=sys.stderr)
            return 3

    # Предстартовая проверка настроек LLM (0 токенов): пустые ключи — самая
    # частая ошибка docker run (в .env они зовутся API_BASE/API_KEY, и
    # неэкспортированная переменная уезжает в контейнер пустой строкой).
    # Проверяем до прогона, чтобы не ловить трейсбек из воркера.
    from solutions.llm import client_from_env as _make_client
    try:
        _make_client()
    except LLMError as e:
        print(f"ОШИБКА: {e}", file=sys.stderr)
        return 4

    report = run(tasks, make_client=client_from_env, config=config)
    print(f"\n{'-' * 60}")
    print(f"решено (answer.txt): {report.answered}/{len(report.outcomes)} "
          f"| нерешаемых: {report.unanswerable} | провалов: {report.failed} "
          f"| пропущено: {report.skipped}")
    if any(o.verified is not None for o in report.outcomes):
        print(f"подтверждено проверкой: {report.reward_earned:g} "
              f"баллов (написано ответов на {report.reward_answered:g} "
              f"из {report.reward_total:g})")
    print(f"расход: {report.spent:.1f} экв. за {report.seconds:.0f}s, "
          f"вызовов LLM: {report.calls} (упавших {report.failed_calls})")
    print(f"отчёт: {Path(args.out) / 'run_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
