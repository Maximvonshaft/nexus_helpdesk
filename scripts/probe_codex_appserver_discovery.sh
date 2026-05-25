#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if command -v cygpath >/dev/null 2>&1; then
  ROOT_DIR="$(cygpath -w "$ROOT_DIR")"
fi

REPORT_PATH="${CODEX_APPSERVER_DISCOVERY_REPORT:-${ROOT_DIR}/docs/engineering/codex_appserver_runtime_v3_discovery_report.md}"
ARTIFACT_DIR="${CODEX_APPSERVER_DISCOVERY_ARTIFACT_DIR:-${ROOT_DIR}/probe_reports/codex_appserver_runtime_v3_discovery}"

PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

mkdir -p "$(dirname "$REPORT_PATH")" "$ARTIFACT_DIR"

"$PYTHON_BIN" - "$ROOT_DIR" "$REPORT_PATH" "$ARTIFACT_DIR" <<'PY'
from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal


ROOT_DIR = Path(sys.argv[1])
REPORT_PATH = Path(sys.argv[2])
ARTIFACT_DIR = Path(sys.argv[3])

DUMMY_ACCESS_TOKEN = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJpc3MiOiJuZXh1cy1kdW1teSIsInN1YiI6ImR1bW15IiwiZXhwIjoxODkwMDAwMDAwfQ."
    "invalidsignature"
)
DUMMY_ACCOUNT_ID = "dummy@example.invalid"
SECRET_FIELD_RE = re.compile(
    r"(?i)(accessToken|refreshToken|Authorization|Bearer|api[_-]?key|OPENAI_API_KEY|CODEX_API_KEY|OPENAI_ACCESS_TOKEN|CODEX_ACCESS_TOKEN)"
)
JWT_RE = re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{6,}\b")
CLEARED_AUTH_ENV_VARS = [
    "CODEX_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_ACCESS_TOKEN",
    "CODEX_ACCESS_TOKEN",
    "OPENCLAW_HOME",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_short(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def read_token_from_env() -> tuple[str | None, str | None]:
    token_file = (os.getenv("NEXUS_CODEX_ACCESS_TOKEN_FILE") or os.getenv("CODEX_APPSERVER_ACCESS_TOKEN_FILE") or "").strip()
    if token_file:
        try:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return None, "token_file_unreadable"
        return (token or None), "token_file"
    token = (os.getenv("NEXUS_CODEX_ACCESS_TOKEN") or os.getenv("CODEX_APPSERVER_ACCESS_TOKEN") or "").strip()
    return (token or None), "env" if token else None


VALID_ACCESS_TOKEN, VALID_TOKEN_SOURCE = read_token_from_env()
VALID_ACCOUNT_ID = (os.getenv("NEXUS_CODEX_ACCOUNT_ID") or os.getenv("CODEX_APPSERVER_ACCOUNT_ID") or "").strip()
VALID_PLAN_TYPE = (os.getenv("NEXUS_CODEX_PLAN_TYPE") or os.getenv("CODEX_APPSERVER_PLAN_TYPE") or "").strip() or None
MODEL = (os.getenv("CODEX_APPSERVER_MODEL") or "gpt-5.5").strip()
PROBE_TIMEOUT_SECONDS = max(3.0, min(float(os.getenv("CODEX_APPSERVER_DISCOVERY_TIMEOUT_SECONDS", "30")), 120.0))


def default_valid_token_matrix() -> dict[str, str]:
    credential_available = "yes" if VALID_ACCESS_TOKEN and VALID_ACCOUNT_ID else "no"
    return {
        "credential_available": credential_available,
        "login_start": "pending",
        "account_read": "pending",
        "model_list": "pending",
        "thread_start": "pending",
        "turn_start": "pending",
        "terminal_state_observed": "pending",
        "assistant_text_extraction_path": "pending",
        "strict_json_parse": "pending",
    }


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"accesstoken", "access_token", "refreshtoken", "refresh_token", "authorization", "apikey", "api_key"}:
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = redact(item)
        return redacted
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        out = value
        for secret in [DUMMY_ACCESS_TOKEN, VALID_ACCESS_TOKEN or ""]:
            if secret:
                out = out.replace(secret, "<redacted-token>")
        out = re.sub(r"(?i)(Bearer\s+)[^\s\"']+", r"\1<redacted-token>", out)
        out = re.sub(r"(?i)(accessToken|refreshToken|apiKey)([\"']?\s*[:=]\s*[\"']?)[^,\"'\s]+", r"\1\2<redacted-token>", out)
        out = JWT_RE.sub("<redacted-jwt>", out)
        return out
    return value


def safe_json(value: Any) -> str:
    return json.dumps(redact(value), ensure_ascii=False, sort_keys=True)


def run_command(args: list[str], timeout: float = 5.0) -> tuple[int | None, str, str]:
    try:
        proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout, shell=False)
        return proc.returncode, redact(proc.stdout), redact(proc.stderr)
    except FileNotFoundError as exc:
        return None, "", str(exc)
    except subprocess.TimeoutExpired as exc:
        return None, redact(exc.stdout or ""), "timeout"
    except OSError as exc:
        return None, "", str(exc)


def command_candidates() -> list[list[str]]:
    raw = (os.getenv("CODEX_APPSERVER_COMMAND") or "").strip()
    if raw:
        return [shlex.split(raw)]
    names = []
    for name in ("codex.cmd", "codex.exe", "codex"):
        found = shutil.which(name)
        if found and found not in names:
            names.append(found)
    return [[name] for name in names]


@dataclass
class RpcResponse:
    ok: bool
    response: dict[str, Any] | None = None
    error: str | None = None
    notifications: list[dict[str, Any]] = field(default_factory=list)


