from __future__ import annotations

import asyncio

from app.services.ai_runtime.openclaw_responses_provider import build_fast_reply_input_text
from app.services.ai_runtime.schemas import FastAIProviderRequest, FastAIProviderResult
from app.services.webchat_fast_ai_service import generate_webchat_fast_reply


def test_input_text_adds_fact_block_only_when_enabled():
    text = build_fast_reply_input_text(
        body="Need status for AB1234567890",
        recent_context=[],
        max_prompt_chars=2000,
        tracking_fact_summary="Trusted tracking fact:\n- Current status: Status A",
        tracking_fact_evidence_present=True,
    )
    assert "Trusted tracking fact block" in text
    assert "Status A" in text

    text_without_flag = build_fast_reply_input_text(
        body="Need status for AB1234567890",
        recent_context=[],
        max_prompt_chars=2000,
        tracking_fact_summary="Trusted tracking fact:\n- Current status: Status A",
        tracking_fact_evidence_present=False,
    )
    assert "Trusted tracking fact block" not in text_without_flag
    assert "Status A" not in text_without_flag


def test_fast_service_forwards_fact_fields(monkeypatch):
    captured = {}

    class Settings:
        enabled = True

    async def fake_provider(*, request, settings):
        captured["request"] = request
        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="unit",
            raw_provider="unit",
            raw_payload_safe_summary={},
            reply="ok",
            intent="tracking",
            tracking_number="AB1234567890",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_intents=[],
            elapsed_ms=1,
        )

    import app.services.webchat_fast_ai_service as service

    monkeypatch.setattr(service, "get_webchat_fast_settings", lambda: Settings())
    monkeypatch.setattr(service, "generate_fast_reply", fake_provider)

    result = asyncio.run(
        generate_webchat_fast_reply(
            tenant_key="default",
            channel_key="website",
            session_id="s1",
            body="Need status for AB1234567890",
            recent_context=[],
            request_id="r1",
            tracking_fact_summary="Trusted tracking fact:\n- Current status: Status A",
            tracking_fact_metadata={"fact_evidence_present": True},
            tracking_fact_evidence_present=True,
        )
    )

    request = captured["request"]
    assert isinstance(request, FastAIProviderRequest)
    assert request.tracking_fact_evidence_present is True
    assert request.tracking_fact_summary is not None
    assert request.tracking_fact_metadata == {"fact_evidence_present": True}
    assert result.ok is True


def test_fast_service_drops_fact_fields_without_evidence(monkeypatch):
    captured = {}

    class Settings:
        enabled = True

    async def fake_provider(*, request, settings):
        captured["request"] = request
        return FastAIProviderResult(
            ok=True,
            ai_generated=True,
            reply_source="unit",
            raw_provider="unit",
            raw_payload_safe_summary={},
            reply="ok",
            intent="tracking",
            tracking_number=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            tool_intents=[],
            elapsed_ms=1,
        )

    import app.services.webchat_fast_ai_service as service

    monkeypatch.setattr(service, "get_webchat_fast_settings", lambda: Settings())
    monkeypatch.setattr(service, "generate_fast_reply", fake_provider)

    result = asyncio.run(
        generate_webchat_fast_reply(
            tenant_key="default",
            channel_key="website",
            session_id="s1",
            body="Need status for AB1234567890",
            recent_context=[],
            request_id="r1",
            tracking_fact_summary="Trusted tracking fact:\n- Current status: Status A",
            tracking_fact_metadata={"fact_evidence_present": False},
            tracking_fact_evidence_present=False,
        )
    )

    request = captured["request"]
    assert request.tracking_fact_evidence_present is False
    assert request.tracking_fact_summary is None
    assert request.tracking_fact_metadata is None
    assert result.ok is True
