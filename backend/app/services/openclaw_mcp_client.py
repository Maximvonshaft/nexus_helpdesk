from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from queue import Empty, Queue
from typing import Any

from ..settings import get_settings
from .error_sanitizer import redact_sensitive_error_text
from .observability import log_event
from .tool_governance import ToolPolicyBlocked, classify_tool_type, enforce_tool_policy, record_tool_call

settings = get_settings()


class OpenClawMCPError(RuntimeError):
    pass


def local_mcp_cli_allowed() -> bool:
    """Return whether this process may start a local OpenClaw MCP CLI.

    In remote_gateway mode the remote bridge HTTP endpoint is the runtime boundary.
    If CLI fallback is disabled, accidentally starting `openclaw mcp serve` must fail
    before logging openclaw_mcp_start or spawning a subprocess.
    """
    return not (
        settings.openclaw_deployment_mode == "remote_gateway"
        and settings.openclaw_bridge_enabled
        and not settings.openclaw_cli_fallback_enabled
    )


def _safe_mcp_error_text(value: Any, *, code: str = "openclaw_mcp_error") -> str:
    return redact_sensitive_error_text(value, error_code=code, error_class="OpenClawMCPError") or "openclaw_mcp_error"


class OpenClawMCPClient:
    def __init__(self, *, actor_capabilities: list[str] | None = None) -> None:
        self.process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._response_queue: Queue[dict[str, Any]] = Queue()
        self._stderr_tail: deque[str] = deque(maxlen=50)
        self._next_id = 1
        self.actor_capabilities = actor_capabilities or []

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

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        extra_paths = [p for p in getattr(settings, 'openclaw_extra_paths', []) if p]
        if extra_paths:
            env['PATH'] = os.pathsep.join(extra_paths + [env.get('PATH', '')])
        return env

    def start(self) -> None:
        if self.process is not None:
            return
        if not local_mcp_cli_allowed():
            raise OpenClawMCPError(
                "local_openclaw_mcp_cli_disabled_in_remote_gateway_mode: "
                "use OPENCLAW_BRIDGE_URL remote bridge client; local CLI fallback is disabled"
            )
        log_event(
            20,
            'openclaw_mcp_start',
            command=settings.openclaw_mcp_command,
            url=settings.openclaw_mcp_url,
            token_file=bool(settings.openclaw_mcp_token_file),
            password_file=bool(settings.openclaw_mcp_password_file),
            extra_paths=settings.openclaw_extra_paths,
        )
        self.process = subprocess.Popen(
            self._build_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._build_env(),
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
            if 'id' in payload:
                self._response_queue.put(payload)
            elif payload.get('method') == 'notifications/message':
                pass

    def _stderr_loop(self) -> None:
        assert self.process and self.process.stderr
        for raw_line in self.process.stderr:
            line = raw_line.strip()
            if not line:
                continue
            safe_line = _safe_mcp_error_text(line, code="openclaw_mcp_stderr")
            self._stderr_tail.append(safe_line)
            log_event(30, 'openclaw_mcp_stderr', line=safe_line)

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
        start_time = time.monotonic()
        while True:
            returncode = self.process.poll()
            if returncode is not None:
                safe_error = _safe_mcp_error_text(
                    f'MCP process exited with code {returncode} while waiting for {method}; stderr: {self._stderr_summary()}',
                    code="openclaw_mcp_process_exited",
                )
                raise OpenClawMCPError(safe_error)
            try:
                queue_timeout = 15
                if method == 'tools/call' and params and params.get('name') == 'events_wait':
                    timeout_arg = params.get('arguments', {}).get('timeoutSeconds')
                    if isinstance(timeout_arg, (int, float)):
                        queue_timeout = timeout_arg + 5
                elapsed = time.monotonic() - start_time
                remaining = max(0.1, queue_timeout - elapsed)
                result = self._response_queue.get(timeout=remaining)
            except Empty as exc:
                if method == 'tools/call' and params and params.get('name') == 'events_wait':
                    return {'id': req_id, 'result': {'events': []}}
                safe_error = _safe_mcp_error_text(
                    f'Timeout waiting for MCP response to {method}; stderr: {self._stderr_summary()}',
                    code="openclaw_mcp_timeout",
                )
                raise OpenClawMCPError(safe_error) from exc
            if result.get('id') != req_id:
                continue
            if 'error' in result:
                raise OpenClawMCPError(_safe_mcp_error_text(result['error'], code="openclaw_mcp_result_error"))
            return result.get('result', {})

    def _initialize(self) -> None:
        self._request('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'helpdesk-suite', 'version': '20.3.0'}})
        assert self.process and self.process.stdin
        self.process.stdin.write(json.dumps({'jsonrpc': '2.0', 'method': 'notifications/initialized', 'params': {}}) + '\n')
        self.process.stdin.flush()

    def _tool_call_timeout_ms(self, name: str, arguments: dict[str, Any] | None = None) -> int:
        if name == 'events_wait':
            timeout_arg = (arguments or {}).get('timeoutSeconds')
            if isinstance(timeout_arg, (int, float)):
                return int((timeout_arg + 5) * 1000)
        return 15000

    def _tool_call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        args = arguments or {}
        started = time.monotonic()
        timeout_ms = self._tool_call_timeout_ms(name, args)
        tool_type = classify_tool_type(name)
        try:
            decision = enforce_tool_policy(tool_name=name, tool_type=tool_type, actor_capabilities=self.actor_capabilities)
        except ToolPolicyBlocked as exc:
            safe_error = _safe_mcp_error_text(exc.decision.reason_code, code="tool_policy_blocked")
            record_tool_call(
                tool_name=name,
                provider='openclaw_mcp',
                tool_type=tool_type,
                input_payload=args,
                output_payload=None,
                status='blocked',
                error_code='tool_policy_blocked',
                error_message=safe_error,
                elapsed_ms=0,
                timeout_ms=timeout_ms,
                policy_decision=exc.decision,
            )
            raise OpenClawMCPError(safe_error) from exc
        try:
            result = self._request('tools/call', {'name': name, 'arguments': args})
            if isinstance(result, dict):
                if 'structuredContent' in result:
                    parsed_result = result['structuredContent']
                else:
                    content = result.get('content')
                    parsed_result = result
                    if isinstance(content, list) and content:
                        first = content[0]
                        if isinstance(first, dict):
                            if 'json' in first:
                                parsed_result = first['json']
                            elif 'text' in first:
                                try:
                                    parsed_result = json.loads(first['text'])
                                except Exception:
                                    parsed_result = first['text']
                record_tool_call(
                    tool_name=name,
                    provider='openclaw_mcp',
                    tool_type=tool_type,
                    input_payload=args,
                    output_payload=parsed_result,
                    status='success',
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    timeout_ms=timeout_ms,
                    policy_decision=decision,
                )
                return parsed_result
            record_tool_call(
                tool_name=name,
                provider='openclaw_mcp',
                tool_type=tool_type,
                input_payload=args,
                output_payload=result,
                status='success',
                elapsed_ms=int((time.monotonic() - started) * 1000),
                timeout_ms=timeout_ms,
                policy_decision=decision,
            )
            return result
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            status = 'timeout' if 'timeout' in str(exc).lower() or 'timed out' in str(exc).lower() else 'failed'
            safe_error = _safe_mcp_error_text(str(exc), code=type(exc).__name__)
            record_tool_call(
                tool_name=name,
                provider='openclaw_mcp',
                tool_type=tool_type,
                input_payload=args,
                output_payload=None,
                status=status,
                error_code=type(exc).__name__,
                error_message=safe_error,
                elapsed_ms=elapsed_ms,
                timeout_ms=timeout_ms,
                policy_decision=decision,
            )
            raise

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