class JsonRpcProcess:
    def __init__(self, base_cmd: list[str], *, mode: Literal["isolated", "local_profile"] = "isolated"):
        self.cmd = base_cmd + ["app-server", "--listen", "stdio://"]
        extra = (os.getenv("CODEX_APPSERVER_STARTUP_ARGS") or "").strip()
        if extra:
            self.cmd.extend(shlex.split(extra))
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        for name in CLEARED_AUTH_ENV_VARS:
            env.pop(name, None)
        self.mode = mode
        self.cleared_auth_env_vars = list(CLEARED_AUTH_ENV_VARS)
        if mode == "isolated":
            codex_home = ARTIFACT_DIR / "isolated_codex_home"
            native_home = ARTIFACT_DIR / "isolated_home"
            codex_home.mkdir(parents=True, exist_ok=True)
            native_home.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(codex_home)
            env["HOME"] = str(native_home)
            env["XDG_CONFIG_HOME"] = str(codex_home)
        else:
            codex_home = Path(env.get("CODEX_HOME") or (Path.home() / ".codex"))
            native_home = Path(env.get("HOME") or str(Path.home()))
        self.proc = subprocess.Popen(
            self.cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creationflags,
            env=env,
        )
        self.codex_home = codex_home
        self.native_home = native_home
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.raw_stdout: list[str] = []
        self.raw_stderr: list[str] = []
        self.notifications: list[dict[str, Any]] = []
        self.notification_handlers: list[Any] = []
        self.notification_lock = threading.Lock()
        for name, stream in (("stdout", self.proc.stdout), ("stderr", self.proc.stderr)):
            thread = threading.Thread(target=self._reader, args=(name, stream), daemon=True)
            thread.start()

    def _reader(self, name: str, stream: Any) -> None:
        while True:
            line = stream.readline()
            if line == "":
                break
            if name == "stdout":
                self.raw_stdout.append(line)
            else:
                self.raw_stderr.append(line)
            self.messages.put((name, line.rstrip("\n")))

    def request(self, request_id: int, method: str, params: Any, timeout: float = PROBE_TIMEOUT_SECONDS) -> RpcResponse:
        if not self.proc.stdin:
            return RpcResponse(False, error="stdin_unavailable")
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        self.proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        notifications: list[dict[str, Any]] = []
        last_stderr = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                return RpcResponse(False, error=f"appserver_exited_{self.proc.returncode}; stderr={redact(last_stderr)[:240]}")
            try:
                source, line = self.messages.get(timeout=0.2)
            except queue.Empty:
                continue
            if source == "stderr":
                last_stderr = line
                continue
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError:
                continue
            if decoded.get("id") == request_id:
                return RpcResponse(True, response=decoded, notifications=notifications)
            if decoded.get("method") and "id" in decoded:
                self.respond_to_server_request(decoded)
                notifications.append(decoded)
                self.record_notification(decoded)
                continue
            notifications.append(decoded)
            self.record_notification(decoded)
        return RpcResponse(False, error=f"{method}_timeout", notifications=notifications)

    def add_notification_handler(self, handler: Any, *, replay_existing: bool = True) -> Any:
        with self.notification_lock:
            self.notification_handlers.append(handler)
            existing = list(self.notifications) if replay_existing else []
        for notification in existing:
            handler(notification)

        def remove() -> None:
            with self.notification_lock:
                self.notification_handlers = [item for item in self.notification_handlers if item is not handler]

        return remove

    def record_notification(self, notification: dict[str, Any]) -> None:
        if not isinstance(notification, dict) or not notification.get("method"):
            return
        with self.notification_lock:
            self.notifications.append(notification)
            if len(self.notifications) > 500:
                self.notifications = self.notifications[-500:]
            handlers = list(self.notification_handlers)
        for handler in handlers:
            try:
                handler(notification)
            except Exception:
                continue

    def respond_to_server_request(self, request: dict[str, Any]) -> None:
        if not self.proc.stdin:
            return
        request_id = request.get("id")
        method = str(request.get("method") or "")
        if method == "account/chatgptAuthTokens/refresh":
            response = {
                "id": request_id,
                "error": {"message": "nexus discovery does not provide refresh tokens"},
            }
        elif method.endswith("/requestApproval"):
            response = {"id": request_id, "result": {"decision": "decline"}}
        elif method == "item/permissions/requestApproval":
            response = {"id": request_id, "result": {"permissions": {}, "scope": "turn"}}
        elif method == "item/tool/requestUserInput":
            response = {"id": request_id, "result": {"answers": {}}}
        else:
            response = {"id": request_id, "result": {}}
        try:
            self.proc.stdin.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            self.proc.stdin.flush()
        except OSError:
            return

    def stop(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass

    def redaction_findings(self) -> list[str]:
        findings: list[str] = []
        for stream_name, lines in (("stdout", self.raw_stdout), ("stderr", self.raw_stderr)):
            rendered = "".join(lines)
            for secret_name, secret in (("dummy_access_token", DUMMY_ACCESS_TOKEN), ("valid_access_token", VALID_ACCESS_TOKEN or "")):
                if secret and secret in rendered:
                    findings.append(f"{stream_name}_contains_{secret_name}")
            if SECRET_FIELD_RE.search(rendered):
                findings.append(f"{stream_name}_contains_secret_keyword")
            if JWT_RE.search(rendered):
                findings.append(f"{stream_name}_contains_jwt_like_material")
        return sorted(set(findings))


def response_error_code(resp: dict[str, Any] | None) -> str | None:
    if not resp:
        return None
    err = resp.get("error")
    if isinstance(err, dict):
        for key in ("code", "message"):
            if key in err:
                return str(err[key])[:120]
    return None


def extract_assistant_text(obj: Any) -> tuple[str | None, str | None]:
    if isinstance(obj, dict):
        if obj.get("type") == "agentMessage" and isinstance(obj.get("text"), str) and obj["text"].strip():
            return obj["text"].strip(), "thread.turns[].items[type=agentMessage].text"
        if isinstance(obj.get("aggregatedOutput"), str) and obj["aggregatedOutput"].strip():
            return obj["aggregatedOutput"].strip(), "aggregatedOutput"
        for key, value in obj.items():
            found, path = extract_assistant_text(value)
            if found:
                return found, path
    elif isinstance(obj, list):
        for value in obj:
            found, path = extract_assistant_text(value)
            if found:
                return found, path
    return None, None


def rpc_result_ok(response: RpcResponse) -> bool:
    return bool(response.ok and response.response and "result" in response.response and "error" not in response.response)


def rpc_result(response: RpcResponse) -> Any:
    if response.response and isinstance(response.response, dict):
        return response.response.get("result")
    return None


def rpc_error(response: RpcResponse) -> str | None:
    return response.error or response_error_code(response.response)


def safe_rpc_summary(response: RpcResponse) -> dict[str, Any]:
    return {
        "ok": rpc_result_ok(response),
        "response": redact(response.response),
        "error": rpc_error(response),
        "notifications": redact(response.notifications[-10:]),
    }


def thread_start_params() -> dict[str, Any]:
    return {
        "model": MODEL,
        "cwd": str(ROOT_DIR),
        "approvalPolicy": "never",
        "sandbox": "read-only",
        "developerInstructions": "Return strict JSON only. Do not use tools, shell, browser, files, or markdown.",
        "dynamicTools": [],
        "ephemeral": True,
        "experimentalRawEvents": False,
        "persistExtendedHistory": False,
    }


def turn_start_params(thread_id: str) -> dict[str, Any]:
    prompt = (
        'Reply with JSON only: {"reply":"probe-ok","intent":"other","tracking_number":null,'
        '"handoff_required":false,"handoff_reason":null,"recommended_agent_action":null}'
    )
    return {
        "threadId": thread_id,
        "input": [{"type": "text", "text": prompt, "text_elements": []}],
        "approvalPolicy": "never",
        "dynamicTools": [],
        "sandboxPolicy": {"type": "readOnly", "access": {"type": "fullAccess"}, "networkAccess": False},
        "model": MODEL,
        "outputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "reply",
                "intent",
                "tracking_number",
                "handoff_required",
                "handoff_reason",
                "recommended_agent_action",
            ],
            "properties": {
                "reply": {"type": "string"},
                "intent": {"enum": ["other"]},
                "tracking_number": {"type": ["string", "null"]},
                "handoff_required": {"type": "boolean"},
                "handoff_reason": {"type": ["string", "null"]},
                "recommended_agent_action": {"type": ["string", "null"]},
            },
        },
    }


