"""Adaptive cap, truncation and budgets: real client with fake HTTP transport."""

import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from solutions.llm import LLMClient, BudgetTracker
from solutions.search.v2.planner import SearchPlanner, build_messages
from solutions.search.v2.tokens import affordable_cap, predict_input, predict_output, recommend_cap
from solutions.tests.search.test_planner import SCHEMA, StubKB, VALID_PLAN
from solutions.tests.test_llm_fakes import FakeResponse, FakeSession


def reply(text, output=20, finish=None, prompt=50):
    choice = {'message': {'content': text}}
    if finish is not None:
        choice['finish_reason'] = finish
    return FakeResponse(data={'choices': [choice], 'usage': {
        'prompt_tokens': prompt, 'completion_tokens': output}})


class AdaptiveTokensTest(unittest.TestCase):
    def planner(self, responses, budget=None, **kwargs):
        session = FakeSession(responses)
        client = LLMClient('https://p/v1', 'k', session=session,
                           tracker=BudgetTracker(budget), retry_delay=0)
        return SearchPlanner(client, **kwargs), session

    def test_length_grows_within_existing_repair_limit(self):
        planner, session = self.planner([
            reply('{"steps":[', 100, 'length'), reply(VALID_PLAN, 70, 'stop')],
            max_tokens=100, predicted_output_tokens=80)
        result = planner.plan('question', StubKB(SCHEMA))
        self.assertIsNotNone(result.plan)
        self.assertEqual(len(session.calls), 2)
        self.assertGreater(session.calls[1]['json']['max_tokens'], 100)
        self.assertFalse(any(m['role'] == 'assistant' for m in session.calls[1]['json']['messages']))

    def test_truncation_inferred_only_without_finish_reason(self):
        for finish, grows in [(None, True), ('stop', False)]:
            with self.subTest(finish=finish):
                planner, session = self.planner([
                    reply('{', 100, finish), reply(VALID_PLAN)], max_tokens=100)
                planner.plan('question', StubKB(SCHEMA))
                self.assertEqual(session.calls[1]['json']['max_tokens'] > 100, grows)

    def test_valid_json_at_cap_without_reason_accepted(self):
        planner, session = self.planner([reply(VALID_PLAN, 100)], max_tokens=100)
        self.assertIsNotNone(planner.plan('question', StubKB(SCHEMA)).plan)
        self.assertEqual(len(session.calls), 1)

    def test_semantic_repair_keeps_cap_and_feedback(self):
        planner, session = self.planner([
            reply('{"steps":[],"answer":"missing"}', 20, 'stop'), reply(VALID_PLAN)],
            max_tokens=128)
        self.assertIsNotNone(planner.plan('question', StubKB(SCHEMA)).plan)
        self.assertEqual(session.calls[1]['json']['max_tokens'], 128)
        self.assertIn('Invalid plan', session.calls[1]['json']['messages'][-1]['content'])

    def test_second_truncation_stops(self):
        planner, session = self.planner([reply('{', 100, 'length'), reply('{', 224, 'length')],
                                        max_tokens=100)
        result = planner.plan('question', StubKB(SCHEMA))
        self.assertIsNone(result.plan)
        self.assertIn('truncated', result.error)
        self.assertEqual(len(session.calls), 2)

    def test_no_budget_for_larger_retry(self):
        planner, session = self.planner([reply('{', 100, 'length')], budget=180,
                                        max_tokens=100, predicted_output_tokens=80)
        result = planner.plan('question', StubKB(SCHEMA))
        self.assertIsNone(result.plan)
        self.assertIn('budget', result.error)
        self.assertEqual(len(session.calls), 1)

    def test_budget_gate_before_http_and_budget_clipping(self):
        planner, session = self.planner([], budget=50, max_tokens=200,
                                        predicted_output_tokens=80)
        self.assertIn('budget', planner.plan('question', StubKB(SCHEMA)).error)
        self.assertEqual(session.calls, [])
        planner, session = self.planner([reply(VALID_PLAN)], budget=200, max_tokens=400,
                                        predicted_output_tokens=80)
        self.assertIsNotNone(planner.plan('question', StubKB(SCHEMA)).plan)
        self.assertLess(session.calls[0]['json']['max_tokens'], 200)

    def test_unknown_floor_and_scaling(self):
        self.assertGreaterEqual(recommend_cap(50, .2, 'unknown'), 400)
        self.assertGreater(recommend_cap(predict_output(50), .3, 'find_get'), 400)
        self.assertGreaterEqual(recommend_cap(50, .7, 'find_get'), 100)

    def test_actual_messages_change_input_not_output(self):
        small = predict_input(build_messages('short', SCHEMA))
        large = predict_input(build_messages('long ' * 100, SCHEMA))
        self.assertGreater(large, small)
        self.assertIsNone(affordable_cap(60, 60, [], 20))

    def test_fallback_does_not_spend_exhausted_budget(self):
        from solutions.search.v2.solver import _fallback_qa
        from solutions.search.v2.kb import KnowledgeBase
        planner, session = self.planner([], budget=1)
        self.assertIsNone(_fallback_qa(planner.client, planner.model, 'question', KnowledgeBase([])))
        self.assertEqual(session.calls, [])

    def test_solver_wires_recommendation_and_override(self):
        from solutions.search.v2.solver import solve
        from solutions.search.v2.complexity import estimate_task
        from solutions.task_parser import Task
        plan = json.dumps({'steps': [
            {'var': 'p', 'op': 'find', 'name': 'Anna'},
            {'var': 'b', 'op': 'get', 'from': 'p', 'field': 'Born'}], 'answer': 'b'})
        for override in (None, 400):
            with self.subTest(override=override), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                kb_dir = root / 'workspace/kb'
                kb_dir.mkdir(parents=True)
                (kb_dir / 'person_anna.md').write_text('# Anna\nBorn: 1980\n')
                (root / 'task.json').write_text(json.dumps({
                    'task_id': 'search_test', 'task_type': 'search', 'reward': 1,
                    'instruction': 'When was Anna born?', 'check': 'exact_str'}))
                task = Task.from_dir(root)
                expected_cap = override or estimate_task(task).recommended_max_tokens
                planner, session = self.planner([reply(plan)])
                result = solve(task, planner.client, max_tokens=override)
                self.assertEqual(result.answer, '1980')
                self.assertEqual(session.calls[0]['json']['max_tokens'], expected_cap)
                self.assertTrue(any('source=override' in t if override else
                                    'source=complexity' in t for t in result.trace))
                self.assertEqual((root / 'answer.txt').read_text().strip(), '1980')
                self.assertEqual((root / 'workspace/answer.txt').read_text().strip(), '1980')

    def test_solver_static_cap_mode(self):
        from solutions.search.v2.solver import solve
        import json
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'workspace/kb').mkdir(parents=True)
            (root / 'workspace/kb/person_anna.md').write_text('# Anna\nBorn: 1980\n')
            (root / 'task.json').write_text(json.dumps({
                'task_id': 'search_static', 'task_type': 'search', 'reward': 1,
                'instruction': 'When was Anna born?', 'check': 'exact_str'}))
            task = __import__('solutions.task_parser', fromlist=['Task']).Task.from_dir(root)
            plan = '{"steps":[{"var":"p","op":"find","name":"Anna"},{"var":"b","op":"get","from":"p","field":"Born"}],"answer":"b"}'
            planner, session = self.planner([reply(plan)])
            with patch.dict('os.environ', {'SEARCH_V2_CAP_MODE': 'static'}):
                solve(task, planner.client)
            self.assertEqual(session.calls[0]['json']['max_tokens'], 400)


if __name__ == '__main__':
    unittest.main()
