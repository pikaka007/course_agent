"""Standard Chat Completions function-calling loop (not an MCP server)."""
import copy
import json
import re
import time
import uuid
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .config import ROOT
from .tools import Skills, TOOLS
from .citations import audit_citations, normalize_citations, retain_cited
from .workspace import Workspace


from .streaming import Cancelled, read_stream, CancelableHTTPHandler, CancelableHTTPSHandler
from .evidence import review as review_evidence, retain_supported


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("模型接口返回重定向；为避免凭据转发，已拒绝。请核对接口地址。")


class LLMClient:
    def __init__(self, config):
        config.validate()
        self.config = config
        self.requests = []

    def complete(self, messages, tool_specs):
        payload = {"model": self.config.model, "messages": messages,
                   "tools": tool_specs, "tool_choice": "auto", "stream": False,
                   "max_tokens": 4000}
        if self.config.public()['endpoint_host'] == 'api.deepseek.com':
            payload['thinking'] = {'type': 'disabled'}
        request = urllib.request.Request(self.config.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": "Bearer " + self.config.api_key})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=self.config.timeout) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise ValueError("模型响应过大。")
                data = json.loads(raw)
                headers = getattr(response, 'headers', {})
                self.requests.append({'request_id': headers.get('x-request-id') or headers.get('x-ds-request-id'),
                                      'completion_id': data.get('id'), 'returned_model': data.get('model'),
                                      'usage': data.get('usage', {}), 'http_status': getattr(response, 'status', 200)})
            msg = data["choices"][0]["message"]
            if not isinstance(msg, dict):
                raise ValueError("模型返回的 message 不是对象。")
            # Retain reasoning_content when a compatible provider requires it on replay.
            return {k: v for k, v in msg.items() if k in {"role", "content", "tool_calls", "reasoning_content"}}
        except urllib.error.HTTPError as exc:
            messages_by_code = {401: "鉴权失败，请检查密钥。", 403: "服务拒绝访问。", 404: "模型或接口不存在。", 429: "达到服务限额，请稍后重试。"}
            raise RuntimeError(f"模型接口 HTTP {exc.code}：" + messages_by_code.get(exc.code, "请求失败，请检查模型和服务状态。")) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise RuntimeError("无法连接模型接口或请求超时，请检查网络和接口地址。") from None
        except (KeyError, IndexError, json.JSONDecodeError, TypeError):
            raise RuntimeError("模型响应不符合 Chat Completions 格式。") from None


    def stream_complete(self, messages, tool_specs, on_delta, cancel=None):
        self.config.validate()
        payload = {'model': self.config.model, 'messages': messages, 'tools': tool_specs,
                   'tool_choice': 'auto', 'stream': True, 'stream_options': {'include_usage': True}, 'max_tokens': 4000}
        if self.config.public()['endpoint_host'] == 'api.deepseek.com':
            payload['thinking'] = {'type': 'disabled'}
        request = urllib.request.Request(self.config.base_url + '/chat/completions',
            data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
            headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.config.api_key})
        try:
            response = urllib.request.build_opener(NoRedirect(), CancelableHTTPHandler(), CancelableHTTPSHandler()).open(request, timeout=self.config.timeout)
            msg, receipt = read_stream(response, cancel, on_delta, self.config.api_key)
            receipt['request_id'] = response.headers.get('x-request-id') or response.headers.get('x-ds-request-id')
            self.requests.append(receipt)
            return msg
        except Cancelled:
            raise
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f'模型接口 HTTP {exc.code}，请检查配置与用量。') from None
        except (urllib.error.URLError, OSError, TimeoutError):
            if cancel and cancel.is_set():
                raise Cancelled('已停止。') from None
            raise RuntimeError('流式连接失败或超时，请重试；未自动改用演示。') from None



