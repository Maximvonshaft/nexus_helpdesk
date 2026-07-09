from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_runtime_foundation_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.nexus_osr.case_context import CaseContext, CaseContextStatus, extract_tracking_reference  # noqa: E402
from app.services.nexus_osr.controlled_action_executor import (  # noqa: E402
    ActionExecutionRequest,
    ControlledActionExecutor,
    default_handlers,
)
from app.services.nexus_osr.policies import (  # noqa: E402
    EscalationAction,
    HumanAvailabilityStatus,
    HumanHoursPolicy,
    ToolExecutionPolicy,
    evaluate_escalation,
)
from app.services.nexus_osr.runtime_decision_contract import (  # noqa: E402
    BusinessReplyType,
    EvidenceSource,
    EvidenceType,
    RuntimeAction,
    RuntimeDecision,
    RuntimeToolAction,
    evaluate_runtime_decision,
)


def test_tracking_status_requires_verified_mcp_current_status():
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TRACKING_STATUS_ANSWER,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your parcel is in transit.",
        evidence_sources=[
            EvidenceSource(
                evidence_type=EvidenceType.MCP_HISTORY_ENRICHMENT,
                source_id="tool:track-history",
                label="tracking history",
                verified=True,
            )
        ],
    )

    result = evaluate_runtime_decision(decision)

    assert not result.allowed
    assert any(item.code == "tracking_status_without_mcp_current_status" for item in result.violations)


def test_tracking_status_allows_verified_current_status():
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TRACKING_STATUS_ANSWER,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your parcel is currently out for delivery.",
        evidence_sources=[
            EvidenceSource(
                evidence_type=EvidenceType.MCP_CURRENT_STATUS,
                source_id="mcp:order-query",
                label="MCP order query",
                verified=True,
                current_status=True,
            )
        ],
    )

    result = evaluate_runtime_decision(decision)

    assert result.allowed


def test_previous_ai_reply_and_customer_claim_cannot_support_factual_outcome():
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TICKET_CREATED_NOTICE,
        next_action=RuntimeAction.REPLY,
        customer_reply="Your ticket has been created.",
        evidence_sources=[
            EvidenceSource(EvidenceType.PREVIOUS_AI_REPLY, "ai:1", "previous AI"),
            EvidenceSource(EvidenceType.CUSTOMER_CLAIM, "msg:1", "customer said ticket exists"),
        ],
    )

    result = evaluate_runtime_decision(decision)

    assert not result.allowed
    assert {item.code for item in result.violations} >= {
        "previous_ai_reply_used_as_fact",
        "customer_claim_used_as_fact",
        "ticket_created_notice_without_ticket_create_action",
    }


def test_knowledge_answer_requires_customer_visible_knowledge():
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.KNOWLEDGE_ANSWER,
        next_action=RuntimeAction.REPLY,
        customer_reply="We support this service.",
        evidence_sources=[EvidenceSource(EvidenceType.KNOWLEDGE_INTERNAL, "kb:1", "internal SOP", customer_visible=False)],
    )

    result = evaluate_runtime_decision(decision)

    assert not result.allowed
    assert any(item.code == "knowledge_answer_without_customer_visible_knowledge" for item in result.violations)


def test_case_context_redacts_transient_customer_inputs_and_tracks_short_lived_state():
    ctx = CaseContext(conversation_id="wc-1", channel="webchat")
    ctx = ctx.with_inbound_message("My email is user@example.com and my number is +382 67123456. Track CH1234567890 please.")

    assert ctx.safe_tracking_reference is not None
    assert ctx.safe_tracking_reference == "parcel ending 567890"
    assert ctx.tracking_number_hash is not None
    assert "user@example.com" not in (ctx.customer_claim_summary or "")
    assert "+382" not in (ctx.customer_claim_summary or "")
    assert "tracking_reference_captured" in ctx.ai_actions_taken

    ctx = ctx.with_contact_method(channel="email", value="user@example.com", source="webchat_form")
    assert ctx.contact_methods[0].value_redacted == "[redacted_email]"

    ctx = ctx.mark_ticket_created("T-1").mark_routed("me-delivery-group").close()
    assert ctx.ticket_created is True
    assert ctx.status == CaseContextStatus.CLOSED
    assert ctx.closed_at is not None


