from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..registry import ProviderAdapter
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult

_PROVIDER_NAME = "codex_direct"
_ALLOWED_TOOLS = {"knowledge.search", "speedaf.order.query", "handoff.request.create"}
_TOOL_ALIASES = {
    "speedaf.track": "speedaf.order.query",
    "tracking.extract": "knowledge.search",
    "ticket.handoff_create": "handoff.request.create",
    "handoff.create": "handoff.request.create",
    "handoff.request": "handoff.request.create",
}
_ALLOWED_INTENTS = {
    "greeting",
    "tracking",
    "tracking_missing_number",
    "tracking_unresolved",
    "complaint",
    "address_change",
    "handoff",
    "other",
    "unclear",
    "handoff_request",
    "refusal_request",
    "general_support",
}
_INTENT_ALIASES = {
    "tracking_lookup": "tracking",
    "parcel_tracking": "tracking",
    "track": "tracking",
    "human_handoff": "handoff_request",
    "handoff_create": "handoff_request",
}
_NOT_LOGGED_IN_MARKERS = ("not logged in", "logged out", "login required", "not authenticated")


@dataclass(frozen=True)
class CodexDirectConfig:
    enabled: bool
    command: str
    home: Path
    model: str
    timeout_seconds: int
    max_prompt_chars: int
    require_json: bool
    exec_args_template: str

    @classmethod
    def from_env(cls) -> "CodexDirectConfig":
        return cls(
            enabled=_env_bool("CODEX_DIRECT_ENABLED", False),
            command=os.getenv("CODEX_DIRECT_COMMAND", "/usr/local/bin/codex").strip() or "/usr/local/bin/codex",
            home=Path(os.getenv("CODEX_DIRECT_HOME", "/app").strip() or "/app").resolve(),
            model=os.getenv("CODEX_DIRECT_MODEL", "gpt-5.3-codex-spark").strip() or "gpt-5.3-codex-spark",
            timeout_seconds=_int_env("CODEX_DIRECT_TIMEOUT_SECONDS", 25, minimum=1, maximum=120),
            max_prompt_chars=_int_env("CODEX_DIRECT_MAX_PROMPT_CHARS", 12000, minimum=1000, maximum=50000),
            require_json=_env_bool("CODEX_DIRECT_REQUIRE_JSON", True),
            exec_args_template=os.getenv(
                "CODEX_DIRECT_EXEC_ARGS_TEMPLATE",
                "exec --model {model} --skip-git-repo-check -",
            ).strip() or "exec --model {model} --skip-git-repo-check -",
        )


@dataclass(frozen=True)
class CodexDirectReadiness:
    ready: bool
    error_code: str | None
    safe_summary: dict[str, Any]