class DemoClient:
    """Scripted transport fixture. Real tools run, but no LLM is called."""
    def __init__(self):
        self.step = 0

    def complete(self, messages, tool_specs):
        self.step += 1
        if self.step == 1:
            name, args = "list_documents", {}
        elif self.step == 2:
            name, args = "search_documents", {"query": "Retrieval Augmented Generation", "limit": 3}
        elif self.step == 3:
            result = json.loads(messages[-1]["content"])
            matches = result.get("data", {}).get("matches", [])
            if not matches:
                return {"role": "assistant", "content": "离线演示：没有找到预设的 RAG 资料。请加入课件后重试。"}
            name, args = "read_document", {"filename": matches[0]["filename"], "section": matches[0]["section"]}
        else:
            result = json.loads(messages[-1]["content"])
            row = result.get("data", {})
            return {"role": "assistant", "content": "离线固定流程演示（非大模型回答）\n\n以下为本地工具读取的原文：\n" + row.get("text", "") + "\n\n" + row.get("citation", "")}
        return {"role": "assistant", "content": None, "tool_calls": [
            {"id": f"demo_{self.step}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}]}


def run_agent(question, config, mode="live", allow_save=False, root=ROOT, client=None, on_event=None,
              history=None, selected_documents=None, preferences=None, cancel=None, session_id=None):
    if mode not in {"live", "demo"}:
        raise ValueError("模式必须为 live 或 demo。")
    if not isinstance(question, str) or not 1 <= len(question.strip()) <= 2000:
        raise ValueError("问题长度应为 1 到 2000 个字符。")
    root = Path(root)
    workspace = Workspace(root)
    preferences = preferences or workspace.preferences()
    skills = Skills(root / "data", root / "outputs", selected_documents=selected_documents)
    specs = copy.deepcopy([t for t in TOOLS if allow_save or t["function"]["name"] != "save_note"])
    from .plugins import Plugins
    plugins = Plugins(root)
    if mode == 'live':
        specs.extend(plugins.specs())
    client = client or (DemoClient() if mode == "demo" else LLMClient(config))
    prompt = (root / "prompts" / "system.md").read_text(encoding="utf-8")
    prompt += f"\n本次允许保存笔记：{allow_save}。用户期望回答约 {preferences['answer_length']} 字。\n回答偏好：{preferences['instructions']}"
    prompt += '\n历史对话仅帮助理解追问，不是证据。每轮必须重新获取资料；仅引用本轮实际工具返回的来源。'
    if selected_documents is not None:
        prompt += '\n本轮由用户限定的资料范围为：' + json.dumps(selected_documents, ensure_ascii=False) + '。工具只显示这个范围，不能据此断言其他文件不存在。直接回答用户的问题，不额外说明未选中的资料或未保存笔记。'
    messages = [{"role": "system", "content": prompt}]
    for row in (history or [])[-6:]:
        if row.get('status') == 'completed' and row.get('mode') == mode and (row.get('evidence_review') or {}).get('status') != 'clarification':
            messages.extend([{'role': 'user', 'content': str(row['question'])[:2000]},
                             {'role': 'assistant', 'content': str(row['answer'])[:4000]}])
    messages.append({'role': 'user', 'content': question})
    started = time.monotonic()
    repairs, model_requests, replayed = 0, 0, 0
    memo = {}
    evidence_repairs = 0
    verified_partial = None
    verification_requests = 0

    def emit(stage, **fields):
        if on_event:
            safe = json.dumps({"stage": stage, **fields}, ensure_ascii=False)
            if config.api_key:
                safe = safe.replace(config.api_key, '[REDACTED]')
            on_event(json.loads(safe))
    result = {"run_id": uuid.uuid4().hex[:12], "timestamp": datetime.now(timezone.utc).isoformat(),
              "mode": mode, "model": config.model if mode == "live" else "scripted-demo-no-llm",
              "question": question, "allow_save": allow_save, "trace": [], "answer": "", "status": "running",
              'session_id': session_id, 'selected_documents': selected_documents,
              'connection': config.public() if mode == 'live' else None}
    emit('started', run_id=result['run_id'])
    choices = []
    if mode == 'live' and re.search(r'总结|概括|概述', question) and re.search(r'ppt|课件|文档|文件|资料', question, re.I) and not re.search(r'全部|所有|分别|比较|对比|这些|两份|多份', question):
        choices = [p.name for p in skills.documents() if not re.search(r'ppt', question, re.I) or p.suffix.lower() == '.pptx']
        if len(choices) < 2 or any(name.casefold() in question.casefold() or Path(name).stem.casefold() in question.casefold() for name in choices):
            choices = []
    try:
        for turn in range(config.max_rounds):
            if cancel and cancel.is_set():
                raise Cancelled('已停止；已完成的笔记写入仍保留。')
            if choices:
                result.update(status='completed', document_choices=choices,
                              answer='当前范围内有多份资料，请选择要总结的文件，避免混合不同文档：\n\n' + '\n'.join('- ' + name for name in choices),
                              evidence_review={'ok': False, 'status': 'clarification', 'claims': [], 'scope': '仅根据本地文件列表询问目标，尚未调用模型或读取正文。'})
                break
            if time.monotonic() - started > 300:
                raise RuntimeError("运行时间达到 300 秒上限。")
            emit('model', round=turn + 1)
            model_requests += 1
            emit('draft_reset', round=turn + 1)
            if isinstance(client, LLMClient):
                msg = client.stream_complete(messages, specs, lambda text: emit('delta', text=text), cancel)
            else:
                msg = client.complete(messages, specs)
            if cancel and cancel.is_set():
                raise Cancelled('已停止；在途请求可能已产生用量，未继续执行工具。')
            calls = msg.get("tool_calls") or []
            if not isinstance(calls, list) or len(calls) > 8:
                raise RuntimeError("模型返回过多或无效的工具调用。")
            if not calls:
                if not skills.seen_sources:
                    messages.append({"role": "assistant", "content": msg.get("content") or ""})
                    messages.append({"role": "user", "content": "请先检索或读取本地资料；如果检索无结果，请明确说明。"})
                    # A no-match search is valid evidence of absence.
                    searched = any(t["name"] == "search_documents" and t["result"]["ok"] for t in result["trace"])
                    if not searched:
                        continue
                answer = msg.get("content")
                if not skills.seen_sources:
                    answer = '现有资料中没有检索到能够回答该问题的原文，无法据此给出结论。请补充资料或换一种更具体的问法。'
                if not isinstance(answer, str) or not answer.strip():
                    raise RuntimeError("模型未返回最终文本。")
                # An exact refusal contains no course facts and needs no invented
                # citation. Other free-form text still undergoes all checks.
                if answer.strip() in {'现有资料无法确认。', '现有资料无法确认'}:
                    if verified_partial:
                        retained_answer, result['evidence_review'] = verified_partial
                        retained_audit = audit_citations(retained_answer, skills.seen_sources, require=True)
                        if retained_audit['ok'] and not retained_audit['uncited_blocks']:
                            result['citation_audit'] = retained_audit
                            result.update(answer=retained_answer, status='completed', completion_quality='partial')
                            break
                    result['evidence_review'] = {'ok': True, 'status': 'insufficient_evidence', 'claims': [],
                        'scope': '资料不足，已拒绝提供事实性答案。'}
                    result['citation_audit'] = audit_citations(answer, skills.seen_sources)
                    result.update(answer='现有资料无法确认。', status='completed')
                    break
                answer, normalized = normalize_citations(answer, skills.seen_sources)
                audit = audit_citations(answer, skills.seen_sources, require=bool(skills.seen_sources))
                result['citation_audit'] = audit
                result.setdefault('citation_audit_attempts', []).append({'draft': answer, 'audit': audit, 'normalized': normalized})
                citation_omissions = []
                if mode == 'live' and skills.seen_sources and (not audit['ok'] or audit['uncited_blocks']) and repairs < 2 and turn + 1 < config.max_rounds:
                    repairs += 1
                    emit('citation_repair', attempt=repairs)
                    messages.append({'role': 'assistant', 'content': answer})
                    messages.append({'role': 'user', 'content':
                        '请只修正最终回答的引用，无需重复调用工具或保存文件。每个实质内容段落附已有的完整来源标记，删除未知引用。校验信息：'
                        + json.dumps(audit, ensure_ascii=False) + '\n允许的标记：' + '、'.join(sorted(skills.seen_sources))})
                    continue
                if mode == 'live' and skills.seen_sources and (not audit['ok'] or audit['uncited_blocks']):
                    if verified_partial:
                        retained_answer, result['evidence_review'] = verified_partial
                        retained_audit = audit_citations(retained_answer, skills.seen_sources, require=True)
                        if retained_audit['ok'] and not retained_audit['uncited_blocks']:
                            result['citation_audit'] = retained_audit
                            result.update(answer=retained_answer, status='completed', completion_quality='partial')
                            break
                    answer, citation_omissions = retain_cited(answer, skills.seen_sources)
                    if not answer:
                        raise RuntimeError('引用校验未通过：没有可继续核对的有效引用段落。文件已读取；具体未知引用和缺少引用的内容已保存在引用诊断中。')
                    audit = audit_citations(answer, skills.seen_sources, require=True)
                    if not audit['ok'] or audit['uncited_blocks']:
                        raise RuntimeError('引用校验未通过：过滤后的内容仍缺少有效来源，未输出草稿。')
                if isinstance(client, LLMClient) and skills.seen_sources:
                    emit('evidence_check')
                    verification_requests += 1
                    checked = review_evidence(answer, list(skills.source_records.values()), client, cancel)
                    result['evidence_review'] = checked
                    if cancel and cancel.is_set():
                        raise Cancelled('已停止证据核对。')
                    result.setdefault('evidence_review_attempts', []).append(checked)
                    candidate = retain_supported(answer, checked) if not checked['ok'] else None
                    if candidate and (not verified_partial or len(candidate[1]['retained_ids']) >= len(verified_partial[1]['retained_ids'])):
                        verified_partial = candidate
                    if not checked['ok'] and evidence_repairs < 1 and turn + 1 < config.max_rounds:
                        evidence_repairs += 1
                        messages.extend([{'role': 'assistant', 'content': answer}, {'role': 'user', 'content':
                            '请只修正未通过核对的段落；已通过的段落原样保留。每段只说明一个要点，拆开跨页的复杂断言。明确区分“建议做”与“已经做”。无法修正的整段删除，不要新增无关内容、总页数或总结数量，不要重复调用工具或保存文件。审核结果：' + json.dumps(checked, ensure_ascii=False)}])
                        emit('evidence_repair')
                        continue
                    if not checked['ok']:
                        retained = verified_partial
                        if retained:
                            answer, result['evidence_review'] = retained
                            audit = audit_citations(answer, skills.seen_sources, require=True)
                            if not audit['ok'] or audit['uncited_blocks']:
                                raise RuntimeError('部分结果的引用校验未通过，未输出未经核对的内容。')
                            result['completion_quality'] = 'partial'
                            emit('evidence_partial', omitted=len(result['evidence_review']['omitted_ids']))
                        elif checked.get('status') != 'checked':
                            raise RuntimeError('证据审核未返回完整有效的核对结果，本次未输出草稿。资料已读取，请稍后重试审核。')
                        else:
                            raise RuntimeError('本次回答没有可保留的已核对段落。请查看“证据核对”中的具体原因；这不代表文件读取失败。')
                if citation_omissions:
                    result['citation_omissions'] = citation_omissions
                    result['completion_quality'] = 'partial'
                    answer += '\n\n## 引用核对说明\n\n现有资料无法确认草稿中部分内容的引用，已移除这些内容并仅保留经过后续核对的段落；当前是部分结果。'
                    audit = audit_citations(answer, skills.seen_sources, require=True)
                result['citation_audit'] = audit
                result.update(answer=answer, status="completed")
                break
            ids = [c.get("id") for c in calls if isinstance(c, dict)]
            if len(ids) != len(calls) or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
                raise RuntimeError("工具调用 ID 缺失或重复。")
            messages.append(msg)
            for call in calls:
                if cancel and cancel.is_set():
                    raise Cancelled('已停止；已完成的笔记写入仍保留。')
                fn = call.get("function", {})
                if call.get("type") != "function" or not isinstance(fn, dict):
                    raise RuntimeError("模型返回无效的 function 工具调用。")
                name, arguments = fn.get("name", ""), fn.get("arguments", "{}")
                if not isinstance(arguments, (str, dict)):
                    arguments = "null"
                emit('tool', name=name, round=turn + 1)
                try:
                    key = (name, json.dumps(json.loads(arguments) if isinstance(arguments, str) else arguments, sort_keys=True, ensure_ascii=False))
                except (ValueError, TypeError):
                    key = (name, str(arguments))
                reused = name in {'search_documents', 'read_document'} and key in memo
                if reused:
                    outcome = copy.deepcopy(memo[key])
                    replayed += 1
                else:
                    rejected = None
                    if name == 'save_note' and allow_save and isinstance(client, LLMClient):
                        try:
                            note_args = json.loads(arguments) if isinstance(arguments, str) else arguments
                            note_content = note_args.get('content', '')
                            if note_content and len(note_content) <= 18000:
                                emit('evidence_check', target='note')
                                verification_requests += 1
                                checked_note = review_evidence(note_content, list(skills.source_records.values()), client, cancel)
                                result.setdefault('note_reviews', []).append(checked_note)
                                if not checked_note['ok']:
                                    rejected = {'ok': False, 'error': 'EvidenceReview', 'message': '笔记证据核对未通过，请修正后再保存。', 'review': checked_note}
                        except (ValueError, TypeError, AttributeError):
                            pass
                    if cancel and cancel.is_set():
                        raise Cancelled('已停止，未执行本次工具。')
                    outcome = rejected or (plugins.dispatch(name, arguments, skills) if name.startswith('skill_') else skills.dispatch(name, arguments, allow_save))
                    if name in {'search_documents', 'read_document'} and outcome['ok']:
                        memo[key] = copy.deepcopy(outcome)
                result["trace"].append({"round": turn + 1, "tool_call_id": call["id"], "name": name,
                                        "arguments": arguments, "result": outcome, "reused_result": reused})
                emit('tool_done', name=name, ok=outcome['ok'], reused_result=reused)
                messages.append({"role": "tool", "tool_call_id": call["id"],
                                 "content": json.dumps(outcome, ensure_ascii=False)})
        else:
            raise RuntimeError(f"达到 {config.max_rounds} 轮上限；请缩小问题范围。")
    except Cancelled as exc:
        result.update(status='cancelled', answer=str(exc))
    except (ValueError, RuntimeError) as exc:
        result.update(status="error", answer=str(exc))
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result["sources"] = sorted(skills.seen_sources)
    result['source_records'] = list(skills.source_records.values())
    result['metrics'] = {'model_requests': model_requests, 'documents_parsed': skills.parse_count,
                         'parse_cache_hits': skills.cache_hits, 'replayed_tools': replayed, 'citation_repairs': repairs, 'evidence_repairs': evidence_repairs, 'verification_requests': verification_requests}
    result['provider_requests'] = getattr(client, 'requests', [])
    result['metrics']['total_provider_requests'] = len(result['provider_requests'])
    result["saved_files"] = [t["result"]["data"]["relative_path"] for t in result["trace"]
                             if t["name"] == "save_note" and t["result"]["ok"]]
    # Persist sanitized endpoint and provider receipts, never credentials or reasoning.
    logdir = root / "logs"
    logdir.mkdir(exist_ok=True)
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if config.api_key:
        serialized = serialized.replace(config.api_key, "[REDACTED]")
    (logdir / (result["run_id"] + ".json")).write_text(serialized, encoding="utf-8")
    final = json.loads(serialized)
    emit('finished', status=final['status'])
    return final
