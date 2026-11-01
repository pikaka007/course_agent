"""Local HTTP integration tests. No remote model requests."""
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from course_agent.config import Config, ROOT
from course_agent.server import make_server


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.root = Path(self.tmp.name)
        for name in ['data', 'outputs', 'web']:
            (self.root / name).mkdir()
        (self.root / 'web/index.html').write_text('CourseMate', encoding='utf-8')
        (self.root / 'outputs/笔记.md').write_text('# Note\nLocal content', encoding='utf-8')
        self.release = threading.Event()

        def runner(question, config, mode, allow_save, root, on_event=None):
            if on_event:
                on_event({'stage': 'tool', 'name': 'search_documents'})
            self.release.wait(5)
            return {'status': 'completed', 'answer': 'done'}

        self.server = make_server(Config('', '', 'test'), 0, self.root, runner)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.token = self.request('/api/status')[1]['token']

    def tearDown(self):
        self.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, data=None, headers=None):
        request = urllib.request.Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=headers or {})
        try:
            response = urllib.request.urlopen(request, timeout=3)
        except urllib.error.HTTPError as e:
            response = e
        with response:
            body = response.read()
            return response.status, json.loads(body) if 'application/json' in response.headers.get('Content-Type', '') else body, response.headers

    def test_job_progress_and_concurrent_submission(self):
        payload = {'question': 'test', 'mode': 'live'}
        headers = {'X-Agent-Token': self.token}
        code, job, _ = self.request('/api/jobs', payload, headers)
        self.assertEqual(code, 202)
        code, state, _ = self.request('/api/jobs/' + job['job_id'])
        self.assertEqual(state['status'], 'running')
        self.assertEqual(self.request('/api/jobs', payload, headers)[0], 409)
        self.release.set()
        # Bounded event wait to let the worker publish completion.
        for _ in range(100):
            _, state, _ = self.request('/api/jobs/' + job['job_id'])
            if state['status'] != 'running':
                break
            threading.Event().wait(0.01)
        self.assertEqual(state['status'], 'completed')
        self.assertEqual(state['events'][0]['name'], 'search_documents')

    def test_note_preview_and_download(self):
        path = '/api/note?filename=' + urllib.parse.quote('笔记.md')
        code, body, _ = self.request(path)
        self.assertEqual(code, 200)
        expected = (self.root / 'outputs/笔记.md').read_bytes()
        self.assertEqual(body['content'], expected.decode('utf-8'))
        code, body, headers = self.request(path + '&download=1')
        self.assertEqual(body, expected)
        self.assertIn('attachment;', headers['Content-Disposition'])

    def test_note_traversal_and_host_rejected(self):
        self.assertEqual(self.request('/api/note?filename=..%2Fdata%2Fprivate.md')[0], 400)
        self.assertEqual(self.request('/api/note?filename=..%5Cdata%5Cprivate.md')[0], 400)
        self.assertEqual(self.request('/api/status', headers={'Host': 'attacker.example'})[0], 403)

    def test_missing_token_and_invalid_mode_rejected(self):
        self.assertEqual(self.request('/api/jobs', {'question': 'test'})[0], 403)
        self.assertEqual(self.request('/api/jobs', {'question': 'test', 'mode': 'bad'}, {'X-Agent-Token': self.token})[0], 400)
