import json
import tempfile
import unittest
from pathlib import Path
from course_agent.agent import LLMClient, run_agent
from course_agent.config import Config
from course_agent.citations import normalize_citations, retain_cited
from course_agent.evidence import blocks

C = '[a.md · 第1行]'

class CitationRecoveryTest(unittest.TestCase):
    def test_normalize_only_unique_complete_marker(self):
        text, changes = normalize_citations('预算【a.md·第1行】', {C})
        self.assertEqual(text, '预算'+C)
        self.assertEqual(len(changes), 1)
        self.assertEqual(normalize_citations('[第1行]', {C})[0], '[第1行]')
        self.assertEqual(normalize_citations('[a.md·第99行]', {C})[1], [])

    def test_ambiguous_normalization_is_not_guessed(self):
        self.assertFalse(normalize_citations('[a b.md·第1行]', {'[ab.md · 第1行]', '[a b.md · 第1行]'})[1])

    def test_drop_entire_line_with_unknown_source(self):
        answer = f'## Three facts\nGood {C}\nBad {C} [fake.md · 第2行]\nUncited facts\n```\ncode {C}\n```'
        text, omitted = retain_cited(answer, {C})
        self.assertEqual(text, 'Good '+C)
        self.assertEqual(len(omitted), 4)

    def fixture(self, rounds=5, evidence_first=False):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir();(root/'prompts').mkdir()
            (root/'data/a.md').write_text('Budget is 120 yuan.')
            (root/'prompts/system.md').write_text('Use evidence.')
            class Client(LLMClient):
                def __init__(self): self.n=0;self.requests=[];self.reviews=0
                def stream_complete(self,messages,specs,on_delta,cancel=None):
                    if not specs:
                        self.reviews+=1
                        rows=json.loads(messages[-1]['content'])['claims']
                        return {'content':json.dumps({'claims':[{'id':c['id'],'verdict':'supported' if c['id']==0 else 'unsupported','reason':'fixture','evidence':[{'citation':C,'quote':'Budget is 120 yuan.'}] if c['id']==0 else []} for c in rows]})}
                    self.n+=1
                    if self.n==1:return {'tool_calls':[{'id':'read','type':'function','function':{'name':'read_document','arguments':'{"filename":"a.md","section":1}'}}]}
                    if evidence_first:
                        if self.n==2:return {'content':f'Budget is 120 yuan. {C}\n\nBudget is 999 yuan. {C}'}
                        return {'content':'Fake [fake.md · 第99行]'}
                    return {'content':f'Budget is 120 yuan. {C}\n\nUnverified long statement with no citation at all.\n\nFake [fake.md · 第99行]'}
            client=Client()
            r=run_agent('summarize',Config('','','test',max_rounds=rounds),root=root,client=client)
            return r,client.reviews

    def test_filtered_citations_still_require_semantic_review(self):
        r,checks=self.fixture()
        self.assertEqual(r['status'],'completed')
        self.assertEqual(r['completion_quality'],'partial')
        self.assertEqual(checks,1)
        self.assertNotIn('Unverified',r['answer'])
        self.assertNotIn('fake.md',r['answer'])
        self.assertEqual(len(r['citation_audit_attempts']),3)
        self.assertTrue(r['citation_audit']['ok'])
        self.assertIn('fake.md',str(r['citation_audit_attempts'][0]))

    def test_last_round_citation_failure_retains_valid_content(self):
        r,checks=self.fixture(2)
        self.assertEqual(r['status'],'completed')
        self.assertEqual(checks,1)
        self.assertEqual(r['metrics']['citation_repairs'],0)

    def test_citation_failure_after_evidence_repair_keeps_checkpoint(self):
        r,_=self.fixture(5,True)
        self.assertEqual(r['status'],'completed')
        self.assertEqual(r['completion_quality'],'partial')
        self.assertIn('120 yuan',r['answer'])
        self.assertNotIn('999 yuan',r['answer'])

class TargetSelectionTest(unittest.TestCase):
    def fixture(self,question='总结一下这个ppt',selection=None):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'data').mkdir();(root/'prompts').mkdir()
            (root/'data/a.pptx').write_text('fixture, not parsed')
            (root/'data/b.pptx').write_text('fixture, not parsed')
            (root/'prompts/system.md').write_text('Use evidence.')
            class Client:
                n=0
                def complete(self,*args):self.n+=1;raise RuntimeError('model reached')
            client=Client()
            r=run_agent(question,Config('','','test'),root=root,client=client,selected_documents=selection)
            return r,client.n

    def test_multiple_ppts_generic_summary_asks_without_model(self):
        r,n=self.fixture()
        self.assertEqual(n,0)
        self.assertEqual(r['document_choices'],['a.pptx','b.pptx'])
        self.assertEqual(r['status'],'completed')
        self.assertEqual(r['metrics']['documents_parsed'],0)

    def test_explicit_scope_does_not_ask_again(self):
        r,n=self.fixture(selection=['a.pptx'])
        self.assertEqual(n,1)
        self.assertNotIn('document_choices',r)

    def test_explicit_filename_and_plural_request_do_not_ask(self):
        for question in ('总结a.pptx','分别总结这两份ppt'):
            r,n=self.fixture(question)
            self.assertEqual(n,1)
            self.assertNotIn('document_choices',r)

if __name__=='__main__':unittest.main()
