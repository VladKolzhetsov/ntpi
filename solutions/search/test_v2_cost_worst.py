"""cost_worst: детерминированная верхняя граница полной стоимости задачи.

Проверяется: разложение формулы (независимый пересчёт), инварианты
(worst >= стоимости первого вызова и план-компоненты), монотонность по
глубине и размеру KB, гейты fallback (пустая/большая база, kb=None).
Живая валидация против фактических расходов прогонов — вне юнит-тестов
(experiments/runs в .gitignore), см. CALIBRATION.md.
"""

import unittest
from pathlib import Path
from unittest.mock import patch

from solutions.search.v2.complexity import (
    ASSISTANT_ECHO_CAP, QA_OUTPUT_CAP, REPAIR_ERROR_TOKENS,
    TRUNCATION_NOTICE_TOKENS, estimate, estimate_task,
)
from solutions.search.v2.kb import KnowledgeBase, Node
from solutions.search.v2.tokens import predict_input
from solutions.task_parser import Task

ROOT = Path(__file__).resolve().parents[2]


def kb_nodes(*records):
    return KnowledgeBase([Node(name, "node", attrs, "", Path(f"node_{name}.md"))
                          for name, attrs in records], files_seen=len(records))


def qa_input_for(question, kb):
    """Независимая сборка QA-промпта — та же, что в estimate()."""
    from solutions.search.v2.solver import QA_SYSTEM_PROMPT, _dump_cards
    return predict_input([
        {"role": "system", "content": QA_SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nFacts:\n{_dump_cards(kb)}"},
    ])


def formula(input_tokens, cap, qa_input):
    """Копия формулы из complexity._cost_worst (защита от рассинхрона)."""
    c_plan = input_tokens / 10 + cap
    echo = min(cap, ASSISTANT_ECHO_CAP)
    c_semantic = (input_tokens + echo + REPAIR_ERROR_TOKENS) / 10 + cap
    trunc_cap = ((cap * 2 + 31) // 32) * 32
    c_trunc = (input_tokens + TRUNCATION_NOTICE_TOKENS) / 10 + trunc_cap
    c_fallback = qa_input / 10 + QA_OUTPUT_CAP if qa_input is not None else 0.0
    return c_plan + max(c_semantic, c_trunc) + c_fallback


class CostWorstTest(unittest.TestCase):
    KB = kb_nodes(
        ("Anna Petrova", {"Born": "1980", "Employer": "Northwind Ltd"}),
        ("Northwind Ltd", {"Headquarters": "Osaka"}),
    )
    QUESTION = "In which year was Anna Petrova born?"

    def estimate_kb(self, question, kb=None):
        kb = self.KB if kb is None else kb
        return estimate(question, kb.schema(), kb=kb), kb

    def test_formula_decomposition_matches_estimate(self):
        result, _ = self.estimate_kb(self.QUESTION)
        qa = qa_input_for(self.QUESTION, self.KB)
        expected = formula(result.predicted_input_tokens,
                           result.recommended_max_tokens, qa)
        self.assertAlmostEqual(result.cost_worst, round(expected, 1))

    def test_worst_bounds_first_call_and_plan_component(self):
        result, _ = self.estimate_kb(self.QUESTION)
        self.assertGreaterEqual(result.cost_worst, result.predicted_cost)
        # C_plan: input/10 + cap (completion <= max_tokens по контракту API)
        self.assertGreaterEqual(
            result.cost_worst,
            result.predicted_input_tokens / 10 + result.recommended_max_tokens)
        # даже медианный первый вызов + худший QA остаётся под границей
        self.assertGreaterEqual(
            result.cost_worst,
            result.predicted_cost + qa_input_for(self.QUESTION, self.KB) / 10
            + QA_OUTPUT_CAP)

    def test_worst_grows_with_depth(self):
        shallow, _ = self.estimate_kb("In which year was Anna Petrova born?")
        deep, _ = self.estimate_kb(
            "In which city is the headquarters of the employer of Anna Petrova?")
        self.assertGreater(deep.predicted_steps, shallow.predicted_steps)
        self.assertGreater(deep.cost_worst, shallow.cost_worst)

    def test_worst_grows_with_kb_size(self):
        small = kb_nodes(("Anna", {"Born": "1980"}))
        big = kb_nodes(*[("Person%d" % i, {"Born": "1980", "City": "Osaka"})
                         for i in range(40)])
        r_small, _ = self.estimate_kb("What is Anna born?", small)
        r_big, _ = self.estimate_kb("What is Anna born?", big)
        self.assertGreater(r_big.cost_worst, r_small.cost_worst)

    def test_worst_is_dynamic_not_constant(self):
        results = [self.estimate_kb(q)[0] for q in (
            "In which year was Anna Petrova born?",
            "In which city is the headquarters of the employer of Anna Petrova?")]
        self.assertNotEqual(results[0].cost_worst, results[1].cost_worst)

    def test_empty_kb_gates_fallback_term(self):
        result = estimate(self.QUESTION, {"node": ["Born"]}, kb=KnowledgeBase([]))
        qa = None
        expected = formula(result.predicted_input_tokens,
                           result.recommended_max_tokens, qa)
        self.assertAlmostEqual(result.cost_worst, round(expected, 1))
        self.assertTrue(any("empty kb" in r for r in result.reasons))

    def test_no_kb_leaves_fallback_unpriced(self):
        result = estimate(self.QUESTION, {"node": ["Born"]})
        expected = formula(result.predicted_input_tokens,
                           result.recommended_max_tokens, None)
        self.assertAlmostEqual(result.cost_worst, round(expected, 1))
        self.assertTrue(any("not priced" in r for r in result.reasons))

    def test_oversize_kb_gates_fallback_term(self):
        big = kb_nodes(*[("Person%d" % i, {"Born": "1980"}) for i in range(7)])
        with patch("solutions.search.v2.solver.MAX_FALLBACK_CARDS", 5):
            result = estimate(self.QUESTION, big.schema(), kb=big)
        expected = formula(result.predicted_input_tokens,
                           result.recommended_max_tokens, None)
        self.assertAlmostEqual(result.cost_worst, round(expected, 1))
        self.assertTrue(any("kb too big" in r for r in result.reasons))

    def test_covers_observed_costs_on_training_tasks(self):
        """worst >= фактической стоимости любого v2-прогона тренировки.

        Порог — максимум из живых прогонов (309.5 для search_0034,
        167.2 для search_0033 с ремонтным раундом, ~149 с QA), с запасом:
        граница обязана накрывать их на любой из задач тренировки.
        """
        worst_seen = 310.0
        checked = 0
        for task_dir in sorted((ROOT / "tasks/task-dir/task-dir").glob("search_*")):
            task = Task.from_dir(task_dir)
            result = estimate_task(task)
            self.assertGreaterEqual(
                result.cost_worst, result.predicted_cost,
                f"{task.task_id}: worst must bound the first call")
            if result.cost_worst < worst_seen:
                self.fail(f"{task.task_id}: worst={result.cost_worst} "
                          f"< наблюдаемого максимума {worst_seen}")
            checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
