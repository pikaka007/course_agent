"""Local preferences, recoverable document removal and bounded chat sessions."""
import copy
import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from .tools import SUPPORTED, MAX_FILE, parse_document
from .sessions import Sessions

DEFAULTS = {'answer_length': 600, 'instructions': '用中文解释概念，先给结论，再给依据。', 'templates': [
    {'title': '理解一个概念', 'question': '根据资料解释 MCP 的工作流程，并引用原文。'},
    {'title': '对比两个概念', 'question': '比较 Tool Use 与 RAG 的区别，引用具体出处。'},
    {'title': '整理复习提纲', 'question': '根据资料整理一份简洁的复习提纲，每个要点标注出处。'}]}


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


class Workspace:
    def __init__(self, root):
        self.root = Path(root)
        self.state = self.root / 'local'
        self.lock = threading.RLock()
        for name in ('data', 'outputs', 'logs', 'local/sessions', 'local/trash'):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self.store = Sessions(self.state)

    def preferences(self):
        path = self.state / 'settings.json'
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else copy.deepcopy(DEFAULTS)

    def save_preferences(self, body):
        n, instructions, templates = body.get('answer_length'), body.get('instructions'), body.get('templates')
        if type(n) is not int or not 100 <= n <= 4000:
            raise ValueError('回答长度须为 100 到 4000。')
        if not isinstance(instructions, str) or len(instructions) > 4000:
            raise ValueError('回答偏好最多 4000 字。')
        if not isinstance(templates, list) or len(templates) > 12:
            raise ValueError('最多保存 12 个任务模板。')
        clean = []
        for row in templates:
            if not isinstance(row, dict) or any(not isinstance(row.get(k), str) or not 1 <= len(row[k].strip()) <= limit for k, limit in [('title', 40), ('question', 2000)]):
                raise ValueError('模板需要标题和问题，分别不超过 40 和 2000 字。')
            clean.append({k: row[k].strip() for k in ('title', 'question')})
        result = {'answer_length': n, 'instructions': instructions, 'templates': clean}
        with self.lock:
            atomic_json(self.state / 'settings.json', result)
        return result

    def _session_path(self, sid):
        if not isinstance(sid, str) or not re.fullmatch(r'[a-f0-9]{16}', sid):
            raise ValueError('会话编号无效。')
        return self.state / 'sessions' / (sid + '.json')

    def session(self, sid=None):
        if sid:
            return self.store.get(sid)
        return {'id': secrets.token_hex(8), 'title': '新会话', 'turns': [], 'updated': ''}

    def sessions(self, query=''):
        return self.store.list(query)

    def append_turn(self, session, result):
        self.store.append(session, result)

    def import_document(self, filename, data):
        self.check_filename(filename)
        if not isinstance(data, bytes) or not 0 < len(data) <= MAX_FILE:
            raise ValueError('文件必须为 1 字节到 40 MB。')
        target = self.root / 'data' / filename
        with self.lock:
            try:
                with target.open('xb') as stream:
                    stream.write(data)
            except FileExistsError:
                raise ValueError('同名资料已存在，请更换文件名。') from None
            try:
                if not list(parse_document(target)):
                    raise ValueError('没有可提取文本。')
            except Exception as exc:
                target.unlink(missing_ok=True)
                raise ValueError(str(exc) if isinstance(exc, ValueError) else '文件无法解析；支持 TXT/MD、DOCX、PPTX 和文字型 PDF。') from None
        return {'filename': filename, 'bytes': len(data)}

    @staticmethod
    def check_filename(name):
        if (not isinstance(name, str) or len(name) > 160 or not name or name.startswith('.')
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((' ', '.'))
                or Path(name).suffix.lower() not in SUPPORTED
                or Path(name).stem.lower() in {'readme', 'agents', 'license', 'changelog', 'con', 'prn', 'aux', 'nul'}
                or re.fullmatch(r'(com|lpt)\d', Path(name).stem, re.I)):
            raise ValueError('文件名或文件类型不受支持。')

    def archive_document(self, name):
        self.check_filename(name)
        path = self.root / 'data' / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('资料不存在。')
        folder = self.state / 'trash' / secrets.token_hex(8)
        folder.mkdir()
        path.rename(folder / name)
        return {'id': folder.name, 'filename': name}

    def trash(self):
        return [{'id': p.parent.name, 'filename': p.name} for p in (self.state / 'trash').glob('*/*') if p.is_file() and p.suffix.lower() in SUPPORTED]

    def restore(self, tid):
        if not isinstance(tid, str) or not re.fullmatch('[a-f0-9]{16}', tid):
            raise ValueError('回收站编号无效。')
        files = list((self.state / 'trash' / tid).glob('*'))
        if len(files) != 1 or files[0].is_symlink():
            raise ValueError('回收记录不存在。')
        p = files[0]
        target = self.root / 'data' / p.name
        if target.exists():
            raise ValueError('同名资料已存在，无法恢复。')
        p.rename(target)
        p.parent.rmdir()
        return {'filename': target.name}

    def archive_history(self):
        dest = self.state / 'history-archive' / secrets.token_hex(8)
        count = 0
        for folder in ('logs', 'local/sessions'):
            for p in (self.root / folder).glob('*.json'):
                target = dest / folder / p.name
                target.parent.mkdir(parents=True, exist_ok=True)
                p.rename(target)
                count += 1
        sessions = self.store.archive()
        return {'archived': count, 'sessions': sessions, 'location': str(dest.relative_to(self.root))}

    def delete_session(self, sid):
        ids = self.store.delete(sid)
        count = 0
        for rid in ids:
            if isinstance(rid, str) and re.fullmatch('[a-f0-9]{12}', rid):
                paths = [self.root/'logs'/(rid+'.json')]
                paths.extend((self.state/'history-archive').glob('*/logs/'+rid+'.json'))
                for p in paths:
                    if p.is_file() and not p.is_symlink():
                        p.unlink(); count += 1
        legacy = self.state/'sessions'/(sid+'.json')
        legacy.unlink(missing_ok=True)
        for p in (self.state/'history-archive').glob('*/local/sessions/'+sid+'.json'):
            if not p.is_symlink():
                p.unlink()
        return {'deleted_session': sid, 'logs_removed': count,
                'preserved': '资料、笔记及 evidence 中的独立证据副本保留。'}