def extract_thread_id(response: RpcResponse) -> str | None:
    result = rpc_result(response)
    if not isinstance(result, dict):
        return None
    thread = result.get("thread")
    if not isinstance(thread, dict):
        return None
    value = thread.get("id")
    return value if isinstance(value, str) and value else None


def extract_turn_id(response: RpcResponse) -> str | None:
    result = rpc_result(response)
    if not isinstance(result, dict):
        return None
    turn = result.get("turn")
    if not isinstance(turn, dict):
        return None
    value = turn.get("id")
    return value if isinstance(value, str) and value else None


def cleanup_ephemeral_turn(client: JsonRpcProcess, *, thread_id: str | None, turn_id: str | None, request_base: int, should_interrupt: bool) -> dict[str, Any]:
    cleanup: dict[str, Any] = {}
    request_id = request_base
    if should_interrupt and thread_id and turn_id:
        request_id += 1
        cleanup["turn_interrupt"] = safe_rpc_summary(
            client.request(request_id, "turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=5)
        )
    if thread_id:
        request_id += 1
        cleanup["thread_unsubscribe"] = safe_rpc_summary(
            client.request(request_id, "thread/unsubscribe", {"threadId": thread_id}, timeout=5)
        )
    return cleanup


def safe_turn_error_message(error: Any) -> str | None:
    if not isinstance(error, dict):
        return None
    value = error.get("message")
    if isinstance(value, str) and value.strip():
        return redact(value.strip())[:240]
    info = error.get("codexErrorInfo")
    if isinstance(info, dict):
        value = info.get("message")
        if isinstance(value, str) and value.strip():
            return redact(value.strip())[:240]
    return None


def classify_turn_error(error: Any, notifications: list[dict[str, Any]] | None = None) -> str:
    rendered = safe_json({"error": error, "notifications": notifications or []}).lower()
    if any(token in rendered for token in ["auth", "sign in", "signin", "login", "log in", "401", "unauthorized", "relogin"]):
        if "unauthorized" in rendered or "401" in rendered:
            return "Unauthorized"
        if "relogin" in rendered or "sign in" in rendered or "signin" in rendered or "login" in rendered:
            return "Relogin"
        return "Auth"
    if rendered in {"null", "{\"error\": null, \"notifications\": []}"}:
        return "None"
    return "Other"


def find_turn_in_payload(obj: Any, turn_id: str | None) -> dict[str, Any] | None:
    if isinstance(obj, dict):
        if isinstance(obj.get("turn"), dict):
            turn = obj["turn"]
            if not turn_id or turn.get("id") == turn_id:
                return turn
        if obj.get("id") == turn_id and "status" in obj:
            return obj
        for value in obj.values():
            found = find_turn_in_payload(value, turn_id)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_turn_in_payload(value, turn_id)
            if found:
                return found
    return None


def read_notification_params(notification: dict[str, Any]) -> dict[str, Any]:
    params = notification.get("params")
    return params if isinstance(params, dict) else {}


def read_notification_thread_id(notification: dict[str, Any]) -> str | None:
    params = read_notification_params(notification)
    turn = params.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("threadId"), str) and turn["threadId"].strip():
        return turn["threadId"].strip()
    value = params.get("threadId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_notification_turn_id(notification: dict[str, Any]) -> str | None:
    params = read_notification_params(notification)
    turn = params.get("turn")
    if isinstance(turn, dict) and isinstance(turn.get("id"), str) and turn["id"].strip():
        return turn["id"].strip()
    value = params.get("turnId")
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_notification_turn(notification: dict[str, Any]) -> dict[str, Any] | None:
    params = read_notification_params(notification)
    turn = params.get("turn")
    return turn if isinstance(turn, dict) else None


def notification_will_retry(notification: dict[str, Any]) -> bool:
    params = read_notification_params(notification)
    value = params.get("willRetry")
    if isinstance(value, bool):
        return value
    error = params.get("error")
    if isinstance(error, dict) and isinstance(error.get("willRetry"), bool):
        return error["willRetry"]
    return False


def extract_agent_delta(notification: dict[str, Any]) -> str | None:
    if notification.get("method") != "item/agentMessage/delta":
        return None
    params = read_notification_params(notification)
    delta = params.get("delta")
    return delta if isinstance(delta, str) and delta else None


class TerminalEventHarness:
    def __init__(self) -> None:
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.buffered: list[dict[str, Any]] = []
        self.notifications: list[dict[str, Any]] = []
        self.latest_turn: dict[str, Any] | None = None
        self.assistant_text: str | None = None
        self.assistant_path: str | None = None
        self.terminal_method: str | None = None
        self.terminal_error: Any = None
        self.terminal_without_auth_error = False

    def handle_notification(self, notification: dict[str, Any]) -> None:
        if not isinstance(notification, dict) or not notification.get("method"):
            return
        if not self.thread_id or not self.turn_id:
            self.buffered.append(notification)
            self.buffered = self.buffered[-100:]
            return
        self.process_notification(notification)

    def set_active(self, thread_id: str, turn_id: str | None) -> None:
        self.thread_id = thread_id
        self.turn_id = turn_id
        buffered = list(self.buffered)
        self.buffered.clear()
        for notification in buffered:
            self.process_notification(notification)

    def matches_active(self, notification: dict[str, Any]) -> bool:
        if not self.thread_id:
            return False
        notification_thread_id = read_notification_thread_id(notification)
        notification_turn_id = read_notification_turn_id(notification)
        if notification_thread_id and notification_thread_id != self.thread_id:
            return False
        if self.turn_id and notification_turn_id and notification_turn_id != self.turn_id:
            return False
        return bool(notification_thread_id == self.thread_id or (self.turn_id and notification_turn_id == self.turn_id))

    def process_notification(self, notification: dict[str, Any]) -> None:
        if not self.matches_active(notification):
            return
        self.notifications.append(notification)
        self.notifications = self.notifications[-100:]
        delta = extract_agent_delta(notification)
        if delta:
            self.assistant_text = (self.assistant_text or "") + delta
            self.assistant_path = "notification:item/agentMessage/delta.params.delta"
        turn = read_notification_turn(notification)
        if turn:
            self.latest_turn = turn
            found_text, found_path = extract_assistant_text(turn)
            if found_text:
                self.assistant_text = found_text
                self.assistant_path = f"notification:{notification.get('method')}.{found_path}"
        method = notification.get("method")
        if method == "turn/completed":
            self.terminal_method = "turn/completed"
            if not self.latest_turn and turn:
                self.latest_turn = turn
        elif method == "error" and not notification_will_retry(notification):
            params = read_notification_params(notification)
            self.terminal_method = "error"
            self.terminal_error = params.get("error") if isinstance(params.get("error"), dict) else params

    def absorb_thread_read(self, response: RpcResponse) -> None:
        for notification in response.notifications:
            self.handle_notification(notification)
        found_text, found_path = extract_assistant_text(response.response)
        if found_text:
            self.assistant_text = found_text
            self.assistant_path = found_path
        found_turn = find_turn_in_payload(response.response, self.turn_id)
        if found_turn:
            self.latest_turn = found_turn

    def terminal_observed(self) -> bool:
        status = self.latest_turn.get("status") if self.latest_turn else None
        return self.terminal_method in {"turn/completed", "error"} or status in {"completed", "failed", "interrupted"}

    def build_result(self, terminal_verdict: str, *, thread_read_response: Any = None) -> dict[str, Any]:
        turn = self.latest_turn or {}
        if self.terminal_error and not turn.get("error"):
            turn = {**turn, "error": self.terminal_error}
        fields = safe_terminal_turn_fields(self.thread_id, self.turn_id, turn, bool(self.assistant_text))
        fields.update({
            "terminal_state_observed": terminal_verdict != "DUMMY_TURN_TIMEOUT_NO_TERMINAL_STATE",
            "terminal_method": self.terminal_method,
            "assistant_text_extraction_path": self.assistant_path,
            "assistant_text_sample": redact(self.assistant_text[:120]) if self.assistant_text else None,
            "terminal_verdict": terminal_verdict,
            "notifications_tail": redact(self.notifications[-10:]),
        })
        if thread_read_response is not None:
            fields["thread_read_response"] = redact(thread_read_response)
        return fields

    def classify_terminal_verdict(self) -> str | None:
        if self.assistant_text:
            return "DUMMY_TURN_COMPLETED_WITH_ASSISTANT"
        turn_error = self.terminal_error
        if self.latest_turn and isinstance(self.latest_turn.get("error"), dict):
            turn_error = self.latest_turn.get("error")
        error_class = classify_turn_error(turn_error, self.notifications)
        status = self.latest_turn.get("status") if self.latest_turn else None
        if self.terminal_method == "error" or status in {"failed", "interrupted"}:
            return "DUMMY_TURN_TERMINAL_AUTH_ERROR" if error_class in {"Auth", "Unauthorized", "Relogin"} else "DUMMY_TURN_TERMINAL_NON_AUTH_ERROR"
        if self.terminal_method == "turn/completed" or status == "completed":
            return "DUMMY_TURN_TERMINAL_AUTH_ERROR" if error_class in {"Auth", "Unauthorized", "Relogin"} else "DUMMY_TURN_COMPLETED_WITHOUT_AUTH_ERROR"
        return None


def safe_terminal_turn_fields(thread_id: str | None, turn_id: str | None, turn: dict[str, Any] | None, assistant_text_present: bool) -> dict[str, Any]:
    turn = turn or {}
    error = turn.get("error")
    items = turn.get("items") if isinstance(turn.get("items"), list) else []
    return {
        "thread_id_present": bool(thread_id),
        "turn_id_present": bool(turn_id),
        "thread_id": thread_id,
        "turn_id": turn_id,
        "turn_status": turn.get("status"),
        "turn_error_message": safe_turn_error_message(error),
        "turn_error_codex_message": safe_turn_error_message({"codexErrorInfo": (error or {}).get("codexErrorInfo")} if isinstance(error, dict) else None),
        "turn_error_class": classify_turn_error(error),
        "completedAt": turn.get("completedAt"),
        "durationMs": turn.get("durationMs"),
        "items_count": len(items),
        "assistant_text_present": assistant_text_present,
    }


def observe_terminal_turn(client: JsonRpcProcess, thread_id: str, turn_id: str | None, request_base: int, harness: TerminalEventHarness | None = None) -> dict[str, Any]:
    notifications: list[dict[str, Any]] = []
    latest_turn: dict[str, Any] | None = None
    assistant_text = None
    assistant_path = None
    deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
    request_id = request_base
    if harness is None:
        harness = TerminalEventHarness()
        harness.set_active(thread_id, turn_id)
    while time.monotonic() < deadline:
        verdict = harness.classify_terminal_verdict()
        if verdict:
            return harness.build_result(verdict)
        request_id += 1
        read = client.request(request_id, "thread/read", {"threadId": thread_id, "includeTurns": True}, timeout=5)
        harness.absorb_thread_read(read)
        verdict = harness.classify_terminal_verdict()
        if verdict:
            return harness.build_result(verdict, thread_read_response=read.response)
        notifications.extend(read.notifications)
        found_text, found_path = extract_assistant_text(read.response)
        if found_text:
            assistant_text = found_text
            assistant_path = found_path
        latest_turn = find_turn_in_payload(read.response, turn_id) or latest_turn
        for notification in read.notifications:
            notification_turn = find_turn_in_payload(notification, turn_id)
            if notification_turn:
                latest_turn = notification_turn
        status = latest_turn.get("status") if latest_turn else None
        if status in {"failed", "interrupted"}:
            fields = safe_terminal_turn_fields(thread_id, turn_id, latest_turn, bool(assistant_text))
            fields.update({
                "terminal_state_observed": True,
                "assistant_text_extraction_path": assistant_path,
                "assistant_text_sample": redact(assistant_text[:120]) if assistant_text else None,
                "terminal_verdict": "DUMMY_TURN_TERMINAL_AUTH_ERROR" if fields["turn_error_class"] in {"Auth", "Unauthorized", "Relogin"} else "DUMMY_TURN_TERMINAL_NON_AUTH_ERROR",
                "thread_read_response": redact(read.response),
            })
            return fields
        if status == "completed":
            fields = safe_terminal_turn_fields(thread_id, turn_id, latest_turn, bool(assistant_text))
            if assistant_text:
                verdict = "DUMMY_TURN_COMPLETED_WITH_ASSISTANT"
            elif fields["turn_error_class"] in {"Auth", "Unauthorized", "Relogin"}:
                verdict = "DUMMY_TURN_TERMINAL_AUTH_ERROR"
            else:
                verdict = "DUMMY_TURN_COMPLETED_WITHOUT_AUTH_ERROR"
            fields.update({
                "terminal_state_observed": True,
                "assistant_text_extraction_path": assistant_path,
                "assistant_text_sample": redact(assistant_text[:120]) if assistant_text else None,
                "terminal_verdict": verdict,
                "thread_read_response": redact(read.response),
            })
            return fields
        time.sleep(0.5)
    fields = safe_terminal_turn_fields(thread_id, turn_id, latest_turn, bool(assistant_text))
    if harness.assistant_text:
        fields = safe_terminal_turn_fields(thread_id, turn_id, harness.latest_turn or latest_turn, True)
        assistant_text = harness.assistant_text
        assistant_path = harness.assistant_path
    fields.update({
        "terminal_state_observed": False,
        "assistant_text_extraction_path": assistant_path,
        "assistant_text_sample": redact(assistant_text[:120]) if assistant_text else None,
        "terminal_verdict": "DUMMY_TURN_TIMEOUT_NO_TERMINAL_STATE",
        "notifications_tail": redact((harness.notifications or notifications)[-10:]),
    })
    return fields


def run_dummy_auth_dependent_probes(client: JsonRpcProcess, *, request_base: int) -> dict[str, Any]:
    probes: dict[str, Any] = {}
    account_read_false = client.request(request_base + 1, "account/read", {"refreshToken": False}, timeout=PROBE_TIMEOUT_SECONDS)
    probes["account_read_refresh_false"] = safe_rpc_summary(account_read_false)
    account_read_true = client.request(request_base + 2, "account/read", {"refreshToken": True}, timeout=PROBE_TIMEOUT_SECONDS)
    probes["account_read_refresh_true"] = safe_rpc_summary(account_read_true)
    model_list = client.request(request_base + 3, "model/list", {}, timeout=PROBE_TIMEOUT_SECONDS)
    probes["model_list"] = safe_rpc_summary(model_list)
    models = []
    result = rpc_result(model_list)
    if isinstance(result, dict) and isinstance(result.get("data"), list):
        for item in result["data"]:
            if isinstance(item, dict):
                model_id = item.get("id") or item.get("model")
                if isinstance(model_id, str):
                    models.append(model_id)
    probes["model_list_usable_model_count"] = len(models)
    probes["model_list_usable_model_sample"] = models[:5]

    harness = TerminalEventHarness()
    remove_handler = client.add_notification_handler(harness.handle_notification, replay_existing=False)
    try:
        thread = client.request(request_base + 4, "thread/start", thread_start_params(), timeout=PROBE_TIMEOUT_SECONDS)
        probes["thread_start"] = safe_rpc_summary(thread)
        thread_id = extract_thread_id(thread)
        probes["thread_start_succeeded"] = bool(thread_id)
        if thread_id:
            turn = client.request(request_base + 5, "turn/start", turn_start_params(thread_id), timeout=PROBE_TIMEOUT_SECONDS)
            probes["turn_start"] = safe_rpc_summary(turn)
            turn_id = extract_turn_id(turn)
            probes["turn_start_succeeded"] = rpc_result_ok(turn)
            if turn_id:
                harness.set_active(thread_id, turn_id)
            probes["terminal_turn"] = observe_terminal_turn(client, thread_id, turn_id, request_base + 100, harness)
            probes["cleanup"] = cleanup_ephemeral_turn(
                client,
                thread_id=thread_id,
                turn_id=turn_id,
                request_base=request_base + 300,
                should_interrupt=not bool(probes["terminal_turn"].get("terminal_state_observed")),
            )
            probes["assistant_output"] = {
                "ok": bool(probes["terminal_turn"].get("assistant_text_present")),
                "text_path": probes["terminal_turn"].get("assistant_text_extraction_path"),
                "sample_text": probes["terminal_turn"].get("assistant_text_sample"),
            }
        else:
            probes["turn_start"] = {"ok": False, "skipped": True, "reason": "thread_start_failed"}
            probes["turn_start_succeeded"] = False
            probes["assistant_output"] = {"ok": False, "skipped": True, "reason": "thread_start_failed"}
            probes["terminal_turn"] = {"terminal_state_observed": False, "terminal_verdict": "DUMMY_THREAD_TURN_CREATED_BUT_NO_ASSISTANT_OUTPUT"}
    finally:
        remove_handler()

    terminal = probes["terminal_turn"]
    terminal_verdict = terminal.get("terminal_verdict")
    p0_assistant = terminal_verdict == "DUMMY_TURN_COMPLETED_WITH_ASSISTANT"
    p0_terminal_without_auth = terminal_verdict == "DUMMY_TURN_COMPLETED_WITHOUT_AUTH_ERROR"
    blocked_unknown = terminal_verdict == "DUMMY_TURN_TIMEOUT_NO_TERMINAL_STATE"
    terminal_auth_pass = terminal_verdict == "DUMMY_TURN_TERMINAL_AUTH_ERROR"
    probes["p0_conditions"] = {
        "dummy_produces_assistant_reply": p0_assistant,
        "dummy_terminal_turn_completed_without_auth_error": p0_terminal_without_auth,
    }
    probes["blocked_unknown"] = blocked_unknown
    probes["DUMMY_LOGIN_ACCEPTED_BUT_AUTH_DEPENDENT_OPERATIONS_FAILED"] = "PASS" if terminal_auth_pass else ("BLOCKED_UNKNOWN" if blocked_unknown else "FAIL")
    probes["DUMMY_TOKEN_MATRIX"] = {
        "login_start": "success",
        "account_read_refresh_false": "success" if probes["account_read_refresh_false"]["ok"] else "fail",
        "account_read_refresh_false_reason": probes["account_read_refresh_false"].get("error"),
        "account_read_refresh_true": "success" if probes["account_read_refresh_true"]["ok"] else "fail",
        "account_read_refresh_true_reason": probes["account_read_refresh_true"].get("error"),
        "model_list": "success" if probes["model_list"]["ok"] else "fail",
        "model_list_count": len(models),
        "thread_start": "success" if thread_id else "fail",
        "thread_id_present": bool(thread_id),
        "turn_start": "success" if probes.get("turn_start_succeeded") else "fail",
        "turn_id_present": bool(terminal.get("turn_id_present")),
        "terminal_state_observed": "yes" if terminal.get("terminal_state_observed") else "no",
        "turn_status": terminal.get("turn_status"),
        "turn_error_class": terminal.get("turn_error_class"),
        "assistant_text_present": "yes" if terminal.get("assistant_text_present") else "no",
        "local_profile_bypass_detected": "pending",
        "final_verdict": "P0_FAIL" if (p0_assistant or p0_terminal_without_auth) else ("BLOCKED_UNKNOWN" if blocked_unknown else ("PASS" if terminal_auth_pass else "INCONCLUSIVE")),
    }
    return probes


def has_possible_local_profile() -> bool:
    candidates = [
        Path(os.environ.get("CODEX_HOME") or "") if os.environ.get("CODEX_HOME") else None,
        Path.home() / ".codex",
        Path(os.environ.get("OPENCLAW_HOME") or "") if os.environ.get("OPENCLAW_HOME") else None,
        Path.home() / ".openclaw",
    ]
    names = {"auth.json", "config.toml", "credentials.json", "profiles.json"}
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if candidate.exists() and any((candidate / name).exists() for name in names):
                return True
        except OSError:
            continue
    return False


def run_local_profile_bypass_probe(base_cmd: list[str]) -> dict[str, Any]:
    if not has_possible_local_profile():
        return {"ok": True, "skipped": True, "reason": "no_local_profile_detected_without_reading_secrets"}
    client: JsonRpcProcess | None = None
    try:
        client = JsonRpcProcess(base_cmd, mode="local_profile")
        init = client.request(7001, "initialize", {
            "clientInfo": {"name": "nexus-local-profile-bypass-probe", "title": "Nexus Local Profile Bypass Probe", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "optOutNotificationMethods": []},
        }, timeout=PROBE_TIMEOUT_SECONDS)
        if not rpc_result_ok(init):
            return {"ok": True, "skipped": True, "reason": "local_profile_appserver_initialize_failed", "initialize": safe_rpc_summary(init)}
        login = client.request(7002, "account/login/start", {
            "type": "chatgptAuthTokens",
            "accessToken": DUMMY_ACCESS_TOKEN,
            "chatgptAccountId": DUMMY_ACCOUNT_ID,
            "chatgptPlanType": "plus",
        }, timeout=PROBE_TIMEOUT_SECONDS)
        probes = run_dummy_auth_dependent_probes(client, request_base=7100)
        matrix = probes.get("DUMMY_TOKEN_MATRIX") or {}
        p0 = matrix.get("final_verdict") == "P0_FAIL"
        return {
            "ok": not p0,
            "skipped": False,
            "login_start": safe_rpc_summary(login),
            "dummy_auth_dependent_probes": probes,
            "P0_SECURITY_LOCAL_PROFILE_BYPASS": p0,
        }
    finally:
        if client:
            client.stop()


def main() -> int:
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "timestamp_utc": now_iso(),
        "root_dir": str(ROOT_DIR),
        "status": "FAIL",
        "failure_reasons": [],
        "command": {},
        "initialize": {},
        "auth_negative": {},
        "auth_positive": {},
        "conversation": {},
        "local_profile_bypass": {},
        "redaction": {},
        "managed_binary_fallback": {},
        "VALID_TOKEN_MATRIX": default_valid_token_matrix(),
    }

    candidates = command_candidates()
    if not candidates:
        report["failure_reasons"].append("codex_appserver_executable_not_found")
        openclaw = shutil.which("openclaw")
        if openclaw:
            code, out, err = run_command([openclaw, "--version"], timeout=5)
            report["managed_binary_fallback"] = {"openclaw_path": openclaw, "version_exit_code": code, "version_stdout": out.strip(), "version_stderr": err.strip()}
        else:
            report["managed_binary_fallback"] = {"openclaw_path": None, "reason": "openclaw_not_found"}
        return write_report(report)

    base_cmd = candidates[0]
    exe = base_cmd[0]
    code, out, err = run_command(base_cmd + ["--version"], timeout=5)
    report["command"] = {
        "path": exe,
        "version_exit_code": code,
        "version_stdout": out.strip(),
        "version_stderr": err.strip(),
        "start_command_shape": base_cmd + ["app-server", "--listen", "stdio://"],
    }
    if code not in {0, None}:
        report["failure_reasons"].append("codex_version_failed")

    client: JsonRpcProcess | None = None
    try:
        client = JsonRpcProcess(base_cmd)
    except Exception as exc:
        report["failure_reasons"].append("codex_appserver_start_failed")
        report["command"]["start_error"] = redact(str(exc))
        return write_report(report)

    try:
        init_params = {
            "clientInfo": {"name": "nexus-codex-runtime-v3-discovery", "title": "Nexus Codex Runtime v3 Discovery", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True, "optOutNotificationMethods": []},
        }
        init = client.request(1, "initialize", init_params, timeout=PROBE_TIMEOUT_SECONDS)
        report["initialize"] = {
            "ok": init.ok and bool(init.response and "result" in init.response),
            "response": redact(init.response),
            "error": init.error or response_error_code(init.response),
        }
        if not report["initialize"]["ok"]:
            report["failure_reasons"].append("jsonrpc_initialize_failed")
            return write_report(report, client)

        dummy_params = {
            "type": "chatgptAuthTokens",
            "accessToken": DUMMY_ACCESS_TOKEN,
            "chatgptAccountId": DUMMY_ACCOUNT_ID,
            "chatgptPlanType": "plus",
        }
        dummy = client.request(2, "account/login/start", dummy_params, timeout=PROBE_TIMEOUT_SECONDS)
        dummy_succeeded = bool(dummy.ok and dummy.response and "result" in dummy.response and "error" not in dummy.response)
        dummy_auth_dependent = run_dummy_auth_dependent_probes(client, request_base=20)
        report["auth_negative"] = {
            "DUMMY_LOGIN_START_ACCEPTED": dummy_succeeded,
            "login_start_response": redact(dummy.response),
            "login_start_error": dummy.error or response_error_code(dummy.response),
            "dummy_auth_dependent_probes": dummy_auth_dependent,
            "ok": dummy_auth_dependent.get("DUMMY_LOGIN_ACCEPTED_BUT_AUTH_DEPENDENT_OPERATIONS_FAILED") == "PASS",
            "DUMMY_TOKEN_MATRIX": dummy_auth_dependent.get("DUMMY_TOKEN_MATRIX"),
            "response": redact(dummy.response),
            "error": dummy.error or response_error_code(dummy.response),
        }
        for condition, failed in (dummy_auth_dependent.get("p0_conditions") or {}).items():
            if failed:
                report["failure_reasons"].append(f"P0_SECURITY_{condition}")
        if dummy_auth_dependent.get("blocked_unknown"):
            report["failure_reasons"].append("BLOCKED_UNKNOWN_dummy_terminal_turn_no_terminal_state")

        report["local_profile_bypass"] = run_local_profile_bypass_probe(base_cmd)
        if report["local_profile_bypass"].get("P0_SECURITY_LOCAL_PROFILE_BYPASS"):
            report["failure_reasons"].append("P0_SECURITY_LOCAL_PROFILE_BYPASS")
        local_profile_bypass_detected = "yes" if report["local_profile_bypass"].get("P0_SECURITY_LOCAL_PROFILE_BYPASS") else "no"
        matrix = report.get("auth_negative", {}).get("DUMMY_TOKEN_MATRIX")
        if isinstance(matrix, dict):
            matrix["local_profile_bypass_detected"] = local_profile_bypass_detected
        nested_matrix = ((report.get("auth_negative", {}).get("dummy_auth_dependent_probes") or {}).get("DUMMY_TOKEN_MATRIX"))
        if isinstance(nested_matrix, dict):
            nested_matrix["local_profile_bypass_detected"] = local_profile_bypass_detected

        blocked_only = bool(report["failure_reasons"]) and all(str(reason).startswith("BLOCKED_UNKNOWN") for reason in report["failure_reasons"])
        if report["failure_reasons"] and not blocked_only:
            return write_report(report, client)

        if not VALID_ACCESS_TOKEN:
            report["auth_positive"] = {"ok": False, "skipped": True, "reason": "pending_server_credential"}
            report["VALID_TOKEN_MATRIX"] = default_valid_token_matrix()
            report["conversation"] = {"ok": False, "skipped": True, "reason": "pending_server_credential"}
            report["status"] = "BLOCKED_UNKNOWN" if blocked_only else "PENDING_VALID_TOKEN"
            return write_report(report, client)
        if not VALID_ACCOUNT_ID:
            report["auth_positive"] = {"ok": False, "skipped": True, "reason": "pending_valid_account_id"}
            report["VALID_TOKEN_MATRIX"] = default_valid_token_matrix()
            report["conversation"] = {"ok": False, "skipped": True, "reason": "pending_valid_account_id"}
            report["status"] = "BLOCKED_UNKNOWN" if blocked_only else "PENDING_VALID_TOKEN"
            return write_report(report, client)

        positive = client.request(
            3,
            "account/login/start",
            {
                "type": "chatgptAuthTokens",
                "accessToken": VALID_ACCESS_TOKEN,
                "chatgptAccountId": VALID_ACCOUNT_ID,
                "chatgptPlanType": VALID_PLAN_TYPE,
            },
            timeout=PROBE_TIMEOUT_SECONDS,
        )
        positive_ok = bool(positive.ok and positive.response and "result" in positive.response and "error" not in positive.response)
        report["auth_positive"] = {
            "ok": positive_ok,
            "token_source": VALID_TOKEN_SOURCE,
            "token_fingerprint": sha256_short(VALID_ACCESS_TOKEN),
            "account_id_fingerprint": sha256_short(VALID_ACCOUNT_ID),
            "response": redact(positive.response),
            "error": positive.error or response_error_code(positive.response),
        }
        valid_matrix = {
            "credential_available": "yes",
            "login_start": "success" if positive_ok else "fail",
            "account_read": "pending",
            "model_list": "pending",
            "thread_start": "pending",
            "turn_start": "pending",
            "terminal_state_observed": "pending",
            "assistant_text_extraction_path": "pending",
            "strict_json_parse": "pending",
        }
        report["VALID_TOKEN_MATRIX"] = valid_matrix
        if not positive_ok:
            report["failure_reasons"].append("positive_auth_failed")
            return write_report(report, client)

        account_false = client.request(4, "account/read", {"refreshToken": False}, timeout=PROBE_TIMEOUT_SECONDS)
        account_true = client.request(5, "account/read", {"refreshToken": True}, timeout=PROBE_TIMEOUT_SECONDS)
        models = client.request(6, "model/list", {}, timeout=PROBE_TIMEOUT_SECONDS)
        report["auth_positive"]["account_read_refresh_false"] = safe_rpc_summary(account_false)
        report["auth_positive"]["account_read_refresh_true"] = safe_rpc_summary(account_true)
        report["auth_positive"]["model_list"] = safe_rpc_summary(models)
        valid_matrix["account_read"] = "success" if rpc_result_ok(account_false) else "fail"
        valid_matrix["model_list"] = "success" if rpc_result_ok(models) else "fail"
        if not rpc_result_ok(account_false):
            report["failure_reasons"].append("positive_account_read_failed")
            return write_report(report, client)
        if not rpc_result_ok(models):
            report["failure_reasons"].append("positive_model_list_failed")
            return write_report(report, client)

        harness = TerminalEventHarness()
        remove_handler = client.add_notification_handler(harness.handle_notification, replay_existing=False)
        try:
            thread = client.request(7, "thread/start", thread_start_params(), timeout=PROBE_TIMEOUT_SECONDS)
            thread_id = extract_thread_id(thread)
            report["conversation"]["thread_start"] = safe_rpc_summary(thread)
            valid_matrix["thread_start"] = "success" if thread_id else "fail"
            if not thread_id:
                report["failure_reasons"].append("thread_start_failed")
                return write_report(report, client)

            turn = client.request(8, "turn/start", turn_start_params(thread_id), timeout=PROBE_TIMEOUT_SECONDS)
            report["conversation"]["turn_start"] = safe_rpc_summary(turn)
            valid_matrix["turn_start"] = "success" if rpc_result_ok(turn) else "fail"
            if not rpc_result_ok(turn):
                report["failure_reasons"].append("turn_start_failed")
                return write_report(report, client)

            turn_id = extract_turn_id(turn)
            if turn_id:
                harness.set_active(thread_id, turn_id)
            completed = observe_terminal_turn(client, thread_id, turn_id, 200, harness)
            report["conversation"]["cleanup"] = cleanup_ephemeral_turn(
                client,
                thread_id=thread_id,
                turn_id=turn_id,
                request_base=400,
                should_interrupt=not bool(completed.get("terminal_state_observed")),
            )
        finally:
            remove_handler()
        report["conversation"]["terminal_turn"] = completed
        valid_matrix["terminal_state_observed"] = "yes" if completed.get("terminal_state_observed") else "no"
        valid_matrix["assistant_text_extraction_path"] = "identified" if completed.get("assistant_text_extraction_path") else "unknown"
        if completed.get("assistant_text_present") and completed.get("assistant_text_extraction_path"):
            report["conversation"]["assistant_output"] = {
                "ok": True,
                "text_path": completed.get("assistant_text_extraction_path"),
                "sample_text": completed.get("assistant_text_sample"),
            }
            try:
                json.loads(str(completed.get("assistant_text_sample") or ""))
                valid_matrix["strict_json_parse"] = "success"
            except ValueError:
                valid_matrix["strict_json_parse"] = "fail"
        else:
            report["conversation"]["assistant_output"] = completed
            valid_matrix["strict_json_parse"] = "pending" if not completed.get("terminal_state_observed") else "fail"
            report["failure_reasons"].append("assistant_output_extraction_not_confirmed")
            return write_report(report, client)

        report["status"] = "BLOCKED_UNKNOWN" if blocked_only else "PASS"
        return write_report(report, client)
    finally:
        if client:
            client.stop()


