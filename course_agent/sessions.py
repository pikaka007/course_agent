"""Transactional chat store. Legacy JSON is imported once without deleting it."""
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from pathlib import Path


class Sessions:
    def __init__(self, state):
        self.state = Path(state)
        self.state.mkdir(parents=True, exist_ok=True)
        self.path = self.state / 'workspace.sqlite3'
        with self.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY,title TEXT,updated TEXT,archived INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS turns(seq INTEGER PRIMARY KEY AUTOINCREMENT,sid TEXT,run_id TEXT UNIQUE,body TEXT);
              CREATE INDEX IF NOT EXISTS turns_sid ON turns(sid);
              CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT);''')
            db.execute('BEGIN IMMEDIATE')
            if not db.execute("SELECT 1 FROM metadata WHERE key='json-import-v3'").fetchone():
                for p in (self.state/'sessions').glob('*.json'):
                    try:
                        s = json.loads(p.read_text(encoding='utf-8'))
                        self.check_id(s['id'])
                        db.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,0)', (s['id'],s['title'],s['updated']))
                        for row in s['turns'][-100:]:
                            db.execute('INSERT OR IGNORE INTO turns(sid,run_id,body) VALUES(?,?,?)', (s['id'],row['run_id'],json.dumps(row,ensure_ascii=False)))
                    except (ValueError, KeyError, OSError, TypeError):
                        continue
                db.execute("INSERT INTO metadata VALUES('json-import-v3','done')")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('PRAGMA secure_delete=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def check_id(sid):
        if not isinstance(sid,str) or not re.fullmatch('[a-f0-9]{16}',sid):
            raise ValueError('会话编号无效。')

    def get(self,sid,full=False):
        self.check_id(sid)
        with self.connect() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND archived=0',(sid,)).fetchone()
            if not row:
                raise ValueError('会话不存在或已归档。')
            turns = [json.loads(r[0]) for r in db.execute('SELECT body FROM turns WHERE sid=? ORDER BY seq DESC'+('' if full else ' LIMIT 100'),(sid,))][::-1]
            return {'id':sid,'title':row['title'],'updated':row['updated'],'turns':turns}

    def list(self, query=''):
        with self.connect() as db:
            rows = db.execute('''SELECT s.id,s.title,s.updated,COUNT(t.seq) AS count FROM sessions s
                LEFT JOIN turns t ON s.id=t.sid WHERE s.archived=0 AND (s.title LIKE ? OR EXISTS
                (SELECT 1 FROM turns x WHERE x.sid=s.id AND x.body LIKE ?)) GROUP BY s.id ORDER BY s.updated DESC LIMIT 100''',('%'+query[:100]+'%','%'+query[:100]+'%')).fetchall()
            return [dict(r) for r in rows]

    def append(self, session, result):
        self.check_id(session['id'])
        row = {k:result.get(k) for k in ('question','answer','run_id','status','mode')}
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO sessions VALUES(?,?,?,0)',(session['id'],result['question'][:40],now))
            db.execute('INSERT OR IGNORE INTO turns(sid,run_id,body) VALUES(?,?,?)',(session['id'],row['run_id'],json.dumps(row,ensure_ascii=False)))
            db.execute('UPDATE sessions SET updated=?,archived=0 WHERE id=?',(now,session['id']))
        session.update(self.get(session['id']))

    def archive(self):
        with self.connect() as db:
            rows = [dict(r) for r in db.execute('SELECT * FROM sessions WHERE archived=0')]
            db.execute('UPDATE sessions SET archived=1')
            return len(rows)

    def expired(self, days):
        if type(days) is not int or not 1 <= days <= 3650:
            raise ValueError('保留天数须为 1 到 3650。')
        cutoff = (datetime.now(timezone.utc)-timedelta(days=days)).isoformat()
        with self.connect() as db:
            return [r[0] for r in db.execute('SELECT id FROM sessions WHERE updated<?',(cutoff,))]

    def delete(self, sid):
        self.check_id(sid)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            ids=[r[0] for r in db.execute('SELECT run_id FROM turns WHERE sid=?',(sid,))]
            db.execute('DELETE FROM turns WHERE sid=?',(sid,))
            db.execute('DELETE FROM sessions WHERE id=?',(sid,))
        return ids
