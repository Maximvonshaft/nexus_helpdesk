from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from ..registry import ProviderAdapter
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult

PROVIDER_NAME = "codex_direct"
_ALLOWED_TOOLS = {"knowledge.search", "speedaf.order.query", "handoff.request.create"}
_TOOL_ALIASES = {
    "speedaf.track": "speedaf.order.query",
    "tracking.extract": "knowledge.search",
    "handoff.create": "handoff.request.create",
    "handoff.request": "handoff.request.create",
    "ticket.handoff_create": "handoff.request.create",
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
_AUTH_ERROR_MARKERS = _NOT_LOGGED_IN_MARKERS + ("auth error", "authentication failed", "invalid token", "expired token")


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
    sandbox_acknowledged: bool
    allow_network_env: bool
    fallback_allowed: bool
    readiness_cache_seconds: int

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
            exec_args_template=os.getenv("CODEX_DIRECT_EXEC_ARGS_TEMPLATE", "exec --model {model} --skip-git-repo-check -").strip() or "exec --model {model} --skip-git-repo-check -",
            sandbox_acknowledged=_env_bool("CODEX_DIRECT_SANDBOX_ACKNOWLEDGED", False),
            allow_network_env=_env_bool("CODEX_DIRECT_ALLOW_NETWORK_ENV", False),
            fallback_allowed=_env_bool("CODEX_DIRECT_FALLBACK_ALLOWED", True),
            readiness_cache_seconds=_int_env("CODEX_DIRECT_READINESS_CACHE_SECONDS", 30, minimum=0, maximum=300),
        )


@dataclass(frozen=True)
class CodexDirectReadiness:
    ready: bool
    error_code: str | None
    safe_summary: dict[str, Any]