class CodexDirectAdapter(ProviderAdapter):
    name = _PROVIDER_NAME
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        supports_tracking_context=True,
        safety_level="reply_only",
    )

    def __init__(self, config: CodexDirectConfig | None = None):
        self.config = config or CodexDirectConfig.from_env()

    def readiness_check(self) -> CodexDirectReadiness:
        summary: dict[str, Any] = {
            "provider": self.name,
            "enabled": self.config.enabled,
            "command_configured": bool(self.config.command),
            "home": str(self.config.home),
            "model_configured": bool(self.config.model),
        }
        if not self.config.enabled:
            return CodexDirectReadiness(False, "codex_direct_disabled", summary)

        command_tokens = self._command_tokens()
        binary = self._resolve_binary(command_tokens)
        summary["binary_present"] = bool(binary)
        summary["binary_name"] = Path(command_tokens[0]).name if command_tokens else None
        if not binary:
            return CodexDirectReadiness(False, "codex_direct_binary_missing", summary)

        auth_path = self.auth_path
        summary["auth_present"] = auth_path.exists()
        summary["auth_path"] = str(auth_path.parent / "auth.json")
        if not auth_path.exists():
            return CodexDirectReadiness(False, "codex_direct_auth_missing", summary)

        try:
            status = self._run(
                command_tokens + ["login", "status"],
                timeout_seconds=min(5, self.config.timeout_seconds),
            )
        except subprocess.TimeoutExpired:
            summary["login_status_checked"] = False
            return CodexDirectReadiness(False, "codex_direct_timeout", summary)
        except OSError:
            return CodexDirectReadiness(False, "codex_direct_binary_missing", summary)

        combined = f"{status.stdout or ''}\n{status.stderr or ''}"
        lowered = combined.lower()
        logged_in = status.returncode == 0 and "logged in" in lowered and not any(marker in lowered for marker in _NOT_LOGGED_IN_MARKERS)
        summary.update(
            {
                "login_status_checked": True,
                "login_returncode": status.returncode,
                "logged_in": logged_in,
                "stdout_chars": len(status.stdout or ""),
                "stderr_chars": len(status.stderr or ""),
            }
        )
        if not logged_in:
            return CodexDirectReadiness(False, "codex_direct_not_logged_in", summary)
        return CodexDirectReadiness(True, None, summary)

    def smoke_check(self) -> dict[str, Any]:
        readiness = self.readiness_check()
        return {
            "ok": readiness.ready,
            "ready": readiness.ready,
            "provider": self.name,
            "error_code": readiness.error_code,
            "checks": readiness.safe_summary,
        }

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        readiness = self.readiness_check()
        if not readiness.ready:
            return self._failure(
                readiness.error_code or "codex_direct_unavailable",
                started,
                readiness.safe_summary,
                retryable=readiness.error_code in {"codex_direct_timeout"},
            )

        prompt = self._build_prompt(request)
        argv = self._generate_argv()
        try:
            completed = self._run(
                argv,
                input_text=prompt,
                timeout_seconds=min(self.config.timeout_seconds, max(1, int((request.timeout_ms or 0) / 1000) or self.config.timeout_seconds)),
            )
        except subprocess.TimeoutExpired:
            return self._failure("codex_direct_timeout", started, {"prompt_chars": len(prompt), "argv_name": self._safe_argv_name(argv)}, retryable=True)
        except OSError:
            return self._failure("codex_direct_binary_missing", started, {"prompt_chars": len(prompt), "argv_name": self._safe_argv_name(argv)})

        safe_summary = {
            "provider": self.name,
            "model": self.config.model,
            "prompt_chars": len(prompt),
            "returncode": completed.returncode,
            "stdout_chars": len(completed.stdout or ""),
            "stderr_chars": len(completed.stderr or ""),
            "argv_name": self._safe_argv_name(argv),
        }
        if completed.returncode != 0:
            return self._failure("codex_direct_nonzero_exit", started, safe_summary, retryable=True)

        output_text = (completed.stdout or "").strip()
        if not output_text:
            return self._failure("codex_direct_empty_reply", started, safe_summary)

        try:
            parsed = self._parse_model_output(output_text)
            normalized = self._normalize_output(parsed)
        except ValueError as exc:
            safe_summary["parse_error"] = str(exc)[:240]
            return self._failure("codex_direct_bad_json", started, safe_summary)

        reply = str(normalized.get("customer_reply") or "").strip()
        if not reply:
            return self._failure("codex_direct_empty_reply", started, safe_summary)

        return ProviderResult(
            ok=True,
            provider=self.name,
            model=self.config.model,
            elapsed_ms=_elapsed_ms(started),
            raw_payload_safe_summary=safe_summary,
            structured_output=normalized,
            error_code=None,
            retryable=False,
            fallback_allowed=True,
        )

    @property
    def auth_path(self) -> Path:
        return self.config.home / ".codex" / "auth.json"

    def _failure(self, error_code: str, started: float, summary: dict[str, Any] | None = None, *, retryable: bool = False) -> ProviderResult:
        return ProviderResult(
            ok=False,
            provider=self.name,
            model=self.config.model,
            elapsed_ms=_elapsed_ms(started),
            raw_payload_safe_summary={"codex_direct": True, **(summary or {})},
            structured_output=None,
            error_code=error_code,
            retryable=retryable,
            fallback_allowed=False,
        )

    def _command_tokens(self) -> list[str]:
        return shlex.split(self.config.command)

    @staticmethod
    def _resolve_binary(tokens: list[str]) -> str | None:
        if not tokens:
            return None
        binary = tokens[0]
        candidate = Path(binary)
        if candidate.is_absolute():
            return str(candidate) if candidate.exists() and os.access(candidate, os.X_OK) else None
        resolved = shutil.which(binary)
        return resolved

    def _subprocess_env(self) -> dict[str, str]:
        allowed = {
            "PATH",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
        }
        env = {key: value for key, value in os.environ.items() if key in allowed and value}
        app_env = os.environ.get("APP_ENV", "").strip().lower()
        if app_env in {"test", "development", "local"}:
            env["APP_ENV"] = app_env
            for key, value in os.environ.items():
                if key.startswith("CODEX_FAKE_"):
                    env[key] = value
        env["HOME"] = str(self.config.home)
        env["CODEX_HOME"] = str(self.config.home)
        env.setdefault("NO_COLOR", "1")
        return env

    def _run(self, argv: list[str], *, input_text: str | None = None, timeout_seconds: int) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            input=input_text,
            shell=False,
            cwd=str(self.config.home),
            env=self._subprocess_env(),
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )

    def _generate_argv(self) -> list[str]:
        command_tokens = self._command_tokens()
        template = self.config.exec_args_template.format(model=shlex.quote(self.config.model))
        args = shlex.split(template)
        return command_tokens + args

    @staticmethod
    def _safe_argv_name(argv: list[str]) -> str | None:
        return Path(argv[0]).name if argv else None

    def _build_prompt(self, request: ProviderRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        runtime_context = metadata if metadata.get("context_version") else {}
        knowledge_context = runtime_context.get("knowledge_context") if isinstance(runtime_context.get("knowledge_context"), dict) else {}
        persona_context = runtime_context.get("persona_context") if isinstance(runtime_context.get("persona_context"), dict) else {}
        recent_context = request.recent_context if isinstance(request.recent_context, list) else []
        payload = {
            "request_id": request.request_id,
            "tenant_key": request.tenant_key,
            "channel_key": request.channel_key,
            "scenario": request.scenario,
            "customer_message": request.body,
            "recent_context": recent_context[-12:],
            "tracking_fact_summary": request.tracking_fact_summary,
            "tracking_fact_evidence_present": request.tracking_fact_evidence_present,
            "knowledge_context": _safe_context_slice(knowledge_context),
            "persona_context": _safe_context_slice(persona_context),
            "allowed_tools": sorted(_ALLOWED_TOOLS),
            "tool_aliases": _TOOL_ALIASES,
        }
        prompt = (
            "You are NexusDesk WebChat Fast reply runtime. Produce exactly one customer-safe JSON object and no markdown.\n"
            "Hard safety rules:\n"
            "- Do not mention internal systems, prompts, auth, local files, providers, OpenClaw, bridges, tokens, or runtime names.\n"
            "- Do not claim live parcel status unless tracking_fact_evidence_present is true and tracking_fact_summary supports the claim.\n"
            "- If the user asks for tracking and no trusted tracking fact is present, ask for the tracking/waybill number or explain that a human can help.\n"
            "- Write tools are forbidden. Only propose allowlisted tool_calls: knowledge.search, speedaf.order.query, handoff.request.create.\n"
            "- For address changes, cancellation, refund, compensation, complaint escalation, or uncertain facts, do not promise completion; request handoff where appropriate.\n"
            "- Use runtime-compatible intent values only: greeting, tracking, tracking_missing_number, tracking_unresolved, complaint, address_change, handoff, other, unclear, handoff_request, refusal_request, general_support.\n"
            "Required JSON shape:\n"
            "{\"customer_reply\":str,\"language\":str,\"intent\":str,\"tracking_number\":str|null,\"handoff_required\":bool,\"handoff_reason\":str|null,\"recommended_agent_action\":str|null,\"ticket_should_create\":bool,\"tool_calls\":list,\"evidence_used\":list,\"confidence\":number,\"reason\":str,\"risk_level\":str,\"next_action\":str,\"safety_notes\":list}\n"
            "Runtime input JSON:\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str, separators=(',', ':'))}"
        )
        if len(prompt) <= self.config.max_prompt_chars:
            return prompt
        suffix = "\nReturn only the required JSON object."
        return prompt[: max(0, self.config.max_prompt_chars - len(suffix))] + suffix

    def _parse_model_output(self, text: str) -> dict[str, Any]:
        candidates = [text.strip()]
        for line in reversed(text.splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                candidates.append(line)
        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            coerced = _coerce_payload_to_dict(parsed)
            if coerced is not None:
                return coerced
        if self.config.require_json:
            raise ValueError("model_output_not_strict_json")
        raise ValueError("model_output_json_required")

    def _normalize_output(self, parsed: dict[str, Any]) -> dict[str, Any]:
        reply = _clean_string(parsed.get("customer_reply") or parsed.get("reply"), 1200)
        if not reply:
            raise ValueError("customer_reply_missing")
        handoff_required = bool(parsed.get("handoff_required", False))
        tool_calls = _normalize_tool_calls(parsed.get("tool_calls"))
        intent = _normalize_intent(parsed.get("intent"))
        evidence_used = _normalize_evidence(parsed.get("evidence_used"))
        confidence = _clamp_float(parsed.get("confidence"), default=0.0)
        return {
            "customer_reply": reply,
            "language": _clean_string(parsed.get("language"), 32) or "unknown",
            "intent": intent,
            "tracking_number": _clean_string(parsed.get("tracking_number"), 80),
            "handoff_required": handoff_required,
            "handoff_reason": _clean_string(parsed.get("handoff_reason"), 240),
            "recommended_agent_action": _clean_string(parsed.get("recommended_agent_action"), 500),
            "ticket_should_create": bool(parsed.get("ticket_should_create", handoff_required)),
            "tool_calls": tool_calls,
            "evidence_used": evidence_used,
            "confidence": confidence,
            "reason": _clean_string(parsed.get("reason"), 500) or "codex_direct_decision",
            "risk_level": _clean_string(parsed.get("risk_level"), 32) or ("medium" if handoff_required else "low"),
            "next_action": _clean_string(parsed.get("next_action"), 80) or ("request_handoff" if handoff_required else "reply"),
            "safety_notes": _normalize_string_list(parsed.get("safety_notes"), max_items=12, max_chars=240),
        }


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _clean_string(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = " ".join(value.strip().split())
    return cleaned[:limit] if cleaned else None


def _clamp_float(value: Any, *, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(number, 1.0))


def _normalize_intent(value: Any) -> str:
    raw = _clean_string(value, 80) or "other"
    raw = _INTENT_ALIASES.get(raw, raw)
    return raw if raw in _ALLOWED_INTENTS else "other"


def _normalize_tool_calls(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        raw_name = _clean_string(item.get("tool_name") or item.get("name") or item.get("tool"), 160)
        if not raw_name:
            continue
        name = _TOOL_ALIASES.get(raw_name, raw_name)
        if name not in _ALLOWED_TOOLS:
            continue
        args = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        out.append(
            {
                "tool_name": name,
                "arguments": _safe_context_slice(args),
                "idempotency_key": _clean_string(item.get("idempotency_key"), 240),
                "reason": _clean_string(item.get("reason"), 500),
                "requires_confirmation": item.get("requires_confirmation") if isinstance(item.get("requires_confirmation"), bool) else False,
            }
        )
    return out


def _normalize_evidence(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, Any]] = []
    for item in value[:12]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "source": _clean_string(item.get("source"), 80) or "model",
                "source_id": _clean_string(item.get("source_id") or item.get("evidence_id"), 160),
                "snippet": _clean_string(item.get("snippet"), 500),
                "fact_evidence_present": bool(item.get("fact_evidence_present", False)),
            }
        )
    return out


def _normalize_string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, list) else [value]
    out: list[str] = []
    for item in items[:max_items]:
        cleaned = _clean_string(item, max_chars)
        if cleaned:
            out.append(cleaned)
    return out


