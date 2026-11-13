# -*- coding: utf-8 -*-
"""Тесты агрегации экспериментов (experiments/agg.py) — офлайн, без сети.

Строим фейковые каталоги прогонов (run.json + run_report.json +
calls.jsonl) в tmpdir и проверяем загрузку серий, медианы, таблицы и
рекомендации против констант раннера.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from experiments.agg import (
    format_recommendations, format_task_table, format_type_table,
    load_series, series_stats,
)
from solutions.runner import EXPECTED_COST, MIN_VIABLE


def write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def outcome(task_id, task_type, reward, status="answered",
            cost=100.0, verified=True):
    return {"task_id": task_id, "task_type": task_type, "reward": reward,
            "status": status, "answer": "x", "cost": cost,
            "verified": verified, "seconds": 1.0, "trace": []}


def report(outcomes, spent=None):
    spent = spent if spent is not None else sum(o["cost"] for o in outcomes)
    return {
        "budget": None, "spent": spent, "calls": 0, "failed_calls": 0,
        "seconds": 1.0, "workers": 1, "model": "m",
        "counts": {"answered": len(outcomes), "unanswerable": 0,
                   "failed": 0, "skipped": 0, "total": len(outcomes)},
        "reward": {"total": sum(o["reward"] for o in outcomes),
                   "answered": sum(o["reward"] for o in outcomes),
                   "earned": sum(o["reward"] for o in outcomes
                                 if o["verified"])},
        "tasks": outcomes,
    }


class Env(unittest.TestCase):

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.series_dir = self.tmpdir / "series"
        self.series_dir.mkdir()

    def write_run(self, run_dir, config, rep, calls=()):
        run_dir.mkdir(parents=True, exist_ok=True)
        write_json(run_dir / "run.json", config)
        write_json(run_dir / "run_report.json", rep)
        lines = [json.dumps(c, ensure_ascii=False) for c in calls]
        (run_dir / "calls.jsonl").write_text("\n".join(lines) + "\n",
                                             encoding="utf-8")

    def config(self, **overrides):
        base = {"label": "test", "model": "m-noreason",
                "models": {}, "versions": {"math": "v2", "coding": "v2"}}
        base.update(overrides)
        return base


class LoadSeriesTest(Env):

    def test_series_layout_and_sort(self):
        for k in (2, 1, 10):
            self.write_run(self.series_dir / f"r{k}", self.config(),
                            report([]))
        runs = load_series(self.series_dir)
        self.assertEqual([r.label for r in runs], ["test"] * 3)
        # порядок повторов числовой, не лексикографический
        self.assertEqual(len(runs), 3)

    def test_bare_run_layout(self):
        self.write_run(self.tmpdir / "bare", self.config(), report([]))
        runs = load_series(self.tmpdir / "bare")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0].label, "test")

    def test_missing_dir_raises(self):
        with self.assertRaises(FileNotFoundError):
            load_series(self.tmpdir / "nope")

    def test_empty_series_raises(self):
        (self.series_dir / "r1").mkdir()
        with self.assertRaises(FileNotFoundError):
            load_series(self.series_dir)

    def test_no_calls_file_is_ok(self):
        self.write_run(self.series_dir / "r1", self.config(), report([]))
        (self.series_dir / "r1" / "calls.jsonl").unlink()
        runs = load_series(self.series_dir)
        self.assertEqual(runs[0].calls, [])


class ModelResolutionTest(Env):

    def test_override_beats_default(self):
        cfg = self.config(models={"coding": "m-reason"})
        self.write_run(self.series_dir / "r1", cfg, report([]))
        (run,) = load_series(self.series_dir)
        self.assertEqual(run.model_for("coding"), "m-reason")
        self.assertEqual(run.model_for("math"), "m-noreason")
        self.assertEqual(run.version_for("math"), "v2")
        self.assertEqual(run.version_for("search"), "?")


class SeriesStatsTest(Env):

    def setUp(self):
        super().setUp()
        self.m1 = [outcome("m_1", "math", 5, cost=40.0),
                   outcome("m_2", "math", 3, cost=80.0)]
        self.m2 = [outcome("m_1", "math", 5, cost=60.0, verified=False),
                   outcome("m_2", "math", 3, cost=100.0)]
        self.calls = [
            {"task_id": "m_1", "call": 0, "error": None},
            {"task_id": "m_1", "call": 1, "error": None},
            {"task_id": "m_2", "call": 0, "error": {"type": "E", "message": "x"}},
        ]

    def test_two_runs_medians(self):
        self.write_run(self.series_dir / "r1", self.config(),
                        report(self.m1, spent=120.0), self.calls)
        self.write_run(self.series_dir / "r2", self.config(),
                        report(self.m2, spent=160.0), self.calls)
        (stats,) = [series_stats(load_series(self.series_dir))]
        self.assertEqual(stats["runs"], 2)
        slot = stats["per_type"]["math"]
        # медиана пулу [40, 80, 60, 100] = 70.0
        self.assertEqual(slot["cost_median"], 70.0)
        self.assertEqual(slot["cost_min"], 40.0)
        self.assertEqual(slot["cost_max"], 100.0)
        self.assertEqual(slot["slots"], 4)
        self.assertEqual(slot["answered"], 4)
        # m_1: верен только первый повтор (5), m_2: верен в обоих (3+3)
        self.assertEqual(slot["reward_earned"], 11.0)
        self.assertEqual(slot["reward_total"], 16.0)
        # вызовов на задачу: медиана [2, 2, 1, 1]
        self.assertEqual(slot["calls_median"], 1.5)
        self.assertEqual(slot["call_errors"], 2)
        self.assertEqual(slot["model"], "m-noreason")
        self.assertEqual(slot["version"], "v2")

    def test_per_task_noise(self):
        self.write_run(self.series_dir / "r1", self.config(),
                        report(self.m1), self.calls)
        self.write_run(self.series_dir / "r2", self.config(),
                        report(self.m2), self.calls)
        (stats,) = [series_stats(load_series(self.series_dir))]
        m1 = stats["per_task"]["m_1"]
        self.assertEqual(m1["cost_min"], 40.0)
        # медиана двух элементов [40, 60] = их среднее
        self.assertEqual(m1["cost_median"], 50.0)
        self.assertEqual(m1["verified_earned"], 1)
        self.assertEqual(m1["verified_of"], 2)
        self.assertEqual(m1["calls"], [2, 2])
        self.assertEqual(m1["task_type"], "math")

    def test_skipped_excluded(self):
        runs_outcomes = [outcome("m_1", "math", 5, cost=40.0),
                         outcome("m_2", "math", 3, status="skipped", cost=0.0)]
        self.write_run(self.series_dir / "r1", self.config(),
                        report(runs_outcomes), [])
        (stats,) = [series_stats(load_series(self.series_dir))]
        slot = stats["per_type"]["math"]
        self.assertEqual(slot["slots"], 1)
        self.assertEqual(slot["cost_median"], 40.0)
        self.assertNotIn("m_2", stats["per_task"])

    def test_unverified_not_in_reward_total(self):
        outcomes = [outcome("m_1", "math", 5, verified=None),
                    outcome("m_2", "math", 3, verified=True)]
        self.write_run(self.series_dir / "r1", self.config(),
                        report(outcomes), [])
        (stats,) = [series_stats(load_series(self.series_dir))]
        slot = stats["per_type"]["math"]
        self.assertEqual(slot["reward_earned"], 3.0)
        self.assertEqual(slot["reward_total"], 3.0)


class FormatTest(Env):

    def make_stats(self):
        self.write_run(
            self.series_dir / "r1", self.config(),
            report([outcome("m_1", "math", 5, cost=40.0)]),
            [{"task_id": "m_1", "call": 0, "error": None}])
        return series_stats(load_series(self.series_dir))

    def test_type_table_has_columns(self):
        text = format_type_table(self.make_stats())
        self.assertIn("math", text)
        self.assertIn("m-noreason", text)
        self.assertIn("v2", text)
        self.assertIn("40", text)
        self.assertIn("1/1", text)

    def test_task_table_has_columns(self):
        text = format_task_table(self.make_stats())
        self.assertIn("m_1", text)
        self.assertIn("1/1", text)

    def test_recommendations_echo_constants(self):
        stats = self.make_stats()
        text = format_recommendations(stats)
        self.assertIn(f"EXPECTED_COST={EXPECTED_COST['math']:g}", text)
        self.assertIn(f"MIN_VIABLE={MIN_VIABLE['math']:g}", text)
        # 40 против 80 = 0.50 -> вне ±30% -> пометка
        self.assertIn("пересмотреть", text)

    def test_recommendations_within_drift_no_mark(self):
        self.write_run(
            self.series_dir / "r1", self.config(),
            report([outcome("m_1", "math", 5, cost=75.0)]), [])
        stats = series_stats(load_series(self.series_dir))
        self.assertNotIn("пересмотреть", format_recommendations(stats))

    def test_empty_recommendations(self):
        self.assertEqual(format_recommendations({}), "")


if __name__ == "__main__":
    unittest.main()
