from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.settings import get_settings

from ..registry import ProviderAdapter
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult


PROVIDER_NAME = "codex_direct"
_LOGIN_STATUS_TIMEOUT_SECONDS = 5


@dataclass(frozen=True)
class CodexDirectReadyStatus:
    ready: bool
    provider: str
    codex_binary: str
    codex_home: str
    auth_file_exists: bool
    login_status: str
    error_code: str | None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "ready": self.ready,
            "provider": self.provider,
            "codex_binary": self.codex_binary,
            "codex_home": self.codex_home,
            "auth_file_exists": self.auth_file_exists,
            "login_status": self.login_status,
            "error_code": self.error_code,
        }


class CodexDirectAdapter(ProviderAdapter):
    name = PROVIDER_NAME
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        safety_level="reply_only",
    )

    def __init__(self) -> None:
        settings = get_settings()
        self.enabled = settings.codex_direct_enabled
        self.command = settings.codex_direct_command
        self.codex_home = settings.codex_direct_home
        self.model = settings.codex_direct_model
        self.timeout_seconds = settings.codex_direct_timeout_seconds
        self.max_prompt_chars = settings.codex_direct_max_prompt_chars
        self.require_json = settings.codex_direct_require_json

    def ready_status(self) -> CodexDirectReadyStatus:
        return codex_direct_ready_status(
            enabled=self.enabled,
            command=self.command,
            codex_home=self.codex_home,
            timeout_seconds=self.timeout_seconds,
        )

    async def generate(self, db, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        ready = self.ready_status()
        if not ready.ready:
            return ProviderResult.unavailable(
                self.name,
                ready.error_code or "codex_direct_not_ready",
                _elapsed_ms(started),
            )

        prompt = self._build_prompt(request)
        if len(prompt) > self.max_prompt_chars:
            prompt = prompt[: self.max_prompt_chars]

        try:
            completed = _run_subprocess(
                [self.command, "exec", "--model", self.model, prompt],
                capture_output=True,
                text=True,
                shell=False,
                timeout=self.timeout_seconds,
                env=self._subprocess_env(),
            )
        except Exception as exc:
            if exc.__class__.__name__ == "TimeoutExpired":
                return ProviderResult.unavailable(self.name, "codex_direct_timeout", _elapsed_ms(started))
            return ProviderResult.unavailable(self.name, "codex_direct_exception", _elapsed_ms(started))

        safe_summary = {
            "codex_direct": True,
            "exit_code": completed.returncode,
            "model": self.model,
            "prompt_chars": len(prompt),
        }
        if completed.returncode != 0:
            return ProviderResult(
                ok=False,
                provider=self.name,
                elapsed_ms=_elapsed_ms(started),
                raw_payload_safe_summary=safe_summary,
                error_code="codex_direct_nonzero_exit",
                retryable=True,
                fallback_allowed=True,
            )

        try:
            parsed = _parse_json_object(completed.stdout, require_json=self.require_json)
            structured = self._adapt_structured_output(parsed, request)
        except ValueError as exc:
            return ProviderResult(
                ok=False,
                provider=self.name,
                elapsed_ms=_elapsed_ms(started),
                raw_payload_safe_summary={**safe_summary, "parse_error": str(exc)[:120]},
                error_code=str(exc),
                retryable=False,
                fallback_allowed=True,
            )
        if not str(structured.get("customer_reply") or "").strip():
            return ProviderResult(
                ok=False,
                provider=self.name,
                elapsed_ms=_elapsed_ms(started),
                raw_payload_safe_summary=safe_summary,
                error_code="codex_direct_empty_reply",
                retryable=False,
                fallback_allowed=True,
            )

        return ProviderResult(
            ok=True,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
            model=self.model,
            elapsed_ms=_elapsed_ms(started),
            structured_output=structured,
            raw_payload_safe_summary={
                **safe_summary,
                "provider": self.name,
                "raw_provider": self.name,
                "reply_source": self.name,
                "error_code": None,
            },
            error_code=None,
        )

    def _subprocess_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["HOME"] = self.codex_home
        return env

    def _build_prompt(self, request: ProviderRequest) -> str:
        payload = {
            "body": request.body,
            "recent_context": request.recent_context or [],
            "tracking_fact_summary": request.tracking_fact_summary,
            "tracking_fact_evidence_present": request.tracking_fact_evidence_present,
            "persona_context": (request.metadata or {}).get("persona_context"),
            "knowledge_context": (request.metadata or {}).get("knowledge_context"),
            "safety_policy": (request.metadata or {}).get("safety_policy"),
        }
        return (
            "You are a production customer support reply provider for NexusDesk WebChat.\n"
            "Return one strict JSON object only. Do not include markdown, code fences, hidden reasoning, or prose outside JSON.\n"
            "Never claim live parcel status unless tracking_fact_evidence_present is true and tracking_fact_summary supports it.\n"
            "Do not execute tools, write databases, cancel orders, update addresses, or dispatch outbound messages.\n"
            "Required JSON shape:\n"
            '{"customer_reply":"...","language":"en","intent":"tracking_lookup|greeting|other|handoff_request",'
            '"handoff_required":false,"ticket_should_create":false,"tool_calls":[],"evidence_used":[],"confidence":0.0,"reason":"..."}\n'
            "Request context JSON:\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}"
        )

    @staticmethod
    def _adapt_structured_output(parsed: dict[str, Any], request: ProviderRequest) -> dict[str, Any]:
        reply = str(parsed.get("customer_reply") or "").strip()
        intent = _map_intent(parsed.get("intent"), request)
        handoff_required = bool(parsed.get("handoff_required")) or str(parsed.get("intent") or "") == "handoff_request"
        return {
            "customer_reply": reply,
            "language": str(parsed.get("language") or "en")[:32],
            "intent": "handoff" if handoff_required else intent,
            "tracking_number": _tracking_number(request) if intent == "tracking" else None,
            "handoff_required": handoff_required,
            "handoff_reason": str(parsed.get("reason") or "")[:500] if handoff_required else None,
            "recommended_agent_action": "Review customer request." if handoff_required else None,
            "ticket_should_create": bool(parsed.get("ticket_should_create")) if handoff_required else False,
            "internal_summary": str(parsed.get("reason") or "")[:1000] or None,
            "risk_flags": [],
        }


def codex_direct_ready_status(
    *,
    enabled: bool | None = None,
    command: str | None = None,
    codex_home: str | None = None,
    timeout_seconds: int | None = None,
) -> CodexDirectReadyStatus:
    settings = get_settings()
    effective_enabled = settings.codex_direct_enabled if enabled is None else enabled
    effective_command = command or settings.codex_direct_command
    effective_home = codex_home or settings.codex_direct_home
    effective_timeout = timeout_seconds or settings.codex_direct_timeout_seconds
    auth_path = Path(effective_home) / ".codex" / "auth.json"

    if not effective_enabled:
        return _ready(False, effective_command, effective_home, auth_path, "unknown", "codex_direct_disabled")
    if not _is_executable(effective_command):
        return _ready(False, effective_command, effective_home, auth_path, "unknown", "codex_direct_binary_missing")
    if not auth_path.exists():
        return _ready(False, effective_command, effective_home, auth_path, "unknown", "codex_direct_auth_missing")

    try:
        completed = _run_subprocess(
            [effective_command, "login", "status"],
            capture_output=True,
            text=True,
            shell=False,
            timeout=min(max(int(effective_timeout), 1), _LOGIN_STATUS_TIMEOUT_SECONDS),
            env={**os.environ.copy(), "HOME": effective_home},
        )
    except Exception:
        return _ready(False, effective_command, effective_home, auth_path, "unknown", "codex_direct_not_logged_in")

    output = f"{completed.stdout}\n{completed.stderr}".lower()
    if completed.returncode == 0 and "logged in" in output and "not logged" not in output:
        return _ready(True, effective_command, effective_home, auth_path, "logged_in", None)
    return _ready(False, effective_command, effective_home, auth_path, "not_logged_in", "codex_direct_not_logged_in")


def _ready(
    ready: bool,
    command: str,
    codex_home: str,
    auth_path: Path,
    login_status: str,
    error_code: str | None,
) -> CodexDirectReadyStatus:
    return CodexDirectReadyStatus(
        ready=ready,
        provider=PROVIDER_NAME,
        codex_binary=command,
        codex_home=codex_home,
        auth_file_exists=auth_path.exists(),
        login_status=login_status,
        error_code=error_code,
    )


def _is_executable(command: str) -> bool:
    if not command:
        return False
    resolved = shutil.which(command) if os.path.basename(command) == command else command
    return bool(resolved and os.path.isfile(resolved) and os.access(resolved, os.X_OK))


def _parse_json_object(value: str, *, require_json: bool) -> dict[str, Any]:
    raw = (value or "").strip()
    if not raw:
        raise ValueError("codex_direct_bad_json")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        if require_json:
            raise ValueError("codex_direct_bad_json") from exc
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("codex_direct_bad_json") from exc
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as nested_exc:
            raise ValueError("codex_direct_bad_json") from nested_exc
    if not isinstance(parsed, dict):
        raise ValueError("codex_direct_bad_json")
    return parsed


def _map_intent(value: Any, request: ProviderRequest) -> str:
    intent = str(value or "other").strip()
    if intent == "tracking_lookup":
        return "tracking" if request.tracking_fact_evidence_present and _tracking_number(request) else "tracking_missing_number"
    if intent == "greeting":
        return "greeting"
    if intent == "handoff_request":
        return "handoff"
    return "other"


def _tracking_number(request: ProviderRequest) -> str | None:
    metadata = (request.metadata or {}).get("tracking_fact_metadata")
    if isinstance(metadata, dict):
        value = metadata.get("number") or metadata.get("tracking_number")
        if value:
            return str(value)[:80]
    return None


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _run_subprocess(args: list[str], **kwargs):
    import subprocess

    return subprocess.run(args, **kwargs)
