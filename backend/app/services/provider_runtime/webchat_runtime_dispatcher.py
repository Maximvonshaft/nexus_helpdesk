from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text

from app.db import SessionLocal

from ..ai_runtime.schemas import RuntimeAIProviderRequest, RuntimeAIProviderResult
from ..customer_language import detect_customer_language
from .output_contracts import WEBCHAT_RUNTIME_OUTPUT_CONTRACT
from .router import ProviderRuntimeRouter
from .schemas import ProviderRequest

logger = logging.getLogger(__name__)
WEBCHAT_RUNTIME_SCENARIO = "agent_turn"


def build_webchat_runtime_provider_request(
    request: RuntimeAIProviderRequest,
    *,
    metadata: dict[str, Any] | None = None,
) -> ProviderRequest:
    safe_metadata = dict(metadata or {})
    if request.metadata:
        safe_metadata.update(request.metadata)
    language = detect_customer_language(request.body, explicit=request.language)
    safe_metadata["language"] = language.language
    safe_metadata["customer_language"] = language.language
    safe_metadata["customer_language_source"] = language.source
    return ProviderRequest(
        request_id=request.request_id or "req_unknown",
        tenant_id=request.tenant_key,
        tenant_key=request.tenant_key,
        channel_key=request.channel_key,
        session_id=request.session_id,
        scenario=WEBCHAT_RUNTIME_SCENARIO,
        body=request.body,
        recent_context=request.recent_context,
        output_contract=WEBCHAT_RUNTIME_OUTPUT_CONTRACT,
        timeout_ms=15000,
        metadata=safe_metadata,
    )


async def dispatch_webchat_runtime_reply(
    *,
    request: RuntimeAIProviderRequest,
) -> RuntimeAIProviderResult:
    db = SessionLocal()
    try:
        provider_request = build_webchat_runtime_provider_request(request)
        result = await ProviderRuntimeRouter(db).route(provider_request)
        if not result.ok or not result.structured_output:
            return RuntimeAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code=result.error_code or "all_providers_failed",
                elapsed_ms=result.elapsed_ms,
                safe_summary={
                    "provider_runtime": True,
                    "provider_bypassed": False,
                    "provider": result.raw_payload_safe_summary,
                },
            )
        if not _authoritative_provider_audit_exists(
            db,
            request=provider_request,
            provider=result.provider,
        ):
            return RuntimeAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code="provider_runtime_audit_unavailable",
                elapsed_ms=result.elapsed_ms,
                safe_summary={
                    "provider_runtime": True,
                    "authoritative_audit": "unavailable",
                },
            )
        output = result.structured_output
        return RuntimeAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source=result.provider,
            raw_provider=result.raw_provider or result.provider,
            raw_payload_safe_summary={
                "provider_runtime": True,
                "provider_bypassed": False,
                "provider": result.raw_payload_safe_summary,
                "ai_decision": {
                    "intent": output.get("intent"),
                    "next_action": output.get("next_action"),
                    "handoff_required": output.get("handoff_required", False),
                    "tool_call_count": len(output.get("tool_calls") or []),
                },
            },
            reply=output.get("customer_reply"),
            intent=output.get("intent"),
            handoff_required=output.get("handoff_required", False),
            handoff_reason=output.get("handoff_reason"),
            recommended_agent_action=None,
            tool_calls=list(output.get("tool_calls") or []),
            elapsed_ms=result.elapsed_ms,
        )
    except Exception:
        logger.exception("ProviderRuntimeRouter failed")
        return RuntimeAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="router_exception",
            elapsed_ms=0,
        )
    finally:
        db.close()


def _authoritative_provider_audit_exists(
    db: Any,
    *,
    request: ProviderRequest,
    provider: str | None,
) -> bool:
    try:
        row = db.execute(
            text(
                """
                SELECT 1
                FROM provider_runtime_audit_logs
                WHERE request_id = :request_id
                  AND tenant_id = :tenant_id
                  AND channel_key = :channel_key
                  AND session_id = :session_id
                  AND provider = :provider
                  AND operation = 'generate'
                  AND status = 'ok'
                  AND error_code IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """
            ),
            {
                "request_id": request.request_id,
                "tenant_id": request.tenant_id,
                "channel_key": request.channel_key,
                "session_id": request.session_id,
                "provider": provider,
            },
        ).first()
    except Exception:
        try:
            db.rollback()
        except Exception:
            pass
        logger.error("provider_runtime_authoritative_audit_check_failed")
        return False
    return row is not None
