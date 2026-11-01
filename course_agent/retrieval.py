"""Persistent FTS5 BM25 + phrase/Chinese-bigram ranking. No embeddings or cloud index."""
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

STOP = set('what is are the a an of to in for and please explain tell me about how does do work according document documents course'.split())
ZH_FILLERS = ('请问', '请', '根据课程资料', '根据资料', '能否', '如何', '什么是', '是什么', '为什么', '解释一下', '解释', '介绍一下', '介绍', '说明', '的区别', '有什么', '怎样', '怎么', '工作流程', '调用流程', '流程', '的作用', '原理', '含义', '一下')


def terms(text):
    out = []
    for term in re.findall(r'[a-z0-9_]+|[\u4e00-\u9fff]+', text.lower()):
        if re.fullmatch('[a-z0-9_]+', term):
            if term not in STOP:
                out.append(term)
        else:
            out.extend(term[i:i+2] for i in range(len(term)-1))
            if len(term) == 1:
                out.append(term)
    return out


def query_plan(query, aliases):
    q = query.lower().strip()
    for filler in ZH_FILLERS:
        q = q.replace(filler, ' ')
    q = re.sub(r'^[的\s]+|[的？?。\s]+$', '', q)
    variants = [q]
    for group in aliases:
        for alias in sorted(group, key=len, reverse=True):
            pattern = r'(?<![a-z0-9_])' + re.escape(alias) + r'(?![a-z0-9_])' if re.search('[a-z]', alias) else re.escape(alias)
            if re.search(pattern, q):
                variants.extend(re.sub(pattern, other, q) for other in group)
                break
    return list(dict.fromkeys(v for v in variants if v.strip()))[:20]


def eligible(body, variant):
    """Preserve meaningful English constraints; don't return 'error correction' for 'quantum error correction'."""
    words = [w for w in re.findall('[a-z0-9_]+', variant) if w not in STOP]
    if any(not re.search(r'(?<![a-z0-9_])' + re.escape(w) + r'(?![a-z0-9_])', body.lower()) for w in words):
        return False
    grams = set(t for t in terms(variant) if re.search('[\u4e00-\u9fff]', t))
    if grams:
        overlap = len(grams & set(terms(body))) / len(grams)
        if overlap < .6:
            return False
    return bool(words or grams)


class Index:
    def __init__(self, data):
        self.path = Path(data).parent / 'local' / 'index.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self):
        con = sqlite3.connect(self.path, timeout=15)
        con.row_factory = sqlite3.Row
        con.execute('PRAGMA journal_mode=WAL')
        con.execute('CREATE TABLE IF NOT EXISTS files(name TEXT PRIMARY KEY, signature TEXT NOT NULL)')
        con.execute('CREATE VIRTUAL TABLE IF NOT EXISTS passages USING fts5(name UNINDEXED, section UNINDEXED, location UNINDEXED, body UNINDEXED, tokens)')
        try:
            with con:
                yield con
        finally:
            con.close()

    def sync(self, paths, parser):
        warnings = []
        with self.connect() as con:
            active = {p.name for p in paths}
            for row in con.execute('SELECT name FROM files').fetchall():
                if row['name'] not in active:
                    con.execute('DELETE FROM passages WHERE name=?', (row['name'],))
                    con.execute('DELETE FROM files WHERE name=?', (row['name'],))
            for path in paths:
                try:
                    stat = path.stat()
                    signature = f'v4:{stat.st_mtime_ns}:{stat.st_size}'
                    prev = con.execute('SELECT signature FROM files WHERE name=?', (path.name,)).fetchone()
                    if prev and prev[0] == signature:
                        continue
                    con.execute('DELETE FROM passages WHERE name=?', (path.name,))
                    con.execute('DELETE FROM files WHERE name=?', (path.name,))
                    rows = list(parser(path))
                    for n, location, body in rows:
                        con.execute('INSERT INTO passages VALUES(?,?,?,?,?)', (path.name, n, location, body, ' '.join(terms(body))))
                    con.execute('INSERT INTO files VALUES(?,?)', (path.name, signature))
                except Exception as exc:
                    con.execute('DELETE FROM passages WHERE name=?', (path.name,))
                    con.execute('DELETE FROM files WHERE name=?', (path.name,))
                    warnings.append({'filename': path.name, 'error': type(exc).__name__, 'message': str(exc) if isinstance(exc, ValueError) else '资料无法解析'})
        return warnings

    def search(self, query, limit, aliases, allowed=None):
        variants = query_plan(query, aliases)
        vocab = set(t for v in variants for t in terms(v))
        if not vocab:
            raise ValueError('请提供有意义的文字关键词。')
        expression = ' OR '.join('"' + t.replace('"', '""') + '"' for t in sorted(vocab))
        with self.connect() as con:
            scope = '' if allowed is None else ' AND name IN (' + ','.join('?' for _ in allowed) + ')'
            rows = con.execute('SELECT name,section,location,body,bm25(passages) AS rank FROM passages WHERE passages MATCH ?' + scope + ' ORDER BY rank LIMIT 300', (expression, *(allowed or []))).fetchall()
        ranked = []
        for rank, row in enumerate(rows):
            low = row['body'].lower()
            valid = [v for v in variants if eligible(row['body'], v)]
            if not valid:
                continue
            phrase = max((2 if v in low else 0) + (4 if v in low.splitlines()[0] else 0) for v in valid)
            overlap = max(len(set(terms(v)) & set(terms(low))) / max(1, len(set(terms(v)))) for v in valid)
            score = phrase + overlap + 1/(1+rank)
            positions = [low.find(t) for t in vocab if t in low]
            start = max(0, min(positions, default=0)-180)
            snippet = row['body'][start:start+1800]
            ranked.append((score, dict(row), snippet))
        ranked.sort(key=lambda r: (-r[0], r[1]['name'], int(r[1]['section'])))
        return ranked[:limit], variants
