import json
import tempfile
import unittest
from pathlib import Path
from course_agent.agent import LLMClient, run_agent
from course_agent.config import Config
from course_agent.evidence import review, retain_supported

CITE = '[a.md · 第1行]'

class ReviewClient:
    def complete(self, messages, specs):
        return {'content': json.dumps({'claims': [
            {'id': 0, 'verdict': 'supported', 'reason': 'present', 'evidence': [{'citation': CITE, 'quote': 'Budget is 120 yuan.'}]},
            {'id': 1, 'verdict': 'unsupported', 'reason': 'recommendation is not completion', 'evidence': []}
        ]})}

class PartialEvidenceTest(unittest.TestCase):
    def checked(self):
        answer = f'## 两项已完成成果\n\n1. Budget is 120 yuan. {CITE}\n\n2. The annotation is already added. {CITE}'
        records = [{'citation': CITE, 'text': 'Budget is 120 yuan. An annotation should be added.'}]
        return answer, review(answer, records, ReviewClient())

    def test_only_supported_blocks_retained_without_unchecked_heading(self):
        answer, checked = self.checked()
        text, result = retain_supported(answer, checked)
        self.assertIn('Budget is 120 yuan.', text)
        self.assertNotIn('already added', text)
        self.assertNotIn('两项已完成', text)
        self.assertIn('部分结果', text)
        self.assertEqual(result['omitted_ids'], [1])
        self.assertFalse(result['ok'])
        self.assertEqual(result['status'], 'partial')

    def test_invalid_incomplete_or_mismatched_review_cannot_release_content(self):
        answer, checked = self.checked()
        self.assertIsNone(retain_supported(answer, {**checked, 'status': 'invalid_review'}))
        self.assertIsNone(retain_supported(answer, {**checked, 'claims': checked['claims'][:1]}))
        self.assertIsNone(retain_supported(answer+' changed '+CITE, checked))

    def test_no_supported_claims_has_no_partial_result(self):
        answer, checked = self.checked()
        for row in checked['claims']: row['verdict'] = 'unsupported'
        self.assertIsNone(retain_supported(answer, checked))

    def test_fabricated_quote_not_retained(self):
        answer, _ = self.checked()
        checked = review(answer, [{'citation': CITE, 'text': 'No matching quote.'}], ReviewClient())
        self.assertIsNone(retain_supported(answer, checked))

    def run_fixture(self, rounds, regress=False):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td); (root/'data').mkdir(); (root/'prompts').mkdir()
            (root/'data/a.md').write_text('Budget is 120 yuan. An annotation should be added.')
            (root/'prompts/system.md').write_text('Use evidence.')
            class Client(LLMClient):
                def __init__(self): self.requests = []; self.n = 0; self.checks = 0
                def stream_complete(self, messages, specs, on_delta, cancel=None):
                    if not specs:
                        self.checks += 1
                        data = ReviewClient().complete(messages, specs)
                        if regress and self.checks > 1:
                            payload = json.loads(data['content'])
                            for c in payload['claims']: c['verdict'] = 'unsupported'
                            data['content'] = json.dumps(payload)
                        return data
                    self.n += 1
                    if self.n == 1:
                        return {'tool_calls': [{'id': 'read1', 'type': 'function', 'function': {'name': 'read_document', 'arguments': '{"filename":"a.md","section":1}'}}]}
                    return {'content': f'Budget is 120 yuan. {CITE}\n\nThe annotation is already added. {CITE}'}
            return run_agent('Summarize', Config('', '', 'test', max_rounds=rounds), root=root, client=Client())

    def test_partial_result_after_bounded_repair_preserves_audit(self):
        r = self.run_fixture(4)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['completion_quality'], 'partial')
        self.assertEqual(len(r['evidence_review_attempts']), 2)
        self.assertNotIn('already added', r['answer'])
        self.assertTrue(r['citation_audit']['ok'])

    def test_last_round_can_still_return_verified_content(self):
        r = self.run_fixture(2)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['completion_quality'], 'partial')
        self.assertEqual(r['metrics']['evidence_repairs'], 0)

    def test_failed_repair_does_not_discard_previously_verified_content(self):
        r = self.run_fixture(4, regress=True)
        self.assertEqual(r['status'], 'completed')
        self.assertEqual(r['completion_quality'], 'partial')
        self.assertIn('Budget is 120 yuan.', r['answer'])

if __name__ == '__main__': unittest.main()
