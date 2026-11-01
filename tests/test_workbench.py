"""V3 tests use temporary roots and fake transport; never the user's API key."""
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from course_agent.config import Config, save_config, ROOT
from course_agent.workspace import Workspace
from course_agent.agent import run_agent, LLMClient
from course_agent.tools import Skills
from course_agent.terminal import clean_terminal
import test_server


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ws = Workspace(self.root)
        (self.root / 'prompts').mkdir()
        (self.root / 'prompts/system.md').write_text('Use evidence.', encoding='utf-8')
        self.ws.import_document('lesson.md', b'RAG injects context.\nMCP provides tools.')

    def tearDown(self):
        self.temp.cleanup()

    def test_project_config_overrides_environment_without_mutating_it(self):
        (self.root / '.env').write_text('OPENAI_BASE_URL=https://api.deepseek.com\nOPENAI_MODEL=deepseek-flash\nOPENAI_API_KEY=LOCAL_TEST_KEY', encoding='utf-8')
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://old.invalid', 'OPENAI_API_KEY': 'OLD_TEST_KEY'}):
            cfg = Config.from_env(self.root)
            self.assertEqual(cfg.api_key, 'LOCAL_TEST_KEY')
            self.assertEqual(cfg.public()['sources']['OPENAI_API_KEY'], '.env')
            self.assertNotIn('LOCAL_TEST_KEY', json.dumps(cfg.public()))
            self.assertNotIn('LOCAL_TEST_KEY', repr(cfg))
            self.assertEqual(os.environ['OPENAI_API_KEY'], 'OLD_TEST_KEY')

    def test_provider_switch_never_silently_reuses_key(self):
        cfg = Config('https://one.invalid', 'TEST_SECRET', 'model')
        with self.assertRaises(ValueError):
            save_config(self.root, cfg, {'base_url': 'https://two.invalid'})
        updated = save_config(self.root, cfg, {'model': 'another'})
        self.assertEqual(updated.api_key, cfg.api_key)
        self.assertEqual(Config.from_env(self.root).model, 'another')
        with self.assertRaises(ValueError):
            save_config(self.root, cfg, {'model': 'bad\nOPENAI_API_KEY=bad'})

    def test_upload_no_overwrite_and_recoverable_remove(self):
        with self.assertRaises(ValueError):
            self.ws.import_document('lesson.md', b'changed')
        archived = self.ws.archive_document('lesson.md')
        self.assertFalse((self.root / 'data/lesson.md').exists())
        self.assertEqual(self.ws.trash()[0]['filename'], 'lesson.md')
        self.ws.restore(archived['id'])
        self.assertIn('RAG', (self.root / 'data/lesson.md').read_text())

    def test_import_paths_invalid_names_and_corrupt_files(self):
        for name in ('../x.md', '..\\x.md', 'a:secret.md', '.hidden.md', 'CON.txt', 'README.md', 'bad.pdf'):
            with self.assertRaises(ValueError, msg=name):
                self.ws.import_document(name, b'test')
        with self.assertRaises(ValueError):
            self.ws.import_document('bad.docx', b'not zip')
        self.assertFalse((self.root / 'data/bad.docx').exists())

    def test_selected_documents_are_enforced_at_read_and_search(self):
        self.ws.import_document('other.md', b'Private material')
        skills = Skills(self.root/'data', self.root/'outputs', ['lesson.md'])
        self.assertEqual(len(skills.documents()), 1)
        self.assertFalse(skills.dispatch('read_document', {'filename':'other.md','section':1})['ok'])
        self.assertEqual(skills.search_documents('Private', 2)['matches'], [])

    def test_preferences_validate_and_persist(self):
        prefs = self.ws.preferences()
        prefs.update(answer_length=300, instructions='Explain briefly', templates=[{'title':'Test','question':'Explain RAG'}])
        self.ws.save_preferences(prefs)
        self.assertEqual(Workspace(self.root).preferences(), prefs)
        prefs['answer_length'] = True
        with self.assertRaises(ValueError):
            self.ws.save_preferences(prefs)

    def test_sessions_persist_and_reject_path_traversal(self):
        s = self.ws.session()
        self.ws.append_turn(s, {'question':'RAG?', 'answer':'Answer', 'status':'completed', 'mode':'live', 'run_id':'abcdef012345'})
        self.assertEqual(Workspace(self.root).session(s['id'])['turns'][0]['question'], 'RAG?')
        with self.assertRaises(ValueError):
            self.ws.session('../../.env')
        self.ws.archive_history()
        self.assertEqual(self.ws.sessions(), [])
        self.assertTrue((self.root/'data/lesson.md').exists())

    def test_followup_context_sent_but_sources_retrieved_fresh(self):
        captured = []
        class Fake:
            def complete(s, messages, specs):
                captured.append(json.loads(json.dumps(messages)))
                if len(captured) == 1:
                    return {'role':'assistant', 'tool_calls':[{'id':'read','type':'function','function':{'name':'read_document','arguments':'{"filename":"lesson.md","section":1}'}}]}
                return {'role':'assistant','content':'RAG context. [lesson.md · 第1行]'}
        history = [{'question':'What is RAG?', 'answer':'Earlier answer', 'status':'completed','mode':'live'}]
        result = run_agent('Give an example', Config('', '', 'test'), root=self.root, history=history, client=Fake())
        self.assertEqual(captured[0][1]['content'], 'What is RAG?')
        self.assertEqual(captured[0][2]['content'], 'Earlier answer')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(len(result['source_records']), 1)

    def test_cancel_after_model_response_prevents_tool_write(self):
        cancel=threading.Event()
        class Fake:
            def complete(s, *args):
                cancel.set()
                return {'role':'assistant','tool_calls':[{'id':'save','type':'function','function':{'name':'save_note','arguments':'{"filename":"bad.md","content":"bad"}'}}]}
        result=run_agent('save',Config('','','test'),allow_save=True,root=self.root,client=Fake(),cancel=cancel)
        self.assertEqual(result['status'],'cancelled')
        self.assertEqual(list((self.root/'outputs').glob('*')), [])

    def test_provider_receipt_retains_usage_without_headers_or_secret(self):
        class Response:
            headers={'x-request-id':'request-test','Authorization':'DO_NOT_STORE'}
            status=200
            def __enter__(s): return s
            def __exit__(s,*args): pass
            def read(s,n): return json.dumps({'id':'completion-test','model':'returned','usage':{'total_tokens':7},'choices':[{'message':{'role':'assistant','content':'OK'}}]}).encode()
        class Opener:
            def open(s,*args,**kwargs):return Response()
        with patch('urllib.request.build_opener',return_value=Opener()):
            client=LLMClient(Config('https://example.invalid','TEST_SECRET','model'))
            client.complete([],[])
        self.assertEqual(client.requests[0]['usage']['total_tokens'],7)
        self.assertEqual(client.requests[0]['request_id'],'request-test')
        self.assertNotIn('DO_NOT_STORE',json.dumps(client.requests))

    def test_terminal_escape_codes_removed(self):
        self.assertNotIn('\x1b',clean_terminal('\x1b]52;clipboard\x07hello'))

    def test_rich_terminal_real_prompt_input_and_session_persistence(self):
        try:
            import io
            from rich.console import Console
            from prompt_toolkit import PromptSession
            from prompt_toolkit.input import create_pipe_input
            from prompt_toolkit.output import DummyOutput
        except ImportError:
            self.skipTest('Install terminal requirements to exercise rich terminal')
        from course_agent.terminal import chat
        output = io.StringIO()
        console = Console(file=output, width=90, force_terminal=False)
        with create_pipe_input() as pipe:
            pipe.send_text('/docs\n/use lesson.md\nExplain RAG\n/sessions\n/quit\n')
            def factory(**kwargs):
                return PromptSession(input=pipe, output=DummyOutput(), **kwargs)
            with patch('sys.stdin.isatty', return_value=True), patch('sys.stdout.isatty', return_value=True), patch('rich.console.Console', return_value=console), patch('prompt_toolkit.PromptSession', side_effect=factory):
                self.assertEqual(chat(Config('', '', 'test'), mode='demo', root=self.root), 0)
        self.assertIn('lesson.md', output.getvalue())
        self.assertIn('completed', output.getvalue())
        self.assertEqual(len(self.ws.sessions()), 1)


