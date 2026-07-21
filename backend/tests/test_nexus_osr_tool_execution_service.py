from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_tool_execution_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr, tool_models, operator_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.enums import SourceChannel  # noqa: E402
from app.models import Customer, Ticket, TicketEvent  # noqa: E402
from app.models_osr import RuntimeDecisionAuditRecord, ToolExecutionPolicyRecord  # noqa: E402
from app.services.nexus_osr.auto_ticket_service import create_or_reuse_ticket_from_case_context  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.tool_execution_service import (  # noqa: E402
    GovernedToolExecutionOptions,
    execute_controlled_tool_calls,
    runtime_tool_actions_from_tool_calls,
)
from app.tool_models import ToolCallLog  # noqa: E402
from app.webchat_models import WebchatConversation, WebchatHandoffRequest  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_tool_execution.db"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False}, future=True)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False)
    Base.metadata.create_all(engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def add_policy(
    db_session,
    tool_name: str,
    *,
    enabled: bool = True,
    ai_auto_executable: bool = True,
    risk_level: str = "medium",
    requires_tracking_number: bool = False,
    requires_contact: bool = False,
    requires_customer_confirmation: bool = False,
    requires_human_confirmation: bool = False,
    country_code: str = "ME",
    channel: str = "webchat",
    allowed_channels: list[str] | None = None,
    allowed_countries: list[str] | None = None,
):
    row = ToolExecutionPolicyRecord(
        tool_name=tool_name,
        country_code=country_code,
        channel=channel,
        enabled=enabled,
        ai_auto_executable=ai_auto_executable,
        risk_level=risk_level,
        requires_tracking_number=requires_tracking_number,
        requires_contact=requires_contact,
        requires_customer_confirmation=requires_customer_confirmation,
        requires_human_confirmation=requires_human_confirmation,
        allowed_channels_json=allowed_channels,
        allowed_countries_json=allowed_countries,
    )
    db_session.add(row)
    db_session.flush()
    return row


def make_conversation(db_session, *, public_id: str = "tool_exec_wc") -> WebchatConversation:
    row = WebchatConversation(
        public_id=public_id,
        visitor_token_hash=f"{public_id}-token-hash",
        tenant_key="pytest",
        channel_key="webchat",
        visitor_name="Tool Exec Visitor",
        status="open",
    )
    db_session.add(row)
    db_session.flush()
    return row


def make_customer(db_session) -> Customer:
    row = Customer(name="Tool Exec Visitor", external_ref="tool-exec-visitor")
    db_session.add(row)
    db_session.flush()
    return row


def case_context_with_tracking_and_contact(conversation: WebchatConversation | None = None) -> CaseContext:
    ctx = CaseContext(
        conversation_id=conversation.id if conversation is not None else None,
        channel="webchat",
        country_code="ME",
        issue_type="tracking",
    ).with_inbound_message("Please help with CH1234567890")
    return ctx.with_contact_method(channel="whatsapp", value="+382 67123456", source="webchat_form")


def test_runtime_tool_calls_preserve_bounded_execution_arguments():
    actions = runtime_tool_actions_from_tool_calls([
        {
            "tool_name": "speedaf.workOrder.create",
            "arguments": {
                "tracking_number": "CH1234567890",
                "phone": "+382 67123456",
                "address": "123 Unsafe Street",
                "raw_payload": {"token": "secret-value"},
            },
            "requires_confirmation": True,
        }
    ])

    assert len(actions) == 1
    action = actions[0]
    assert action.tool_name == "speedaf.workOrder.create"
    assert action.requires_confirmation is True
    assert action.arguments["tracking_number"] == "CH1234567890"
    assert action.arguments["phone"] == "+382 67123456"
    assert action.arguments["address"] == "123 Unsafe Street"
    assert "raw_payload" not in action.arguments
    assert "secret-value" not in str(action.arguments)


def test_disabled_policy_blocks_and_writes_safe_audit(db_session):
    add_policy(db_session, "ticket.create", enabled=False, requires_tracking_number=True, requires_contact=True)
    conversation = make_conversation(db_session, public_id="disabled_policy_wc")
    ctx = case_context_with_tracking_and_contact(conversation)

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "ticket.create", "idempotency_key": "disabled-ticket", "requires_confirmation": True}],
        case_context=ctx,
        conversation=conversation,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "tool_disabled"
    assert db_session.query(Ticket).count() == 0
    log = db_session.query(ToolCallLog).one()
    assert log.status == "blocked"
    assert log.redaction_applied is True
    assert "CH1234567890" not in (log.input_summary or "")
    assert "+382" not in (log.input_summary or "")
    audit = db_session.query(RuntimeDecisionAuditRecord).one()
    assert audit.allowed is False


