import unittest

from agent_quality_review import attach, digest


class AgentQualityReviewTests(unittest.TestCase):
    def test_hash_pinned_answer_and_memory(self):
        report = {
            'workflows': [{'id': 'w', 'memory_rubric': ['memory fact'], 'phases': [
                {'id': 'one', 'rubric': ['answer fact']}
            ]}],
            'results': [{'workflow': 'w', 'mode': 'ids', 'repeat': 1,
                         'compaction_memory': 'memory fact',
                         'phases': [{'id': 'one', 'answer': 'answer fact'}]}],
        }
        labels = {
            'answers': [{'workflow': 'w', 'mode': 'ids', 'repeat': 1, 'phase': 'one',
                         'answer_sha256': digest('answer fact'), 'rubric_hits': [True]}],
            'memories': [{'workflow': 'w', 'mode': 'ids', 'repeat': 1,
                          'memory_sha256': digest('memory fact'), 'rubric_hits': [True]}],
        }
        attached = attach(report, labels)
        self.assertTrue(attached['results'][0]['memory_quality']['acceptable'])
        self.assertTrue(attached['results'][0]['phases'][0]['quality']['acceptable'])
        report['results'][0]['phases'][0]['answer'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'Changed answer'):
            attach(report, labels)


if __name__ == '__main__':
    unittest.main()
