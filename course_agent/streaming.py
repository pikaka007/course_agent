"""Bounded SSE decoder; assemble tool arguments before executing anything."""
import json
import socket
import threading
import http.client
import urllib.request
import queue


class _CancelableConnection:
    def getresponse(self):
        # urllib closes its socket object after handing the response off. Retain
        # a duplicate descriptor so shutdown can still interrupt a blocked read.
        duplicate = None
        if self.sock:
            # SSLSocket.dup is intentionally unavailable. Duplicate the raw
            # transport handle solely for shutdown; never read TLS bytes here.
            raw = socket.socket(fileno=self.sock.fileno())
            try:
                duplicate = raw.dup()
            finally:
                raw.detach()
        try:
            response = super().getresponse()
            response._cancel_socket = duplicate
            return response
        except BaseException:
            if duplicate:
                duplicate.close()
            raise


class _HTTPConnection(_CancelableConnection, http.client.HTTPConnection):
    pass


class _HTTPSConnection(_CancelableConnection, http.client.HTTPSConnection):
    pass


class CancelableHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_HTTPConnection, req)


class CancelableHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_HTTPSConnection, req, context=self._context)


class Cancelled(RuntimeError):
    pass


def read_stream(response, cancel=None, on_delta=None, secret=''):
    calls, text, reasoning, pending = {}, '', '', ''
    receipt = {'completion_id': None, 'returned_model': None, 'usage': {}, 'http_status': getattr(response, 'status', 200)}
    stopped = threading.Event()
    inbox = queue.Queue(maxsize=64)
    def reader():
        # Windows shutdown may not wake an already blocked buffered read. Keep
        # that read off the orchestration thread; cancellation returns promptly.
        try:
            while not stopped.is_set():
                try:
                    item = response.readline(65537)
                except Exception as exc:
                    item = exc
                while not stopped.is_set():
                    try:
                        inbox.put(item, timeout=.05)
                        break
                    except queue.Full:
                        pass
                if not item or isinstance(item, Exception):
                    return
        finally:
            if hasattr(response, '_cancel_socket'):
                response.close()
                if response._cancel_socket:
                    response._cancel_socket.close()
    threading.Thread(target=reader, daemon=True).start()
    total, finished, done = 0, False, False
    def emit_pending(final=False):
        nonlocal pending
        if secret:
            pending = pending.replace(secret, '[REDACTED]')
        keep = 0 if final else len(secret)
        n = max(0, len(pending)-keep)
        if n and on_delta:
            on_delta(pending[:n])
        pending = pending[n:]
    try:
        while True:
            if cancel and cancel.is_set():
                raise Cancelled('已停止生成；服务端可能已产生用量。')
            try:
                line = inbox.get(timeout=.05)
            except queue.Empty:
                continue
            if isinstance(line, Exception):
                raise line
            total += len(line)
            if len(line) > 65536 or total > 4 * 1024 * 1024:
                raise ValueError('流式响应超过大小限制。')
            if not line:
                break
            if not line.startswith(b'data:'):
                continue
            raw = line[5:].strip()
            if raw == b'[DONE]':
                done = True
                break
            event = json.loads(raw.decode('utf-8'))
            if event.get('error'):
                raise ValueError('模型服务在流式响应中返回错误。')
            for name, key in [('completion_id', 'id'), ('returned_model', 'model'), ('usage', 'usage')]:
                if event.get(key) is not None:
                    receipt[name] = event[key]
            for choice in event.get('choices', []):
                if choice.get('index', 0) != 0:
                    continue
                finished = finished or choice.get('finish_reason') is not None
                if choice.get('finish_reason') in ('length', 'content_filter'):
                    raise ValueError('模型输出被截断或过滤，请缩短问题后重试。')
                delta = choice.get('delta', {})
                piece = delta.get('content') or ''
                text += piece
                pending += piece
                emit_pending()
                reasoning += delta.get('reasoning_content') or ''
                for item in delta.get('tool_calls') or []:
                    index = item.get('index')
                    if type(index) is not int or not 0 <= index < 8:
                        raise ValueError('流式工具索引无效。')
                    row = calls.setdefault(index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    row['id'] += item.get('id') or ''
                    for key in ('name', 'arguments'):
                        row['function'][key] += item.get('function', {}).get(key) or ''
        if cancel and cancel.is_set():
            raise Cancelled('已停止生成；未执行后续工具。')
        if not done or not finished:
            raise ValueError('流式连接提前结束，未把不完整回答标为成功。')
        emit_pending(True)
        message = {'role': 'assistant', 'content': text or None}
        if calls:
            message['tool_calls'] = [calls[n] for n in sorted(calls)]
        if reasoning:
            message['reasoning_content'] = reasoning
        return message, receipt
    except (OSError, ValueError):
        if cancel and cancel.is_set():
            raise Cancelled('已停止生成；服务端可能已产生用量。') from None
        raise
    finally:
        stopped.set()
        retained = getattr(response, '_cancel_socket', None)
        if retained:
            try:
                retained.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