def test_missing_tracking_and_contact_blocks_before_execution(db_session):
    add_policy(db_session, "ticket.create", requires_tracking_number=True, requires_contact=True)
    ctx = CaseContext(channel="webchat", country_code="ME")

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "ticket.create", "idempotency_key": "missing-context", "requires_confirmation": True}],
        case_context=ctx,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is False
    assert result.error_code == "missing_required_context"
    assert set(result.summary["missing_requirements"]) == {"tracking_number", "contact_method"}
    assert db_session.query(Ticket).count() == 0


def test_confirmation_required_returns_without_execution(db_session):
    add_policy(db_session, "timeline.event.create", requires_human_confirmation=True, risk_level="low")
    conversation = make_conversation(db_session, public_id="confirm_wc")
    customer = make_customer(db_session)
    ticket_result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=case_context_with_tracking_and_contact(conversation),
        customer=customer,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    before_events = db_session.query(TicketEvent).count()

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "timeline.event.create", "idempotency_key": "needs-confirm", "requires_confirmation": True}],
        case_context=ticket_result.case_context,
        conversation=conversation,
        ticket=ticket_result.ticket,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is False
    assert result.status == "confirmation_required"
    assert result.error_code == "human_confirmation_required"
    assert db_session.query(TicketEvent).count() == before_events


def test_ticket_create_requires_customer_confirmation_then_executes_idempotently(db_session):
    add_policy(
        db_session,
        "ticket.create",
        requires_tracking_number=True,
        requires_contact=True,
        requires_customer_confirmation=True,
    )
    conversation = make_conversation(db_session, public_id="ticket_create_wc")
    customer = make_customer(db_session)
    ctx = case_context_with_tracking_and_contact(conversation)
    tool_call = {
        "tool_name": "ticket.create",
        "idempotency_key": "ticket-idem",
        "requires_confirmation": True,
        "arguments": {"issue_type": "tracking"},
    }

    proposed = execute_controlled_tool_calls(
        db_session,
        tool_calls=[tool_call],
        case_context=ctx,
        conversation=conversation,
        customer=customer,
        channel="webchat",
        country_code="ME",
    )[0]
    assert proposed.ok is False
    assert proposed.status == "confirmation_required"
    assert proposed.error_code == "customer_confirmation_required"
    assert db_session.query(Ticket).count() == 0

    confirmed_options = GovernedToolExecutionOptions(
        customer_confirmation_granted=True,
    )
    first = execute_controlled_tool_calls(
        db_session,
        tool_calls=[tool_call],
        case_context=ctx,
        conversation=conversation,
        customer=customer,
        channel="webchat",
        country_code="ME",
        options=confirmed_options,
    )[0]
    second = execute_controlled_tool_calls(
        db_session,
        tool_calls=[tool_call],
        case_context=first.case_context or ctx,
        conversation=conversation,
        customer=customer,
        channel="webchat",
        country_code="ME",
        options=confirmed_options,
    )[0]

    assert first.ok is True
    assert first.status == "executed"
    assert first.summary["created"] is True
    assert first.summary["ticket_no"].startswith("OSR-ME-")
    assert first.case_context is not None
    assert first.case_context.ticket_created is True
    assert db_session.query(Ticket).count() == 1
    assert second.ok is True
    assert second.status == "duplicate"
    assert db_session.query(Ticket).count() == 1

def test_handoff_request_create_calls_handoff_service_and_suspends_ai(db_session):
    add_policy(db_session, "handoff.request.create", risk_level="medium")
    conversation = make_conversation(db_session, public_id="handoff_wc")
    customer = make_customer(db_session)
    ticket_result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=case_context_with_tracking_and_contact(conversation),
        customer=customer,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "handoff.request.create", "idempotency_key": "handoff-idem", "arguments": {"reason": "customer_requested_human"}}],
        case_context=ticket_result.case_context,
        conversation=conversation,
        ticket=ticket_result.ticket,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is True
    assert result.status == "executed"
    assert result.summary["status"] == "requested"
    assert result.case_context is not None
    assert result.case_context.handoff_requested is True
    assert db_session.query(WebchatHandoffRequest).count() == 1
    assert conversation.ai_suspended is True
    assert conversation.handoff_status == "requested"