class CodexDirectAdapter(ProviderAdapter):
    name = PROVIDER_NAME
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        supports_tracking_context=True,
        safety_level="reply_only_sandbox_required",
    )
    _readiness_cache_lock = threading.RLock()
    _readiness_cache: dict[tuple[Any, ...], tuple[float, CodexDirectReadiness]] = {}

    def __init__(self, config: CodexDirectConfig | None = None):
        self.config = config or CodexDirectConfig.from_env()
        self.app_env = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower() or "development"

    async def readiness_check(self) -> CodexDirectReadiness:
        return await asyncio.to_thread(self._readiness_check_sync)

    async def smoke_check(self) -> dict[str, Any]:
        readiness = await self.readiness_check()
        return {
            "ok": readiness.ready,
            "ready": readiness.ready,
            "provider": self.name,
            "error_code": readiness.error_code,
            "checks": readiness.safe_summary,
        }

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        timings: dict[str, int] = {}

        readiness_started = time.monotonic()
        readiness = await self.readiness_check()
        timings["readiness_ms"] = _elapsed_ms(readiness_started)
        if not readiness.ready:
            summary = dict(readiness.safe_summary)
            summary["latency"] = _latency_summary(started, timings)
            return self._failure(
                readiness.error_code or "codex_direct_unavailable",
                started,
                summary,
                retryable=readiness.error_code in {"codex_direct_timeout", "codex_direct_not_logged_in"},
            )

        prompt_started = time.monotonic()
        prompt = self._build_prompt(request)
        timings["prompt_build_ms"] = _elapsed_ms(prompt_started)

        argv_started = time.monotonic()
        argv = self._generate_argv()
        timeout_seconds = _runtime_timeout_seconds(request.timeout_ms, self.config.timeout_seconds)
        timings["argv_build_ms"] = _elapsed_ms(argv_started)

        subprocess_started = time.monotonic()
        try:
            completed = await asyncio.to_thread(
                self._run_sync,
                argv,
                prompt,
                timeout_seconds,
            )
            timings["subprocess_ms"] = _elapsed_ms(subprocess_started)
        except subprocess.TimeoutExpired:
            timings["subprocess_ms"] = _elapsed_ms(subprocess_started)
            return self._failure(
                "codex_direct_timeout",
                started,
                {
                    "prompt_chars": len(prompt),
                    "argv_name": self._safe_argv_name(argv),
                    "timeout_seconds": timeout_seconds,
                    "timeout_source": "codex_direct_subprocess",
                    "latency": _latency_summary(started, timings),
                },
                retryable=True,
            )
        except OSError:
            timings["subprocess_ms"] = _elapsed_ms(subprocess_started)
            return self._failure(
                "codex_direct_binary_missing",
                started,
                {
                    "prompt_chars": len(prompt),
                    "argv_name": self._safe_argv_name(argv),
                    "latency": _latency_summary(started, timings),
                },
            )

        safe_summary = {
            "provider": self.name,
            "model": self.config.model,
            "prompt_chars": len(prompt),
            "returncode": completed.returncode,
            "stdout_chars": len(completed.stdout or ""),
            "stderr_chars": len(completed.stderr or ""),
            "argv_name": self._safe_argv_name(argv),
            "env_mode": "scrubbed",
            "subprocess_mode": "to_thread_shell_false_stdin",
            "timeout_seconds": timeout_seconds,
            "latency": _latency_summary(started, timings),
        }
        if completed.returncode != 0:
            if _completed_has_auth_error(completed):
                self._clear_readiness_cache()
            return self._failure("codex_direct_nonzero_exit", started, safe_summary, retryable=True)

        output_text = (completed.stdout or "").strip()
        if not output_text:
            return self._failure("codex_direct_empty_reply", started, safe_summary)

        parse_started = time.monotonic()
        try:
            parsed = self._parse_model_output(output_text)
            normalized = self._normalize_output(parsed)
            timings["parse_ms"] = _elapsed_ms(parse_started)
            safe_summary["latency"] = _latency_summary(started, timings)
        except ValueError as exc:
            timings["parse_ms"] = _elapsed_ms(parse_started)
            safe_summary["latency"] = _latency_summary(started, timings)
            safe_summary["parse_error"] = str(exc)[:240]
            return self._failure("codex_direct_bad_json", started, safe_summary)

        if not str(normalized.get("customer_reply") or "").strip():
            safe_summary["latency"] = _latency_summary(started, timings)
            return self._failure("codex_direct_empty_reply", started, safe_summary)

        safe_summary["latency"] = _latency_summary(started, timings)
        return ProviderResult(
            ok=True,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
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

    def _readiness_check_sync(self) -> CodexDirectReadiness:
        auth_meta = self._auth_metadata()
        summary: dict[str, Any] = {
            "provider": self.name,
            "enabled": self.config.enabled,
            "command_configured": bool(self.config.command),
            "binary_name": Path(shlex.split(self.config.command)[0]).name if self.config.command else None,
            "home": str(self.config.home),
            "codex_home": str(self.auth_path.parent),
            "model_configured": bool(self.config.model),
            "sandbox_acknowledged": self.config.sandbox_acknowledged,
            "env_mode": "scrubbed",
            "subprocess_mode": "shell_false_stdin",
            "readiness_cache_hit": False,
            "readiness_cache_ttl_seconds": self.config.readiness_cache_seconds,
            "auth_mtime_present": auth_meta["mtime_ns"] is not None,
        }
        if not self.config.enabled:
            return CodexDirectReadiness(False, "codex_direct_disabled", summary)
        if self.app_env == "production" and not self.config.sandbox_acknowledged:
            return CodexDirectReadiness(False, "codex_direct_sandbox_not_acknowledged", summary)

        command_tokens = self._command_tokens()
        binary = self._resolve_binary(command_tokens)
        summary["binary_present"] = bool(binary)
        if not binary:
            return CodexDirectReadiness(False, "codex_direct_binary_missing", summary)

        summary["auth_present"] = self.auth_path.exists()
        summary["auth_path"] = str(self.auth_path.parent / "auth.json")
        if not self.auth_path.exists():
            return CodexDirectReadiness(False, "codex_direct_auth_missing", summary)

        cache_key = self._readiness_cache_key(auth_meta)
        cached = self._cached_readiness(cache_key)
        if cached is not None:
            cached_summary = dict(cached.safe_summary)
            cached_summary.update(
                {
                    "readiness_cache_hit": True,
                    "readiness_cache_ttl_seconds": self.config.readiness_cache_seconds,
                    "auth_mtime_present": auth_meta["mtime_ns"] is not None,
                }
            )
            return CodexDirectReadiness(True, None, cached_summary)

        login_started = time.monotonic()
        try:
            status = self._run_sync(command_tokens + ["login", "status"], None, min(5.0, float(self.config.timeout_seconds)))
            summary["login_status_ms"] = _elapsed_ms(login_started)
        except subprocess.TimeoutExpired:
            summary["login_status_ms"] = _elapsed_ms(login_started)
            summary["login_status_checked"] = False
            return CodexDirectReadiness(False, "codex_direct_timeout", summary)
        except OSError:
            summary["login_status_ms"] = _elapsed_ms(login_started)
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
        readiness = CodexDirectReadiness(True, None, summary)
        self._store_readiness_cache(cache_key, readiness)
        return readiness

    def _auth_metadata(self) -> dict[str, int | None]:
        try:
            stat = self.auth_path.stat()
        except OSError:
            return {"mtime_ns": None, "size": None}
        return {"mtime_ns": int(stat.st_mtime_ns), "size": int(stat.st_size)}

    def _readiness_cache_key(self, auth_meta: dict[str, int | None]) -> tuple[Any, ...]:
        return (
            self.config.command,
            str(self.config.home),
            str(self.auth_path),
            self.config.model,
            self.config.sandbox_acknowledged,
            auth_meta.get("mtime_ns"),
            auth_meta.get("size"),
        )

    def _cached_readiness(self, cache_key: tuple[Any, ...]) -> CodexDirectReadiness | None:
        if self.config.readiness_cache_seconds <= 0:
            return None
        now = time.monotonic()
        with self._readiness_cache_lock:
            cached = self._readiness_cache.get(cache_key)
            if not cached:
                return None
            expires_at, readiness = cached
            if expires_at <= now:
                self._readiness_cache.pop(cache_key, None)
                return None
            return readiness if readiness.ready else None

    def _store_readiness_cache(self, cache_key: tuple[Any, ...], readiness: CodexDirectReadiness) -> None:
        if self.config.readiness_cache_seconds <= 0 or not readiness.ready:
            return
        with self._readiness_cache_lock:
            self._readiness_cache[cache_key] = (time.monotonic() + self.config.readiness_cache_seconds, readiness)

    def _clear_readiness_cache(self) -> None:
        with self._readiness_cache_lock:
            self._readiness_cache.clear()

    def _failure(self, error_code: str, started: float, summary: dict[str, Any] | None = None, *, retryable: bool = False) -> ProviderResult:
        return ProviderResult(
            ok=False,
            provider=self.name,
            raw_provider=self.name,
            reply_source=self.name,
            model=self.config.model,
            elapsed_ms=_elapsed_ms(started),
            raw_payload_safe_summary={"codex_direct": True, **(summary or {})},
            structured_output=None,
            error_code=error_code,
            retryable=retryable,
            fallback_allowed=self.config.fallback_allowed,
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
        return shutil.which(binary)

    def _subprocess_env(self) -> dict[str, str]:
        allowed = {"PATH", "LANG", "LC_ALL", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}
        if self.config.allow_network_env:
            allowed.update({"HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"})
        env = {key: value for key, value in os.environ.items() if key in allowed and value}
        if self.app_env in {"test", "development", "local"}:
            env["APP_ENV"] = self.app_env
            for key, value in os.environ.items():
                if key.startswith("CODEX_FAKE_"):
                    env[key] = value
        env["HOME"] = str(self.config.home)
        env["CODEX_HOME"] = str(self.auth_path.parent)
        env.setdefault("NO_COLOR", "1")
        return env

    def _run_sync(self, argv: list[str], input_text: str | None, timeout_seconds: float) -> subprocess.CompletedProcess[str]:
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
        return command_tokens + shlex.split(template)

    @staticmethod
    def _safe_argv_name(argv: list[str]) -> str | None:
        return Path(argv[0]).name if argv else None

    def _build_prompt(self, request: ProviderRequest) -> str:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        knowledge_context = metadata.get("knowledge_context") if isinstance(metadata.get("knowledge_context"), dict) else {}
        persona_context = metadata.get("persona_context") if isinstance(metadata.get("persona_context"), dict) else {}
        tracking_fact_metadata = metadata.get("tracking_fact_metadata") if isinstance(metadata.get("tracking_fact_metadata"), dict) else {}
        reply_repair = metadata.get("reply_repair") if isinstance(metadata.get("reply_repair"), dict) else {}
        recent_context = request.recent_context if isinstance(request.recent_context, list) else []
        compact_no_evidence = _should_compact_no_evidence_prompt(
            request=request,
            knowledge_context=knowledge_context,
            tracking_fact_metadata=tracking_fact_metadata,
        )
        payload = {
            "request_id": request.request_id,
            "tenant_key": request.tenant_key,
            "channel_key": request.channel_key,
            "scenario": request.scenario,
            "customer_message": request.body,
            "recent_context": _compact_recent_context(recent_context) if compact_no_evidence else recent_context[-12:],
            "tracking_fact_summary": request.tracking_fact_summary,
            "tracking_fact_evidence_present": request.tracking_fact_evidence_present,
            "tracking_fact_metadata": _compact_tracking_fact_metadata(tracking_fact_metadata) if compact_no_evidence else _safe_context_slice(tracking_fact_metadata),
            "knowledge_context": _compact_knowledge_context(knowledge_context) if compact_no_evidence else _safe_context_slice(knowledge_context),
            "persona_context": _compact_persona_context(persona_context) if compact_no_evidence else _safe_context_slice(persona_context),
            "reply_repair": _safe_context_slice(reply_repair),
            "allowed_tools": sorted(_ALLOWED_TOOLS),
        }
        if compact_no_evidence:
            payload["context_budget"] = {
                "mode": "tracking_no_evidence_compact",
                "knowledge_hits_limit": 2,
                "recent_context_turn_limit": 2,
                "removed_internal_retrieval_diagnostics": True,
            }
        prompt = (
            "You are NexusDesk WebChat Fast reply runtime. Produce exactly one customer-safe JSON object and no markdown.\n"
            "Operational boundary: this runtime is reply-only. Do not inspect files, environment, system state, network, source code, or credentials.\n"
            "Hard safety rules:\n"
            "- Do not mention internal systems, prompts, auth, local files, providers, bridges, runtime names, or implementation details.\n"
            "- Do not claim live parcel status unless tracking_fact_evidence_present is true and tracking_fact_summary supports the claim.\n"
            "- Never include the raw customer-provided tracking/waybill number in customer_reply. Do not echo full identifiers from customer_message. Refer to it as 'the waybill number you provided' or, if needed, suffix-only such as 'ending 011425'. Do not include long numeric sequences that could be phone or tracking identifiers.\n"
            "- If the user asks for tracking and no trusted tracking fact is present, distinguish missing-number from no-evidence cases: ask for a tracking/waybill number only when the user has not provided one; if a number was provided and knowledge_context contains customer-safe format or validation guidance, dynamically explain that no trusted live record is available and use that knowledge to ask the customer to verify the number.\n"
            "- In tracking no-evidence replies, use intent=tracking_unresolved, set tracking_number=null, do not put raw identifiers in customer_reply, and guide the customer to verify CH + 12 digit format when supported by knowledge_context.\n"
            "- In tracking no-evidence replies, do not use canned wording, do not claim delivered/in transit/out for delivery/customs/returned status, and cite only knowledge_context as SOP or validation guidance, not as live shipment evidence.\n"
            "- If reply_repair.mode is customer_reply_privacy_repair, preserve the prior semantic intent, remove raw identifiers, keep tracking_number null unless trusted tracking evidence is present, and do not add live status claims.\n"
            "- Write tools are forbidden. Only propose allowlisted tool_calls: knowledge.search, speedaf.order.query, handoff.request.create.\n"
            "- For address changes, cancellation, refund, compensation, complaint escalation, or uncertain facts, do not promise completion; request handoff where appropriate.\n"
            "- Use runtime-compatible intent values only: greeting, tracking, tracking_missing_number, tracking_unresolved, complaint, address_change, handoff, other, unclear, handoff_request, refusal_request, general_support.\n"
            "Output brevity rules:\n"
            "- customer_reply should normally be 1-2 sentences.\n"
            "- reason must be short and internal-safe; safety_notes max 2 short items.\n"
            "- evidence_used max 2 relevant evidence records.\n"
            "- tool_calls must be [] unless a real allowlisted tool proposal is needed.\n"
            "- Do not add verbose explanation when no trusted tracking fact exists.\n"
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
        intent = _normalize_intent(parsed.get("intent"))
        return {
            "customer_reply": reply,
            "language": _clean_string(parsed.get("language"), 32) or "unknown",
            "intent": intent,
            "tracking_number": _clean_string(parsed.get("tracking_number"), 80),
            "handoff_required": handoff_required,
            "handoff_reason": _clean_string(parsed.get("handoff_reason"), 240),
            "recommended_agent_action": _clean_string(parsed.get("recommended_agent_action"), 500),
            "ticket_should_create": bool(parsed.get("ticket_should_create", handoff_required)),
            "tool_calls": _normalize_tool_calls(parsed.get("tool_calls")),
            "evidence_used": _normalize_evidence(parsed.get("evidence_used")),
            "confidence": _clamp_float(parsed.get("confidence"), default=0.0),
            "reason": _clean_string(parsed.get("reason"), 500) or "codex_direct_decision",
            "risk_level": _clean_string(parsed.get("risk_level"), 32) or ("medium" if handoff_required else "low"),
            "next_action": _clean_string(parsed.get("next_action"), 80) or ("request_handoff" if handoff_required else "reply"),
            "safety_notes": _normalize_string_list(parsed.get("safety_notes"), max_items=2, max_chars=160),
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


def _runtime_timeout_seconds(timeout_ms: int | None, provider_timeout_seconds: int) -> float:
    if timeout_ms is None or timeout_ms <= 0:
        return float(provider_timeout_seconds)
    return max(0.1, min(float(provider_timeout_seconds), float(timeout_ms) / 1000.0))


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _latency_summary(started: float, timings: dict[str, int] | None = None) -> dict[str, int]:
    summary = {key: int(value) for key, value in (timings or {}).items() if isinstance(value, int) and value >= 0}
    summary["total_ms"] = _elapsed_ms(started)
    return summary


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
    for item in value[:2]:
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


def _should_compact_no_evidence_prompt(
    *,
    request: ProviderRequest,
    knowledge_context: dict[str, Any],
    tracking_fact_metadata: dict[str, Any],
) -> bool:
    if request.scenario != "webchat_fast_reply" or request.tracking_fact_evidence_present:
        return False
    if bool(tracking_fact_metadata.get("fact_evidence_present")):
        return False
    tool_status = str(tracking_fact_metadata.get("tool_status") or "").strip().lower()
    if tool_status and tool_status not in {"error", "failed", "failure", "timeout", "not_found"}:
        return False
    return bool(_compact_knowledge_hits(knowledge_context, limit=1))


def _compact_recent_context(recent_context: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in recent_context[-4:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        if role in {"customer", "visitor", "user"}:
            normalized_role = "customer"
        elif role in {"assistant", "agent", "ai", "bot"}:
            normalized_role = "assistant"
        else:
            continue
        text = _clean_string(item.get("text") or item.get("body") or item.get("content"), 240)
        if text:
            out.append({"role": normalized_role, "text": text})
    return out


def _compact_tracking_fact_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "fact_evidence_present",
        "tool_status",
        "failure_reason",
        "tracking_fact_failure_reason",
        "tracking_number_hash",
        "tracking_number_suffix",
        "waybill_suffix",
        "pii_redacted",
    }
    compact = {key: _safe_context_slice(value) for key, value in metadata.items() if key in allowed}
    if "failure_reason" not in compact and metadata.get("tracking_fact_failure_reason"):
        compact["failure_reason"] = _clean_string(metadata.get("tracking_fact_failure_reason"), 160)
    return compact


def _compact_persona_context(persona_context: dict[str, Any]) -> dict[str, Any]:
    allowed = {"profile_key", "name", "tone", "language", "locale", "brand_voice", "reply_style"}
    return {key: _safe_context_slice(value) for key, value in persona_context.items() if key in allowed}


def _compact_knowledge_context(knowledge_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval": knowledge_context.get("retrieval") or "hybrid_rag_v2",
        "total_matches": knowledge_context.get("total_matches"),
        "hits": _compact_knowledge_hits(knowledge_context, limit=2),
    }


def _compact_knowledge_hits(knowledge_context: dict[str, Any], *, limit: int) -> list[dict[str, Any]]:
    raw_hits = knowledge_context.get("hits")
    hits = raw_hits if isinstance(raw_hits, list) else []
    compact: list[dict[str, Any]] = []
    for item in hits:
        if not isinstance(item, dict) or not _is_tracking_guidance_hit(item):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        source_metadata = item.get("source_metadata") if isinstance(item.get("source_metadata"), dict) else {}
        answer_text = (
            item.get("fact_answer")
            or item.get("direct_answer")
            or item.get("answer")
            or item.get("summary")
            or item.get("text")
        )
        compact_item = {
            "item_key": _clean_string(item.get("item_key") or metadata.get("item_key") or source_metadata.get("item_key"), 160),
            "title": _clean_string(item.get("title"), 160),
            "answer_mode": _clean_string(item.get("answer_mode") or metadata.get("answer_mode") or source_metadata.get("answer_mode"), 80),
            "knowledge_kind": _clean_string(item.get("knowledge_kind") or metadata.get("knowledge_kind") or source_metadata.get("knowledge_kind"), 80),
            "fact_status": _clean_string(item.get("fact_status") or metadata.get("fact_status") or source_metadata.get("fact_status"), 80),
            "answer": _clean_string(answer_text, 500),
            "source_version": item.get("source_version") or metadata.get("source_version") or source_metadata.get("source_version"),
            "published_version": item.get("published_version") or metadata.get("published_version") or source_metadata.get("published_version"),
        }
        compact.append({key: value for key, value in compact_item.items() if value is not None})
        if len(compact) >= limit:
            break
    return compact


def _is_tracking_guidance_hit(item: dict[str, Any]) -> bool:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    source_metadata = item.get("source_metadata") if isinstance(item.get("source_metadata"), dict) else {}
    knowledge_kind = str(item.get("knowledge_kind") or metadata.get("knowledge_kind") or source_metadata.get("knowledge_kind") or "").lower()
    answer_mode = str(item.get("answer_mode") or metadata.get("answer_mode") or source_metadata.get("answer_mode") or "").lower()
    haystack = " ".join(
        str(value or "")
        for value in (
            item.get("item_key"),
            item.get("title"),
            item.get("summary"),
            item.get("text"),
            item.get("fact_answer"),
            item.get("direct_answer"),
            item.get("answer"),
        )
    ).lower()
    business_or_guided = knowledge_kind == "business_fact" or answer_mode in {"guided_answer", "direct_answer"}
    tracking_related = any(term in haystack for term in ("waybill", "tracking", "运单", "单号", "ch + 12", "ch followed by 12", "12 位"))
    return business_or_guided and tracking_related


def _safe_context_slice(value: Any) -> Any:
    if isinstance(value, dict):
        sliced: dict[str, Any] = {}
        for key, item in list(value.items())[:40]:
            if str(key).lower() in {"raw_payload", "auth", "token", "access_token", "refresh_token", "secret", "password"}:
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


def _completed_has_auth_error(completed: subprocess.CompletedProcess[str]) -> bool:
    combined = f"{completed.stdout or ''}\n{completed.stderr or ''}".lower()
    return any(marker in combined for marker in _AUTH_ERROR_MARKERS)
