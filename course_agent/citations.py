"""Deterministic source and paragraph coverage checks, not semantic fact checking."""
import re

BRACKETS = re.compile(r"\[[^\]\n]{1,220}\]")


def normalize_citations(text, known_sources):
    """Repair typography only, when a complete marker maps uniquely to a source."""
    def key(marker):
        return re.sub(r'\s+', '', marker.replace('【', '[').replace('】', ']'))
    candidates = {}
    for source in known_sources:
        candidates.setdefault(key(source), []).append(source)
    changes = []
    def replace(match):
        old = match.group()
        matches = candidates.get(key(old), [])
        if len(matches) == 1 and old != matches[0]:
            changes.append({'from': old, 'to': matches[0]})
            return matches[0]
        return old
    return re.sub(r'\[[^\]\n]{1,220}\]|【[^】\n]{1,220}】', replace, text), changes


def retain_cited(text, known_sources):
    """Keep whole cited lines for semantic review, never invent a missing source."""
    known = set(known_sources)
    kept, omitted = [], []
    fenced = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('```'):
            fenced = not fenced
            continue
        if not line:
            continue
        refs = set(BRACKETS.findall(line))
        if not fenced and refs and refs <= known and re.sub(BRACKETS, '', line).strip(' #*:-'):
            kept.append(re.sub(r'^\s*\d+[.)、]\s*', '', line))
        else:
            omitted.append(line)
    return '\n\n'.join(kept), omitted


def audit_citations(text, known_sources, require=False):
    references = sorted(set(BRACKETS.findall(text)))
    unknown = [c for c in references if c not in known_sources]
    valid = [c for c in references if c in known_sources]
    uncovered = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or re.fullmatch(r'[-*_\s]+', line):
            continue
        # Operational statements are not claims about course content.
        if re.match(r'^(已保存|保存结果|输出路径|本次未保存|现有资料无法确认|离线固定流程|以下为本地工具)', line):
            continue
        if len(re.sub(r'\s', '', line)) >= 20 and not any(c in line for c in valid):
            uncovered.append(line[:160])
    missing = require and not valid
    return {"ok": not unknown and not missing, "citations": valid,
            "unknown_citations": unknown, "missing_citations": missing,
            "uncited_blocks": uncovered,
            "scope": "仅核对来源标记和段落覆盖，不判定结论是否被原文语义支持。"}
