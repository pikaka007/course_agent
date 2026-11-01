"""Loopback workbench API with authenticated mutations and bounded jobs."""
import base64
import json
import re
import secrets
import threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs, quote
from .agent import run_agent
from .config import ROOT, save_config
from .tools import Skills, bounded_path
from .workspace import Workspace, atomic_json
from .provider import list_models, probe
from .life import Life
from .plugins import Plugins


def make_server(config, port=8765, root=ROOT, runner=run_agent):
    root = Path(root)
    workspace = Workspace(root)
    token = secrets.token_urlsafe(32)
    lock, state_lock = threading.Lock(), threading.RLock()
    jobs, cancellations = {}, {}

    def execute(job_id, body, session, snapshot):
        def progress(event):
            with state_lock:
                if event['stage'] == 'delta':
                    jobs[job_id]['draft'] += event.get('text', '')
                    return
                if event['stage'] == 'draft_reset':
                    jobs[job_id]['draft'] = ''
                jobs[job_id]['events'] = (jobs[job_id]['events'] + [event])[-60:]
        try:
            kwargs = {'root': root, 'on_event': progress}
            if runner is run_agent:
                kwargs.update(history=session['turns'], selected_documents=body.get('documents'),
                              preferences=workspace.preferences(), cancel=cancellations[job_id], session_id=session['id'])
            result = runner(body['question'], snapshot, body['mode'], body['allow_save'], **kwargs)
            if runner is run_agent:
                workspace.append_turn(session, result)
            with state_lock:
                jobs[job_id].update(status=result['status'], result=result, draft='')
        except Exception as exc:
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '本地服务执行失败，请检查文件权限。'
            if snapshot.api_key:
                message = message.replace(snapshot.api_key, '[REDACTED]')
            with state_lock:
                jobs[job_id].update(status='error', error=message)
        finally:
            lock.release()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def send(self, status, payload, content_type='application/json; charset=utf-8', filename=None):
            data = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            for key, value in [('Content-Type', content_type), ('Content-Length', str(len(data))),
                               ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'), ('X-Frame-Options', 'DENY'),
                               ('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")]:
                self.send_header(key, value)
            if filename:
                self.send_header('Content-Disposition', "attachment; filename=note.md; filename*=UTF-8''" + quote(filename))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def valid_host(self):
            return self.headers.get('Host') in {f'127.0.0.1:{server.server_port}', f'localhost:{server.server_port}'}

        def do_GET(self):
            if not self.valid_host():
                return self.send(403, {'error': 'Invalid host'})
            try:
                self.get_route()
            except (OSError, ValueError, TypeError) as exc:
                self.send(400, {'error': str(exc) if isinstance(exc, ValueError) else '无法读取本地文件。'})

        def get_route(self):
            parsed = urlsplit(self.path)
            route, query = parsed.path, parse_qs(parsed.query)
            static = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'application/javascript'), '/style.css': ('style.css', 'text/css'), '/study.js': ('study.js', 'application/javascript')}
            if route in static:
                name, kind = static[route]
                return self.send(200, (root / 'web' / name).read_bytes(), kind + '; charset=utf-8')
            if route == '/api/study':
                return self.send(200, Life(root).snapshot())
            if route == '/api/skills':
                return self.send(200, Plugins(root).public())
            if route == '/api/skills/source':
                return self.send(200, Plugins(root).source(query.get('id',[''])[0]))
            if route == '/api/mail-export':
                return self.send(200, Life(root).mail_export(query.get('id',[''])[0]), 'message/rfc822', 'draft.eml')
            if route == '/api/status':
                return self.send(200, {**config.public(), 'token': token, 'preferences': workspace.preferences(),
                                      **Skills(root / 'data', root / 'outputs').list_documents()})
            if route == '/api/sessions':
                return self.send(200, workspace.sessions(query.get('q', [''])[0]))
            if route == '/api/session-export':
                session = workspace.store.get(query.get('id', [''])[0], full=True)
                return self.send(200, json.dumps(session, ensure_ascii=False, indent=2).encode('utf-8'),
                                 'application/json; charset=utf-8', session['id'] + '.json')
            if route == '/api/retention-preview':
                ids = workspace.store.expired(int(query.get('days', ['30'])[0]))
                return self.send(200, {'ids': ids, 'count': len(ids)})
            if route.startswith('/api/sessions/'):
                session = workspace.session(route.rsplit('/', 1)[-1])
                results = []
                for t in session['turns']:
                    rid = t.get('run_id', '')
                    if re.fullmatch('[a-f0-9]{12}', rid):
                        path = root / 'logs' / (rid + '.json')
                        if path.exists():
                            results.append(json.loads(path.read_text(encoding='utf-8')))
                return self.send(200, {**session, 'results': results})
            if route == '/api/trash':
                return self.send(200, workspace.trash())
            if route == '/api/runs':
                paths = sorted((root / 'logs').glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
                rows = []
                for p in paths:
                    try:
                        r = json.loads(p.read_text(encoding='utf-8'))
                        if 'run_id' in r:
                            rows.append(r)
                    except (OSError, ValueError):
                        continue
                return self.send(200, rows)
            if route.startswith('/api/jobs/'):
                with state_lock:
                    job = jobs.get(route.rsplit('/', 1)[-1])
                    return self.send(200, job) if job else self.send(404, {'error': '任务已过期，请查看会话或运行记录。'})
            if route == '/api/note':
                name = query.get('filename', [''])[0]
                if Path(name).name != name or '/' in name or chr(92) in name or not name.endswith('.md'):
                    raise ValueError('文件名不合法。')
                path = bounded_path(root / 'outputs', name)
                if path.is_symlink() or path.stat().st_size > 100000:
                    raise ValueError('文件不符合笔记下载限制。')
                content = path.read_bytes()
                if query.get('download') == ['1']:
                    return self.send(200, content, 'text/markdown; charset=utf-8', name)
                return self.send(200, {'filename': name, 'content': content.decode('utf-8')})
            return self.send(404, {'error': 'Not found'})

        def do_POST(self):
            nonlocal config
            if not self.valid_host() or self.headers.get('X-Agent-Token') != token:
                return self.send(403, {'error': '请求校验失败，请刷新页面。'})
            origin = self.headers.get('Origin')
            if origin and origin not in {f'http://127.0.0.1:{server.server_port}', f'http://localhost:{server.server_port}'}:
                return self.send(403, {'error': 'Invalid origin'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                limit = 56 * 1024 * 1024 if self.path == '/api/documents/upload' else (2100000 if self.path == '/api/study/import' else 24000)
                if not 0 < size <= limit:
                    raise ValueError('请求大小不合法。')
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('请求必须为对象。')
                if self.path == '/api/cancel':
                    with state_lock:
                        event = cancellations.get(body.get('job_id'))
                        if not event:
                            raise ValueError('任务不存在。')
                        event.set()
                    return self.send(200, {'ok': True, 'message': '正在停止，在途模型请求可能需要等待超时。'})
                if self.path in {'/api/run', '/api/jobs'}:
                    question, mode = body.get('question'), body.get('mode', 'live')
                    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
                        raise ValueError('问题长度应为 1 到 2000 个字符。')
                    if mode not in {'live', 'demo'} or type(body.get('allow_save', False)) is not bool:
                        raise ValueError('运行模式或保存权限无效。')
                    documents = body.get('documents')
                    Skills(root / 'data', root / 'outputs', selected_documents=documents)
                    session = workspace.session(body.get('session_id'))
                    body.update(question=question, mode=mode, allow_save=body.get('allow_save', False))
                    if mode == 'live' and runner is run_agent:
                        config.validate()
                    if not lock.acquire(blocking=False):
                        return self.send(409, {'error': '已有任务运行，请等待或停止当前任务。'})
                    job_id = secrets.token_hex(12)
                    with state_lock:
                        for old in list(jobs):
                            if len(jobs) < 20:
                                break
                            if jobs[old]['status'] != 'running':
                                del jobs[old]
                                cancellations.pop(old, None)
                        jobs[job_id] = {'job_id': job_id, 'session_id': session['id'], 'status': 'running', 'events': [], 'draft': '', 'question': question}
                        cancellations[job_id] = threading.Event()
                    if self.path == '/api/run':
                        execute(job_id, body, session, config)
                        return self.send(200, jobs[job_id].get('result', jobs[job_id]))
                    threading.Thread(target=execute, args=(job_id, body, session, config), daemon=True).start()
                    return self.send(202, {'job_id': job_id, 'session_id': session['id']})
                if not lock.acquire(blocking=False):
                    return self.send(409, {'error': '任务运行期间暂不能修改配置或资料。'})
                try:
                    if self.path == '/api/skills/install':
                        return self.send(200, Plugins(root).install(body.get('path','')))
                    if self.path == '/api/skills/enable':
                        return self.send(200, Plugins(root).enable(body.get('id'),body.get('enabled'),body.get('hash')))
                    if self.path == '/api/study/propose':
                        return self.send(200, Life(root).propose(body.get('action'),body.get('args')))
                    if self.path == '/api/study/confirm':
                        if type(body.get('accept')) is not bool:raise ValueError('需要明确确认或拒绝。')
                        return self.send(200, Life(root).confirm(body.get('id'),body['accept']))
                    if self.path == '/api/study/plan':
                        return self.send(200, Life(root).plan(body))
                    if self.path == '/api/study/import':
                        return self.send(200, Life(root).import_ics(body.get('content')))
                    if self.path == '/api/settings/model':
                        config = save_config(root, config, body)
                        return self.send(200, config.public())
                    if self.path == '/api/sessions/delete':
                        if body.get('confirm') is not True:
                            raise ValueError('请确认删除会话。')
                        return self.send(200, workspace.delete_session(body.get('id')))
                    if self.path == '/api/retention-clean':
                        ids = workspace.store.expired(body.get('days'))
                        if body.get('confirm') is not True or sorted(body.get('ids', [])) != sorted(ids):
                            raise ValueError('清理范围有变化，请重新预览后确认。')
                        for sid in ids:
                            workspace.delete_session(sid)
                        return self.send(200, {'deleted': len(ids)})
                    if self.path == '/api/settings/preferences':
                        return self.send(200, workspace.save_preferences(body))
                    if self.path == '/api/models':
                        return self.send(200, {'models': list_models(config)})
                    if self.path == '/api/probe':
                        result = probe(config)
                        atomic_json(root / 'local' / 'connection-test.json', result)
                        return self.send(200, result)
                    if self.path == '/api/documents/upload':
                        try:
                            data = base64.b64decode(body.get('content', ''), validate=True)
                        except (ValueError, TypeError):
                            raise ValueError('上传数据编码无效。') from None
                        return self.send(201, workspace.import_document(body.get('filename'), data))
                    if self.path == '/api/documents/archive':
                        return self.send(200, workspace.archive_document(body.get('filename')))
                    if self.path == '/api/documents/restore':
                        return self.send(200, workspace.restore(body.get('id')))
                    if self.path == '/api/history/archive':
                        if body.get('confirm') is not True:
                            raise ValueError('请确认归档历史记录。')
                        return self.send(200, workspace.archive_history())
                    return self.send(404, {'error': 'Not found'})
                finally:
                    lock.release()
            except (ValueError, TypeError, OSError, RuntimeError) as exc:
                message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else '本地文件操作失败。'
                if config.api_key:
                    message = message.replace(config.api_key, '[REDACTED]')
                self.send(400, {'error': message})

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    return server


def serve(config, port=8765):
    server = make_server(config, port)
    print(f'CourseMate 已启动：http://127.0.0.1:{server.server_port}（Ctrl+C 停止）', flush=True)
    print(f"模型：{config.model}  接口：{config.public()['endpoint_host']}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