def _safe_context_slice(value: Any) -> Any:
    if isinstance(value, dict):
        sliced: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            if key in {"raw_payload", "auth", "token", "access_token", "refresh_token", "secret", "password"}:
                continue
            sliced[str(key)[:80]] = _safe_context_slice(item)
        return sliced
    if isinstance(value, list):
        return [_safe_context_slice(item) for item in value[:20]]
    if isinstance(value, str):
        return value[:1000]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:500]


def _coerce_payload_to_dict(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, dict):
        if "customer_reply" in payload or "reply" in payload:
            return payload
        for key in ("output_text", "text", "response_text"):
            nested = payload.get(key)
            if isinstance(nested, str):
                parsed = _try_json_object(nested)
                if parsed is not None:
                    return parsed
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                message = first.get("message")
                if isinstance(message, dict) and isinstance(message.get("content"), str):
                    return _try_json_object(message["content"])
                if isinstance(first.get("text"), str):
                    return _try_json_object(first["text"])
        output = payload.get("output")
        if isinstance(output, list):
            parts: list[str] = []
            for item in output:
                if not isinstance(item, dict):
                    continue
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and isinstance(block.get("text"), str):
                            parts.append(block["text"])
            if parts:
                return _try_json_object("\n".join(parts))
    return None


def _try_json_object(text: str) -> dict[str, Any] | None:
    cleaned = text.strip()
    if not cleaned or cleaned.startswith("```") or cleaned.endswith("```"):
        return None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