def write_report(report: dict[str, Any], client: JsonRpcProcess | None = None) -> int:
    if client:
        findings = client.redaction_findings()
        report.setdefault("command", {})["isolated_codex_home"] = str(client.codex_home)
        report.setdefault("command", {})["isolated_home"] = str(client.native_home)
        report.setdefault("command", {})["cleared_auth_env_vars"] = client.cleared_auth_env_vars
        report["redaction"] = {
            "ok": len(findings) == 0,
            "findings": findings,
            "stdout_lines": len(client.raw_stdout),
            "stderr_lines": len(client.raw_stderr),
        }
        if findings:
            report["failure_reasons"].append("token_or_secret_material_in_appserver_logs")
    else:
        report["redaction"] = {"ok": True, "findings": [], "stdout_lines": 0, "stderr_lines": 0}
    if report.get("failure_reasons"):
        if all(str(reason).startswith("BLOCKED_UNKNOWN") for reason in report["failure_reasons"]):
            report["status"] = "BLOCKED_UNKNOWN"
        else:
            report["status"] = "FAIL"
    elif report.get("status") not in {"PASS", "PENDING_VALID_TOKEN", "BLOCKED_UNKNOWN"}:
        report["status"] = "FAIL"

    safe_report = redact(report)
    payload_path = ARTIFACT_DIR / "discovery_report_sanitized.json"
    with payload_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(safe_report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    lines = [
        "# Codex App-Server Runtime v3 Discovery Report",
        "",
        f"Generated UTC: {safe_report['timestamp_utc']}",
        f"Status: **{safe_report['status']}**",
        "",
        "## Verdict",
        "",
    ]
    if safe_report["status"] == "PASS":
        lines.append("Discovery passed. Production-candidate implementation may proceed subject to tests and SLA gates.")
    elif safe_report["status"] == "PENDING_VALID_TOKEN":
        lines.append("Discovery passed the dummy-token negative gates, but production-candidate implementation must wait for a controlled valid Nexus Codex credential to identify the positive reply extraction path.")
    elif safe_report["status"] == "BLOCKED_UNKNOWN":
        lines.append("Discovery is blocked for production approval: dummy token did not produce an assistant reply, but no natural terminal turn state was observed. Engineering candidate work may proceed, but do not increase canary or approve production until terminal auth behavior is known.")
    else:
        lines.append("Discovery failed. Production approval and canary increase must not proceed; keep Python 18800 rollback available.")
    lines.extend(["", "Failure reasons:"])
    for reason in safe_report.get("failure_reasons") or ["none"]:
        lines.append(f"- `{reason}`")
    lines.extend(
        [
            "",
            "## Executable",
            "",
            f"- Command path: `{safe_report.get('command', {}).get('path')}`",
            f"- Version exit code: `{safe_report.get('command', {}).get('version_exit_code')}`",
            f"- Version stdout: `{safe_report.get('command', {}).get('version_stdout')}`",
            f"- Start command shape: `{safe_report.get('command', {}).get('start_command_shape')}`",
            f"- Isolated CODEX_HOME: `{safe_report.get('command', {}).get('isolated_codex_home')}`",
            f"- Isolated HOME: `{safe_report.get('command', {}).get('isolated_home')}`",
            f"- Cleared auth env vars: `{safe_report.get('command', {}).get('cleared_auth_env_vars')}`",
            "",
            "## OpenClaw Reference Notes",
            "",
            "- Reference repo: `https://github.com/openclaw/openclaw`",
            "- Current main inspected: `4a45098a866949f8cbb790840fd7ee1533855450`",
            "- Reference pack pinned commit: `732cf542404f06c5e978ec37936a179d8c339d5e`",
            "- `package.json` does not expose `extensions/codex/src/app-server/*` as stable public package exports.",
            "- `shared-client.ts` initializes one reusable app-server client and then applies auth through `account/login/start`.",
            "- `auth-bridge.ts` isolates Codex home for stdio startup; this probe sets an isolated `CODEX_HOME` for the same boundary.",
            "- Phase 1D harness registers a notification handler before `thread/start` / `turn/start`, buffers early notifications, correlates `params.threadId` / `params.turnId` and `params.turn.threadId` / `params.turn.id`, and treats `turn/completed` or non-retry `error` as terminal.",
            "",
            "## JSON-RPC Initialize",
            "",
            f"- OK: `{safe_report.get('initialize', {}).get('ok')}`",
            f"- Error: `{safe_report.get('initialize', {}).get('error')}`",
            f"- Response: `{safe_json(safe_report.get('initialize', {}).get('response'))}`",
            "",
            "## Auth Negative Test",
            "",
            f"- `DUMMY_LOGIN_START_ACCEPTED`: `{safe_report.get('auth_negative', {}).get('DUMMY_LOGIN_START_ACCEPTED')}`",
            f"- Login/start error: `{safe_report.get('auth_negative', {}).get('login_start_error')}`",
            f"- Login/start response: `{safe_json(safe_report.get('auth_negative', {}).get('login_start_response'))}`",
            f"- `DUMMY_LOGIN_ACCEPTED_BUT_AUTH_DEPENDENT_OPERATIONS_FAILED`: `{((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('DUMMY_LOGIN_ACCEPTED_BUT_AUTH_DEPENDENT_OPERATIONS_FAILED'))}`",
            f"- Dummy `account/read refreshToken=false` OK: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('account_read_refresh_false') or {}).get('ok'))}`",
            f"- Dummy `account/read refreshToken=true` OK: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('account_read_refresh_true') or {}).get('ok'))}`",
            f"- Dummy `model/list` usable count: `{((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('model_list_usable_model_count'))}`",
            f"- Dummy `thread/start` succeeded: `{((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('thread_start_succeeded'))}`",
            f"- Dummy `turn/start` succeeded: `{((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('turn_start_succeeded'))}`",
            f"- Dummy assistant output OK: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('assistant_output') or {}).get('ok'))}`",
            f"- Dummy terminal verdict: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('terminal_verdict'))}`",
            f"- Dummy terminal observed: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('terminal_state_observed'))}`",
            f"- Phase 1D terminal event handling understood: `{(safe_report.get('auth_negative', {}).get('DUMMY_TOKEN_MATRIX') or {}).get('terminal_state_observed') == 'yes'}`",
            f"- Dummy terminal thread id: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('thread_id'))}`",
            f"- Dummy terminal turn id: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('turn_id'))}`",
            f"- Dummy turn status: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('turn_status'))}`",
            f"- Dummy turn error class: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('turn_error_class'))}`",
            f"- Dummy turn error message: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('turn_error_message'))}`",
            f"- Dummy turn completedAt: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('completedAt'))}`",
            f"- Dummy turn durationMs: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('durationMs'))}`",
            f"- Dummy turn items count: `{(((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('terminal_turn') or {}).get('items_count'))}`",
            f"- Dummy cleanup: `{safe_json((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('cleanup'))}`",
            f"- Dummy P0 conditions: `{((safe_report.get('auth_negative', {}).get('dummy_auth_dependent_probes') or {}).get('p0_conditions'))}`",
            "",
            "## DUMMY_TOKEN_MATRIX",
            "",
            f"`{safe_json(safe_report.get('auth_negative', {}).get('DUMMY_TOKEN_MATRIX'))}`",
            "",
            "## Local Profile Bypass Test",
            "",
            f"- OK: `{safe_report.get('local_profile_bypass', {}).get('ok')}`",
            f"- Skipped: `{safe_report.get('local_profile_bypass', {}).get('skipped')}`",
            f"- Reason: `{safe_report.get('local_profile_bypass', {}).get('reason')}`",
            f"- `P0_SECURITY_LOCAL_PROFILE_BYPASS`: `{safe_report.get('local_profile_bypass', {}).get('P0_SECURITY_LOCAL_PROFILE_BYPASS')}`",
            "",
            "## Auth Positive Test",
            "",
            f"- OK: `{safe_report.get('auth_positive', {}).get('ok')}`",
            f"- Skipped: `{safe_report.get('auth_positive', {}).get('skipped')}`",
            f"- Reason/Error: `{safe_report.get('auth_positive', {}).get('reason') or safe_report.get('auth_positive', {}).get('error')}`",
            f"- Token fingerprint: `{safe_report.get('auth_positive', {}).get('token_fingerprint')}`",
            "",
            "## VALID_TOKEN_MATRIX",
            "",
            f"`{safe_json(safe_report.get('VALID_TOKEN_MATRIX'))}`",
            "",
            "## Conversation Probe",
            "",
            f"- Skipped: `{safe_report.get('conversation', {}).get('skipped')}`",
            f"- Reason: `{safe_report.get('conversation', {}).get('reason')}`",
            f"- Thread start: `{(safe_report.get('conversation', {}).get('thread_start') or {}).get('ok')}`",
            f"- Turn start: `{(safe_report.get('conversation', {}).get('turn_start') or {}).get('ok')}`",
            f"- Assistant extraction OK: `{(safe_report.get('conversation', {}).get('assistant_output') or {}).get('ok')}`",
            f"- Extraction path: `{(safe_report.get('conversation', {}).get('assistant_output') or {}).get('text_path')}`",
            "",
            "## Redaction",
            "",
            f"- OK: `{safe_report.get('redaction', {}).get('ok')}`",
            f"- Findings: `{safe_report.get('redaction', {}).get('findings')}`",
            f"- Captured stdout lines: `{safe_report.get('redaction', {}).get('stdout_lines')}`",
            f"- Captured stderr lines: `{safe_report.get('redaction', {}).get('stderr_lines')}`",
            "",
            "## Sanitized Artifact",
            "",
            f"- JSON: `{payload_path}`",
            "",
            "No access token, refresh token, bearer token, API key, or JWT-like material is intentionally written to this report.",
        ]
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(lines) + "\n")
    print(f"DISCOVERY_STATUS={safe_report['status']}")
    print(f"DISCOVERY_REPORT={REPORT_PATH}")
    return 0 if safe_report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
PY
