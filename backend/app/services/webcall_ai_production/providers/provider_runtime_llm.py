from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any, Coroutine

from app.db import SessionLocal
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult
from sqlalchemy.orm import Session

from .base import LLMProvider, LLMResult, ProviderError

_DEFAULT_CONTRACT = "nexus_webchat_runtime_reply_v1"
_DEFAULT_PROVIDER = "router"
_DEFAULT_SCENARIO = "webcall_ai_decision"
_DEFAULT_CHANNEL = "webcall_ai"
_DEFAULT_TENANT = "default"
_ROUTER_ALIASES = frozenset({"router", "private_ai_runtime"})


class ProviderRuntimeLLMProvider(LLMProvider):
    provider_name = "provider_runtime"

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        request = _build_request(text=text, language=language)
        db = SessionLocal()
        try:
            result = _run_async(_route_request(db, request))
            if not result.ok or not result.structured_output:
                raise ProviderError(self.provider_name, result.error_code or "provider_runtime_unavailable")
            return _to_llm_result(result)
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(self.provider_name, "provider_runtime_exception") from exc
        finally:
            db.close()


async def _route_request(db: Session, request: ProviderRequest) -> ProviderResult:
    provider_alias = _env("WEBCALL_AI_PROVIDER_RUNTIME_PROVIDER", _DEFAULT_PROVIDER)
    router = ProviderRuntimeRouter(db)
    if provider_alias not in _ROUTER_ALIASES:
        router._write_audit(
            request,
            "generate",
            "skipped",
            "router",
            0,
            {"provider_alias_valid": False},
            "provider_runtime_provider_alias_invalid",
        )
        return ProviderResult.unavailable(
            "router",
            "provider_runtime_provider_alias_invalid",
            0,
            fallback_allowed=False,
        )
    return await router.route(request)


def _build_request(*, text: str, language: str | None) -> ProviderRequest:
    body = (text or "").strip()
    lang = (language or "en").strip() or "en"
    metadata = {
        "persona_context": None,
        "knowledge_context": {"retrieval": "unavailable", "total_matches": 0, "locked_facts": [], "hits": []},
        "safety_policy": {
            "knowledge_scope": "voice_transcript_only_without_tracking_evidence",
            "tracking_truth_boundary": "Parcel live status requires tracking_fact_evidence_present=true and trusted tracking_fact_summary.",
        },
        "source": "webcall_ai_production",
        "language": lang,
    }
    return ProviderRequest(
        request_id=f"webcall-ai-llm-{uuid.uuid4().hex}",
        tenant_id=_env("WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID", _DEFAULT_TENANT),
        tenant_key=_env("WEBCALL_AI_PROVIDER_RUNTIME_TENANT_KEY", _env("WEBCALL_AI_PROVIDER_RUNTIME_TENANT_ID", _DEFAULT_TENANT)),
        channel_key=_env("WEBCALL_AI_PROVIDER_RUNTIME_CHANNEL_KEY", _DEFAULT_CHANNEL),
        session_id=_env("WEBCALL_AI_PROVIDER_RUNTIME_SESSION_ID", "webcall-ai-production"),
        scenario=_env("WEBCALL_AI_PROVIDER_RUNTIME_SCENARIO", _DEFAULT_SCENARIO),
        body=body,
        recent_context=[{"role": "user", "content": body, "language": lang}],
        tracking_fact_summary=None,
        tracking_fact_evidence_present=False,
        output_contract=_env("WEBCALL_AI_PROVIDER_RUNTIME_OUTPUT_CONTRACT", _DEFAULT_CONTRACT),
        timeout_ms=_int_env("WEBCALL_AI_PROVIDER_RUNTIME_TIMEOUT_MS", 10000, minimum=500, maximum=30000),
        metadata=metadata,
    )


def _to_llm_result(result: ProviderResult) -> LLMResult:
    output = result.structured_output or {}
    reply = _first_text(output, "customer_reply", "reply", "response_text", "response", "message", "customer_visible_reply")
    if not reply:
        raise ProviderError(ProviderRuntimeLLMProvider.provider_name, "provider_runtime_missing_reply")
    intent = _first_text(output, "intent") or "other"
    handoff_required = output.get("handoff_required") is True
    handoff_reason = _first_text(output, "handoff_reason")
    if handoff_required and not handoff_reason:
        handoff_reason = _first_text(output, "recommended_agent_action") or "provider_runtime_handoff"
    return LLMResult(
        response_text=reply,
        intent=intent,
        handoff_required=handoff_required,
        handoff_reason=handoff_reason,
        provider_name=f"provider_runtime:{result.provider}",
    )


def _run_async(coro: Coroutine[Any, Any, ProviderResult]) -> ProviderResult:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, ProviderResult] = {}
    error: dict[str, BaseException] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:
            error["value"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error["value"]
    return result["value"]


def _first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.strip().split())
    return None


def _env(name: str, default: str) -> str:
    value = (os.getenv(name) or default).strip()
    return value or default


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))
