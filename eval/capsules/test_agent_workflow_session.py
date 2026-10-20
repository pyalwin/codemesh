from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_workflow_session import catalog, run_trajectory
from experiment import extract_unit


class SessionWorkflowTests(unittest.TestCase):
    def test_no_source_mode_exposes_only_capsule_read(self):
        workflow = {'units': ['sample.py::run']}
        self.assertIn('read(U#) for a capsule', catalog(workflow, 'ids_guard_nosource'))
        self.assertNotIn('read_source', catalog(workflow, 'ids_guard_nosource'))

    def test_resumes_before_compaction_then_starts_fresh_session(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'sample.py').write_text('def run():\n    return helper()\n')
            unit = extract_unit(root, 'sample.py::run')
            workflow = {'id': 'flow', 'units': [unit['id']], 'phases': [
                {'id': 'one', 'question': 'Inspect.', 'min_reads': 1},
                {'id': 'two', 'question': 'Follow.', 'min_reads': 1},
                {'id': 'three', 'question': 'Resume.', 'min_reads': 0},
            ]}
            decisions = [
                {'action': 'read', 'unit': 'U1', 'answer': ''},
                {'action': 'answer', 'unit': '', 'answer': 'one'},
                {'action': 'read', 'unit': 'U1', 'answer': ''},
                {'action': 'answer', 'unit': '', 'answer': 'two'},
                {'action': 'answer', 'unit': '', 'answer': 'three'},
            ]
            seen = []
            def fake_call(message, session_id, *, resume, structured, out):
                seen.append((session_id, resume, structured, message))
                if structured:
                    return {'decision': decisions.pop(0), 'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0.01}
                return {'answer': 'verified memory', 'input_tokens': 1, 'output_tokens': 1, 'cost_usd': 0.01}
            with patch('agent_workflow_session.session_call', side_effect=fake_call):
                result = run_trajectory(workflow, 'ids_guard', 1, {unit['id']: unit}, root)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['tool_reads'], 2)
            self.assertEqual([entry[1] for entry in seen], [False, True, True, True, True, False])
            self.assertEqual(len({entry[0] for entry in seen[:5]}), 1)
            self.assertNotEqual(seen[4][0], seen[5][0])
            self.assertIn('verified memory', seen[5][3])
            self.assertNotIn('Tool result for read', seen[5][3])


if __name__ == '__main__':
    unittest.main()