def test_case_context_does_not_accept_phone_number_as_tracking_reference():
    phone_only = CaseContext(conversation_id="wc-phone", channel="webchat").with_inbound_message(
        "My phone is +382 67123456, please contact me. I do not have the parcel number yet."
    )

    assert phone_only.safe_tracking_reference is None
    assert phone_only.tracking_number_hash is None
    assert "tracking_reference_captured" not in phone_only.ai_actions_taken

    safe_ref, tracking_hash = extract_tracking_reference("Call +382 67123456 first, then check CH1234567890 please")
    assert safe_ref == "parcel ending 567890"
    assert tracking_hash is not None


def test_human_hours_policy_handles_online_offline_and_holidays():
    policy = HumanHoursPolicy(
        queue_key="me-support",
        timezone_name="UTC",
        weekly_hours={"mon": [("09:00", "18:00")]},
        holidays={"2026-07-09"},
    )

    online = policy.evaluate(datetime.fromisoformat("2026-07-06T10:00:00+00:00"))
    offline = policy.evaluate(datetime.fromisoformat("2026-07-06T20:00:00+00:00"))
    holiday = policy.evaluate(datetime.fromisoformat("2026-07-09T10:00:00+00:00"))

    assert online.status == HumanAvailabilityStatus.ONLINE
    assert offline.status == HumanAvailabilityStatus.OFFLINE
    assert offline.auto_ticket_when_offline is True
    assert holiday.reason == "holiday"


def test_escalation_policy_allows_can_do_before_configured_threshold():
    first = evaluate_escalation("I want compensation for this bad delivery", ai_attempt_count=0)
    later = evaluate_escalation("I want compensation for this bad delivery", ai_attempt_count=2)

    assert first.matched
    assert first.action == EscalationAction.TRY_AI_RESOLUTION
    assert later.action == EscalationAction.HANDOFF_OR_TICKET
    assert later.ticket_required


def test_controlled_action_executor_blocks_missing_context_then_executes_ticket_create():
    policy = ToolExecutionPolicy(
        tool_name="ticket.create",
        enabled=True,
        ai_auto_executable=True,
        requires_tracking_number=True,
        requires_contact=True,
        allowed_channels={"webchat"},
        allowed_countries={"ME"},
    )
    executor = ControlledActionExecutor(policies={"ticket.create": policy}, handlers=default_handlers())
    ctx = CaseContext(conversation_id="wc-1", channel="webchat", country_code="ME")

    blocked = executor.execute(ActionExecutionRequest(
        action=RuntimeToolAction(tool_name="ticket.create"),
        channel="webchat",
        country_code="ME",
        case_context=ctx,
    ))

    assert not blocked.ok
    assert blocked.error_code == "missing_required_context"
    assert set(blocked.summary["missing_requirements"]) == {"tracking_number", "contact_method"}

    ctx = ctx.with_inbound_message("CH1234567890").with_contact_method(channel="whatsapp", value="+382 67123456", source="webchat_form")
    executed = executor.execute(ActionExecutionRequest(
        action=RuntimeToolAction(tool_name="ticket.create", arguments={"ticket_id": "NEX-1"}),
        channel="webchat",
        country_code="ME",
        case_context=ctx,
        idempotency_key="idem-1",
    ))

    assert executed.ok
    assert executed.status == "executed"
    assert executed.case_context is not None
    assert executed.case_context.ticket_created is True


def test_blocked_decision_must_not_have_customer_reply():
    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.NO_ANSWER,
        next_action=RuntimeAction.BLOCK,
        customer_reply="I should not be visible.",
    )

    result = evaluate_runtime_decision(decision)

    assert not result.allowed
    assert any(item.code == "blocked_decision_has_customer_reply" for item in result.violations)