def test_timeline_event_create_writes_internal_safe_event(db_session):
    add_policy(db_session, "timeline.event.create", risk_level="low")
    conversation = make_conversation(db_session, public_id="timeline_wc")
    customer = make_customer(db_session)
    ticket_result = create_or_reuse_ticket_from_case_context(
        db_session,
        case_context=case_context_with_tracking_and_contact(conversation),
        customer=customer,
        conversation=conversation,
        source_channel=SourceChannel.web_chat,
    )
    before_events = db_session.query(TicketEvent).count()

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[
            {
                "tool_name": "timeline.event.create",
                "idempotency_key": "timeline-idem",
                "arguments": {"summary": "Customer user@example.com called from +382 67123456 about CH1234567890", "raw_payload": {"token": "secret"}},
            }
        ],
        case_context=ticket_result.case_context,
        conversation=conversation,
        ticket=ticket_result.ticket,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is True
    assert result.status == "executed"
    assert result.customer_visible_summary is None
    assert db_session.query(TicketEvent).count() >= before_events + 1
    log = db_session.query(ToolCallLog).filter(ToolCallLog.tool_name == "timeline.event.create").one()
    combined = f"{log.input_summary} {log.output_summary}"
    assert "user@example.com" not in combined
    assert "+382" not in combined
    assert "CH1234567890" not in combined
    assert "secret" not in combined.lower()


def test_speedaf_work_order_default_blocked_even_when_policy_allows(db_session):
    add_policy(db_session, "speedaf.workOrder.create", risk_level="high", requires_tracking_number=True, requires_contact=True)
    ctx = case_context_with_tracking_and_contact()

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "speedaf.workOrder.create", "idempotency_key": "speedaf-high-risk"}],
        case_context=ctx,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "high_risk_write_tool_blocked"


def test_speedaf_work_order_requires_explicit_test_allow_but_still_needs_handler(db_session):
    add_policy(db_session, "speedaf.workOrder.create", risk_level="high", requires_tracking_number=True, requires_contact=True)
    ctx = case_context_with_tracking_and_contact()

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "speedaf.workOrder.create", "idempotency_key": "speedaf-allowed-test"}],
        case_context=ctx,
        channel="webchat",
        country_code="ME",
        options=GovernedToolExecutionOptions(
            allow_high_risk_write_execution=True,
            allowed_high_risk_write_tools=frozenset({"speedaf.workOrder.create"}),
        ),
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "tool_handler_missing"


def test_policy_gate_blocks_unknown_tool_before_execution(db_session):
    ctx = case_context_with_tracking_and_contact()

    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "unknown.tool", "idempotency_key": "unknown-tool"}],
        case_context=ctx,
        channel="webchat",
        country_code="ME",
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code == "unknown_tool_blocked"
    assert db_session.query(ToolCallLog).count() == 1
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 1


def test_explicit_empty_tool_allowlist_fails_closed(db_session):
    conversation = make_conversation(db_session, public_id="empty_allowlist_wc")
    result = execute_controlled_tool_calls(
        db_session,
        tool_calls=[{"tool_name": "timeline.event.create"}],
        case_context=CaseContext(
            conversation_id=conversation.id,
            channel="webchat",
            country_code="ME",
        ),
        conversation=conversation,
        channel="webchat",
        country_code="ME",
        options=GovernedToolExecutionOptions(
            allowed_tool_names=frozenset(),
            granted_permissions=frozenset(),
        ),
    )[0]

    assert result.ok is False
    assert result.status == "blocked"
    assert result.error_code in {"tool_not_available", "tool_permission_denied"}
    assert db_session.query(TicketEvent).count() == 0


def test_server_idempotency_is_scoped_to_writes_and_ignores_model_keys():
    from app.services.nexus_osr import tool_execution_service_core as core

    context = CaseContext(
        conversation_id="conversation-1",
        ticket_id="ticket-1",
        channel="website",
        country_code="CH",
    )
    read_action = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "speedaf.order.query",
                "arguments": {"tracking_number": "CH111111123456"},
                "idempotency_key": "model-controlled-read-key",
            }
        ]
    )[0]
    assert core._idempotency_key_for_action(
        read_action,
        case_context=context,
        tenant_id="tenant-1",
        channel="website",
        country_code="CH",
    ) is None

    first = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "first request"},
                "idempotency_key": "same-model-key",
            }
        ]
    )[0]
    same = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "first request"},
                "idempotency_key": "different-model-key",
            }
        ]
    )[0]
    second = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "second request"},
                "idempotency_key": "same-model-key",
            }
        ]
    )[0]

    def key(action):
        return core._idempotency_key_for_action(
            action,
            case_context=context,
            tenant_id="tenant-1",
            channel="website",
            country_code="CH",
        )

    assert key(first) == key(same)
    assert key(first) != key(second)
    assert "same-model-key" not in str(key(first))
    assert "first request" not in str(key(first))


def test_execution_argument_bounding_stops_nested_payloads():
    nested = {"level": {"level": {"level": {"level": {"level": {"level": "too deep"}}}}}}
    action = runtime_tool_actions_from_tool_calls(
        [{"tool_name": "timeline.event.create", "arguments": nested}]
    )[0]

    assert "too deep" not in str(action.arguments)
    assert "[truncated]" in str(action.arguments)
