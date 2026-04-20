from __future__ import annotations

import json
import subprocess
import threading
from collections import deque
from queue import Empty, Queue
from typing import Any

from ..settings import get_settings
from .observability import log_event

settings = get_settings()


class OpenClawMCPError(RuntimeError):
    pass


class OpenClawMCPClient:
    def __init__(self) -> None:
        self.process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._response_queue: Queue[dict[str, Any]] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._next_id = 1

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _build_command(self) -> list[str]:
        cmd = [settings.openclaw_mcp_command, 'mcp', 'serve']
        if settings.openclaw_mcp_url:
            cmd += ['--url', settings.openclaw_mcp_url]
        if settings.openclaw_mcp_token_file:
            cmd += ['--token-file', settings.openclaw_mcp_token_file]
        if settings.openclaw_mcp_password_file:
            cmd += ['--password-file', settings.openclaw_mcp_password_file]
        if settings.openclaw_mcp_claude_channel_mode:
            cmd += ['--claude-channel-mode', settings.openclaw_mcp_claude_channel_mode]
        return cmd

    def start(self) -> None:
        if self.process is not None:
            return
        import os
        env = os.environ.copy()
        if '/home/vboxuser/.local/bin' not in env.get('PATH', ''):
            env['PATH'] = f"/home/vboxuser/.local/bin:{env.get('PATH', '')}"
        
        self.process = subprocess.Popen(
            self._build_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()
        self._stderr_thread = threading.Thread(target=self._stderr_loop, daemon=True)
        self._stderr_thread.start()
        self._initialize()

    def close(self) -> None:
        if self.process is None:
            return
        try:
            if self.process.stdin:
                self.process.stdin.close()
        except Exception:
            pass
        process = self.process
        self.process = None
        try:
            process.terminate()
            process.wait(timeout=5)
        except Exception:
            process.kill()

    def _reader_loop(self) -> None:
        assert self.process and self.process.stdout
        for line in self.process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            # Handle MCP JSON-RPC
            if 'id' in payload:
                self._response_queue.put(payload)
            elif payload.get('method') == 'notifications/message':
                pass # Ignore raw notifications

    def _stderr_loop(self) -> None:
        assert self.process and self.process.stderr
        for raw_line in self.process.stderr:
            line = raw_line.strip()
            if not line:
                continue
            self._stderr_tail.append(line)
            log_event(30, 'openclaw_mcp_stderr', line=line)

    def _stderr_summary(self) -> str:
        if not self._stderr_tail:
            return 'no stderr captured'
        return ' | '.join(list(self._stderr_tail)[-5:])

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self.process is None or self.process.stdin is None:
            raise OpenClawMCPError('MCP process is not running')
        req_id = self._next_id
        self._next_id += 1
        payload = {'jsonrpc': '2.0', 'id': req_id, 'method': method, 'params': params or {}}
        self.process.stdin.write(json.dumps(payload, ensure_ascii=False) + '\n')
        self.process.stdin.flush()
        import time
        start_time = time.monotonic()
        while True:
            returncode = self.process.poll()
            if returncode is not None:
                raise OpenClawMCPError(f'MCP process exited with code {returncode} while waiting for {method}; stderr: {self._stderr_summary()}')
            try:
                queue_timeout = 15
                if method == 'tools/call' and params and params.get('name') == 'events_wait':
                    if isinstance(params.get('arguments', {}).get('timeoutSeconds'), (int, float)):
                        queue_timeout = params['arguments']['timeoutSeconds'] + 5
                
                # Check how much time is left relative to the calculated queue_timeout
                elapsed = time.monotonic() - start_time
                remaining = max(0.1, queue_timeout - elapsed)
                
                result = self._response_queue.get(timeout=remaining)
            except Empty as exc:
                if method == 'tools/call' and params and params.get('name') == 'events_wait':
                    return {'id': req_id, 'result': {'events': []}}
                raise OpenClawMCPError(f'Timeout waiting for MCP response to {method}; stderr: {self._stderr_summary()}') from exc
            if result.get('id') != req_id:
                # We pulled a response for a different request, loop again
                continue
            if 'error' in result:
                raise OpenClawMCPError(str(result['error']))
            return result.get('result', {})

    def _initialize(self) -> None:
        self._request('initialize', {
            'protocolVersion': '2024-11-05',
            'capabilities': {},
            'clientInfo': {'name': 'helpdesk-suite', 'version': '20.3.0'},
        })
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}) + '\n')
        self.process.stdin.flush()

    def _tool_call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        result = self._request('tools/call', {'name': name, 'arguments': arguments or {}})
        if isinstance(result, dict):
            if 'structuredContent' in result:
                return result['structuredContent']
            content = result.get('content')
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    if 'json' in first:
                        return first['json']
                    if 'text' in first:
                        try:
                            return json.loads(first['text'])
                        except Exception:
                            return first['text']
        return result

    def conversations_list(self, *, limit: int = 50, channel: str | None = None, include_last_message: bool = True) -> Any:
        args: dict[str, Any] = {'limit': limit, 'includeLastMessage': include_last_message}
        if channel:
            args['channel'] = channel
        return self._tool_call('conversations_list', args)

    def conversation_get(self, session_key: str) -> Any:
        return self._tool_call('conversation_get', {'session_key': session_key})

    def messages_read(self, session_key: str, *, limit: int = 50) -> Any:
        return self._tool_call('messages_read', {'session_key': session_key, 'limit': limit})

    def attachments_fetch(self, message_id: str, session_key: str | None = None) -> Any:
        args = {'messageId': message_id}
        if session_key is not None:
            args['sessionKey'] = session_key
        return self._tool_call('attachments_fetch', args)

    def events_poll(self, cursor: int | None = None) -> Any:
        args: dict[str, Any] = {}
        if cursor is not None:
            args['cursor'] = cursor
        return self._tool_call('events_poll', args)

    def events_wait(self, *, cursor: int | None = None, timeout_seconds: int | None = None) -> Any:
        args: dict[str, Any] = {}
        if cursor is not None:
            args['cursor'] = cursor
        if timeout_seconds is not None:
            args['timeoutSeconds'] = timeout_seconds
        return self._tool_call('events_wait', args)

    def messages_send(self, session_key: str, text: str) -> Any:
        return self._tool_call('messages_send', {'session_key': session_key, 'text': text})
