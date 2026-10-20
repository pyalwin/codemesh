import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow import expand_answer, make_registry, render_read, run_trajectory
from experiment import extract_unit


SOURCE = '''def first(value):
    """Return a transformed value."""
    return helper(value)

def second(value):
    """Return another transformed value."""
    return other(value)
'''


class AgentWorkflowTests(unittest.TestCase):
    def test_stable_aliases_and_source_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.py').write_text(SOURCE)
            units = {name: extract_unit(root, name) for name in ('sample.py::first', 'sample.py::second')}
            registry = make_registry(list(units.values()))
            first = render_read('U1', 'ids', 'What does first call?', units, registry)
            second = render_read('U2', 'ids', 'What does second call?', units, registry)
            self.assertIn('### U1 first', first)
            self.assertIn('helper(value)', first)
            self.assertIn('### U2 second', second)
            self.assertIn('other(value)', second)
            self.assertNotIn('sample.py:', first)
            call_alias = next(alias for alias, item in registry['evidence'].items() if item['line'] == 3)
            self.assertIn(call_alias, first)
            self.assertEqual(expand_answer(f'Calls helper [{call_alias}]', registry), 'Calls helper [sample.py:3]')
            self.assertEqual(expand_answer('See U1:2-3', registry), 'See sample.py:2-3')

    def test_compaction_discards_old_observations_and_can_rehydrate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.py').write_text(SOURCE)
            unit = extract_unit(root, 'sample.py::first')
            workflow = {
                'id': 'flow', 'units': [unit['id']],
                'phases': [
                    {'id': 'one', 'question': 'Inspect first.', 'min_reads': 1},
                    {'id': 'two', 'question': 'Follow first.', 'min_reads': 1},
                    {'id': 'three', 'question': 'Resume.', 'min_reads': 0},
                ],
            }
            decisions = [
                {'action': 'read', 'unit': 'U1', 'answer': ''},
                {'action': 'answer', 'unit': '', 'answer': 'First answer'},
                {'action': 'read', 'unit': 'U1', 'answer': ''},
                {'action': 'answer', 'unit': '', 'answer': 'Second answer'},
                {'action': 'read', 'unit': 'U1', 'answer': ''},
                {'action': 'answer', 'unit': '', 'answer': 'Final answer'},
            ]
            def fake_model(prompt, *, structured, out):
                if not structured:
                    return {'answer': 'Memory U1', 'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0.01}
                decision = decisions.pop(0)
                return {'decision': decision, 'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0.01}
            with patch('agent_workflow.model_call', side_effect=fake_model):
                result = run_trajectory(workflow, 'ids', 1, {unit['id']: unit}, root)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['tool_reads'], 3)
            self.assertEqual(result['repeat_reads'], 2)
            self.assertEqual(result['totals']['input_tokens'], 7)
            post_prompt = result['calls'][-2]['prompt']
            self.assertIn('Memory U1', post_prompt)
            self.assertNotIn('First answer', post_prompt)

    def test_guarded_raise_is_explicit_only_in_guard_variant(self):
        source = '''def validate(by_alias=False, by_name=None):
    if by_alias is False and by_name is not True:
        raise PydanticUserError("choose an input name")
    return helper(by_alias=by_alias, by_name=by_name)
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.py').write_text(source)
            unit = extract_unit(root, 'sample.py::validate')
            units = {unit['id']: unit}
            registry = make_registry([unit])
            question = 'What happens when by_alias=False and by_name=None?'
            plain = render_read('U1', 'ids', question, units, registry)
            guarded = render_read('U1', 'ids_guard', question, units, registry)
            self.assertNotIn('by_alias is False', plain)
            self.assertIn('if by_alias is False and by_name is not True', guarded)
            self.assertIn('raise PydanticUserError', guarded)


if __name__ == '__main__':
    unittest.main()
