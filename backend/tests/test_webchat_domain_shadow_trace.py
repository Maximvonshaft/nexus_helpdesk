from __future__ import annotations

from app.services.domain_intelligence.webchat_shadow_bridge import build_webchat_domain_shadow_trace
from app.services.webchat_runtime_ai_service import _attach_domain_shadow_trace


def test_webchat_domain_shadow_trace_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", raising=False)

    trace = build_webchat_domain_shadow_trace(
        body="Where is my parcel now?",
        tenant_key="default",
        channel_key="website",
    )

    assert trace is None


def test_webchat_domain_shadow_trace_enabled_is_non_enforcing(monkeypatch) -> None:
    monkeypatch.setenv("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", "true")

    trace = build_webchat_domain_shadow_trace(
        body="I want to complain about the courier.",
        tenant_key="default",
        channel_key="website",
    )

    assert trace is not None
    assert trace["trace_version"] == "domain_webchat_shadow_trace_v1"
    assert trace["shadow_mode"] is True
    assert trace["enforced"] is False
    assert trace["understanding"]["primary_intent"] == "logistics.complaint_escalation"
    assert trace["answer_plan"]["plan_type"] == "work_order_create"
    assert trace["side_effects"] == {
        "tool_executed": False,
        "ticket_created": False,
        "handoff_triggered": False,
        "reply_changed": False,
        "retrieval_changed": False,
    }


def test_webchat_runtime_context_only_attaches_shadow_trace_when_flag_enabled(monkeypatch) -> None:
    runtime_context = {"context_version": "nexus.webchat_runtime_context"}
    monkeypatch.delenv("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", raising=False)

    unchanged = _attach_domain_shadow_trace(
        runtime_context,
        body="Where is my parcel now?",
        tenant_key="default",
        channel_key="website",
        market_id=None,
        language=None,
    )
    assert unchanged is runtime_context
    assert "domain_intelligence_trace" not in unchanged

    monkeypatch.setenv("DOMAIN_INTELLIGENCE_WEBCHAT_SHADOW_TRACE_ENABLED", "true")
    enriched = _attach_domain_shadow_trace(
        runtime_context,
        body="Where is my parcel now?",
        tenant_key="default",
        channel_key="website",
        market_id=None,
        language=None,
    )
    assert enriched is not runtime_context
    assert "domain_intelligence_trace" in enriched
    assert enriched["domain_intelligence_trace"]["side_effects"]["reply_changed"] is False
    assert "domain_intelligence_trace" not in runtime_context
