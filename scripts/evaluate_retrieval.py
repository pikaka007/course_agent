"""Small, authored regression corpus; no private course files or remote requests."""
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from course_agent.tools import Skills

CORPUS = {
    'rag.md': 'Retrieval Augmented Generation retrieves relevant passages and adds them to the prompt before generating an answer.',
    'mcp.md': 'Model Context Protocol connects an AI client to external tools and data. The client lists tools and invokes a tool through a server.',
    'tools.md': 'Tool use allows the model to request a function. The application validates arguments and executes the function.',
    'injection.md': 'Prompt injection embeds hostile instructions in untrusted documents. Document instructions are treated as data.',
    'cache.md': '缓存失效策略：文件修改时间或大小发生变化时，重新解析文件并更新索引。',
    'backup.md': '数据备份方案：用户可以导出会话，定期备份本地数据。备份文件也可能包含私人内容。',
    'errors.md': 'Software error correction fixes programming mistakes. This reading does not cover physics.',
    'budget.md': 'The class project budget is 120 yuan. The submission deadline is Friday.',
}
CASES = [
    ('RAG', 'rag.md'), ('什么是检索增强生成？', 'rag.md'),
    ('Please explain Retrieval Augmented Generation', 'rag.md'),
    ('MCP', 'mcp.md'), ('模型上下文协议是什么？', 'mcp.md'),
    ('What is Model Context Protocol?', 'mcp.md'),
    ('工具调用的作用', 'tools.md'), ('How does tool use work?', 'tools.md'),
    ('解释提示注入', 'injection.md'), ('什么是缓存失效策略？', 'cache.md'),
    ('数据备份方案', 'backup.md'), ('class project budget', 'budget.md'),
    ('quantum error correction', None), ('量子纠错阈值', None),
    ('unicornzzzz', None), ('What is the class project budget on Monday?', None),
]


def evaluate():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td); (root/'data').mkdir()
        for name, content in CORPUS.items():
            (root/'data'/name).write_text(content, encoding='utf-8')
        results = {}
        for label, method in [('v3_keyword', 'search_documents_legacy'), ('v4_fulltext', 'search_documents')]:
            skills = Skills(root/'data', root/'outputs')
            rows = []; start = time.perf_counter()
            for query, expected in CASES:
                hits = [m['filename'] for m in getattr(skills, method)(query, 3)['matches']]
                rank = hits.index(expected)+1 if expected in hits else None
                rows.append({'query':query, 'expected':expected, 'hits':hits, 'rank':rank,
                             'passed':bool(rank) if expected else not hits})
            positive = [r for r in rows if r['expected']]
            negative = [r for r in rows if not r['expected']]
            results[label] = {'hit_at_3':sum(bool(r['rank']) for r in positive)/len(positive),
                'mrr_at_3':sum(1/r['rank'] if r['rank'] else 0 for r in positive)/len(positive),
                'negative_accuracy':sum(r['passed'] for r in negative)/len(negative),
                'elapsed_ms':round((time.perf_counter()-start)*1000,2), 'cases':rows}
        return {'scope':'16 条自编回归问题：12 条有答案、4 条无答案。仅用于本项目回归，不能代表一般检索质量。耗时含本轮索引/缓存影响，不作公平性能比较。', 'results':results}


if __name__ == '__main__':
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))
