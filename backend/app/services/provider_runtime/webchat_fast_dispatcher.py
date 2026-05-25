from __future__ import annotations

import logging

from app.db import SessionLocal

from ..ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from ..ai_runtime_context import build_webchat_runtime_context
from .router import ProviderRuntimeRouter
from .schemas import ProviderRequest

logger = logging.getLogger(__name__)


def _fallback_runtime_context(request: FastAIProviderRequest) -> dict:
    return {
        "context_version": "nexus_webchat_runtime_context_v1",
        "tenant_key": request.tenant_key,
        "metadata_filters": {
            "market_id": request.market_id,
            "channel": request.channel_key,
            "language": request.language,
            "audience_scope": "customer",
        },
        "persona_context": None,
        "knowledge_context": {"retrieval": "unavailable", "total_matches": 0, "hits": []},
        "safety_policy": {
            "knowledge_scope": "policy_sop_faq_only",
            "tracking_truth_boundary": "Parcel live status requires tracking_fact_evidence_present=true and trusted tracking_fact_summary.",
        },
    }


def build_webchat_fast_provider_request(request: FastAIProviderRequest, *, metadata: dict | None = None) -> ProviderRequest:
    safe_metadata = dict(metadata or {})
    if request.metadata:
        safe_metadata.update(request.metadata)
    if request.tracking_fact_evidence_present and request.tracking_fact_metadata:
        safe_metadata["tracking_fact_metadata"] = request.tracking_fact_metadata
    return ProviderRequest(
        request_id=request.request_id or "req_unknown",
        tenant_id=request.tenant_key,
        tenant_key=request.tenant_key,
        channel_key=request.channel_key,
        session_id=request.session_id,
        scenario="webchat_fast_reply",
        body=request.body,
        recent_context=request.recent_context,
        tracking_fact_summary=request.tracking_fact_summary,
        tracking_fact_evidence_present=request.tracking_fact_evidence_present,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
        metadata=safe_metadata,
    )


async def dispatch_webchat_fast_reply(*, request: FastAIProviderRequest) -> FastAIProviderResult:
    db = SessionLocal()
    try:
        try:
            runtime_context = build_webchat_runtime_context(
                db,
                tenant_key=request.tenant_key,
                channel_key=request.channel_key,
                body=request.body,
                market_id=request.market_id,
                language=request.language,
            )
        except Exception:
            logger.exception("webchat_runtime_context_build_failed")
            runtime_context = _fallback_runtime_context(request)
        router = ProviderRuntimeRouter(db)
        res = await router.route(build_webchat_fast_provider_request(request, metadata=runtime_context))
        if not res.ok or not res.structured_output:
            return FastAIProviderResult.unavailable(
                provider="provider_runtime",
                error_code=res.error_code or "all_failed",
                elapsed_ms=res.elapsed_ms,
            )

        output = res.structured_output
        safe_summary = dict(res.raw_payload_safe_summary or {})
        safe_summary["provider_runtime"] = True
        reply = output.get("customer_reply") or output.get("reply")

        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source=res.provider,
            raw_provider=res.provider,
            raw_payload_safe_summary=safe_summary,
            reply=reply,
            intent=output.get("intent"),
            tracking_number=output.get("tracking_number"),
            handoff_required=output.get("handoff_required", False),
            handoff_reason=output.get("handoff_reason"),
            recommended_agent_action=output.get("recommended_agent_action"),
            tool_intents=[],
            elapsed_ms=res.elapsed_ms,
        )
    except Exception:
        logger.exception("ProviderRuntimeRouter failed")
        return FastAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="router_exception",
            elapsed_ms=0,
        )
    finally:
        db.close()
