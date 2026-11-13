import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from experiments.calibrate_search import calibrate, fit


class CalibrationTest(unittest.TestCase):
    def test_fit_known_line(self):
        a, b = fit([(1, 80), (2, 100), (5, 160)])
        self.assertAlmostEqual(a, 60)
        self.assertAlmostEqual(b, 20)

    def test_no_depth_variation(self):
        with self.assertRaises(ValueError):
            fit([(2, 100), (2, 105)])

    def test_followup_and_wrong_answers_excluded_from_fit(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            tasks, calls = [], []
            for tid, cost, verified, followup in (
                ('1', 80, True, False), ('2', 100, True, False),
                ('3', 120, True, False), ('4', 999, False, False),
                ('5', 999, True, True),
            ):
                tasks.append({'task_id': tid, 'cost': cost + (50 if followup else 0),
                              'verified': verified, 'trace': ['executor: answer=x']})
                call = {'task_id': tid, 'call': 0, 'solver': 'search/v2',
                        'model': 'test', 'request': {'messages': [{'content': 'prompt'}]},
                        'response': {'cost': cost}}
                calls.append(call)
                if followup:
                    calls.append({**call, 'call': 1, 'response': {'cost': 50}})
            (p / 'run_report.json').write_text(json.dumps({'tasks': tasks}))
            (p / 'calls.jsonl').write_text('\n'.join(map(json.dumps, calls)))
            with patch('experiments.calibrate_search.Task.from_dir', side_effect=lambda x: x), \
                 patch('experiments.calibrate_search.estimate_task',
                       side_effect=lambda x: SimpleNamespace(predicted_steps=int(x.name))):
                result = calibrate([p])
            self.assertEqual(result['tasks'], 3)
            self.assertEqual(result['samples'], 3)
            self.assertAlmostEqual(result['base_cost'], 60)
            self.assertAlmostEqual(result['step_cost'], 20)
            self.assertEqual(result['calls_with_followup'], 1)
            self.assertEqual(result['mean_followup_cost_all'], 10)


if __name__ == '__main__':
    unittest.main()