class WorkbenchHTTPTest(unittest.TestCase):
    setUp = test_server.ServerTest.setUp
    tearDown = test_server.ServerTest.tearDown
    request = test_server.ServerTest.request
    def test_model_preferences_upload_restore_and_secret_isolation(self):
        headers={'X-Agent-Token':self.token}
        cfg={'base_url':'https://example.invalid/v1','model':'test-model','api_key':'ONLY_TEST_SECRET'}
        code,data,_=self.request('/api/settings/model',cfg,headers)
        self.assertEqual(code,200)
        self.assertNotIn('ONLY_TEST_SECRET',json.dumps(data))
        self.assertNotIn('ONLY_TEST_SECRET',json.dumps(self.request('/api/status')[1]))
        payload={'filename':'uploaded.md','content':base64.b64encode(b'RAG test text').decode()}
        self.assertEqual(self.request('/api/documents/upload',payload,headers)[0],201)
        self.assertEqual(self.request('/api/documents/upload',payload,headers)[0],400)
        _, archived,_=self.request('/api/documents/archive',{'filename':'uploaded.md'},headers)
        self.assertEqual(self.request('/api/documents/restore',{'id':archived['id']},headers)[0],200)
        self.assertEqual(self.request('/api/settings/preferences',{'answer_length':200,'instructions':'Short','templates':[]},headers)[0],200)
        self.assertEqual(self.request('/api/status')[1]['preferences']['answer_length'],200)

    def test_cross_origin_mutation_and_session_traversal_rejected(self):
        self.assertEqual(self.request('/api/settings/preferences',{}, {'X-Agent-Token':self.token,'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.request('/api/sessions/..%2F.env')[0],400)

    def test_busy_config_is_not_mutated(self):
        headers={'X-Agent-Token':self.token}
        self.request('/api/jobs',{'question':'test','mode':'demo'},headers)
        self.assertEqual(self.request('/api/settings/preferences',{'answer_length':200,'instructions':'','templates':[]},headers)[0],409)
