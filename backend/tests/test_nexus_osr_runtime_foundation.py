from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus_osr_runtime_foundation_tests.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.nexus_osr.case_context import (  # noqa: E402
    CaseContext,
    CaseContextStatus,
    extract_tracking_reference,
)
from app.services.nexus_osr.controlled_action_executor import (  # noqa: E402
    ActionExecutionRequest,
    ActionExecutionResult,
    ControlledActionExecutor,
)
from app.services.nexus_osr.policies import (  # noqa: E402
    HumanAvailabilityStatus,
    HumanHoursPolicy,
    ToolExecutionPolicy,
)
from app.services.nexus_osr.runtime_decision_contract import (  # noqa: E402
    RuntimeToolAction,
)


def test_case_context_redacts_transient_customer_inputs_and_tracks_short_lived_state():
    ctx = CaseContext(conversation_id="wc-1", channel="webchat")
    ctx = ctx.with_inbound_message(
        "My email is user@example.com and my number is +382 67123456. "
        "Track CH1234567890 please."
    )

    assert ctx.safe_tracking_reference == "parcel ending 567890"
    assert ctx.tracking_number_hash is not None
    assert "user@example.com" not in (ctx.customer_claim_summary or "")
    assert "+382" not in (ctx.customer_claim_summary or "")
    assert "tracking_reference_captured" in ctx.ai_actions_taken

    ctx = ctx.with_contact_method(
        channel="email",
        value="user@example.com",
        source="webchat_form",
    )
    assert ctx.contact_methods[0].value_redacted == "[redacted_email]"

    ctx = ctx.mark_ticket_created("T-1").mark_routed("me-delivery-group").close()
    assert ctx.ticket_created is True
    assert ctx.status == CaseContextStatus.CLOSED
    assert ctx.closed_at is not None


def test_case_context_does_not_accept_phone_number_as_tracking_reference():
    phone_only = CaseContext(
        conversation_id="wc-phone",
        channel="webchat",
    ).with_inbound_message(
        "My phone is +382 67123456, please contact me. "
        "I do not have the parcel number yet."
    )

    assert phone_only.safe_tracking_reference is None
    assert phone_only.tracking_number_hash is None
    assert "tracking_reference_captured" not in phone_only.ai_actions_taken

    safe_ref, tracking_hash = extract_tracking_reference(
        "Call +382 67123456 first, then check CH1234567890 please"
    )
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
    assert holiday.reason == "holiday"


def test_controlled_action_executor_uses_only_supplied_production_handler():
    policy = ToolExecutionPolicy(
        tool_name="ticket.create",
        enabled=True,
        ai_auto_executable=True,
        requires_tracking_number=True,
        requires_contact=True,
        allowed_channels={"webchat"},
        allowed_countries={"ME"},
    )

    def handler(request: ActionExecutionRequest) -> ActionExecutionResult:
        assert request.case_context is not None
        next_context = request.case_context.mark_ticket_created("NEX-1")
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary={"ticket_id": "NEX-1"},
            case_context=next_context,
        )

    executor = ControlledActionExecutor(
        policies={"ticket.create": policy},
        handlers={"ticket.create": handler},
    )
    ctx = CaseContext(conversation_id="wc-1", channel="webchat", country_code="ME")

    blocked = executor.execute(
        ActionExecutionRequest(
            action=RuntimeToolAction(tool_name="ticket.create"),
            channel="webchat",
            country_code="ME",
            case_context=ctx,
        )
    )
    assert not blocked.ok
    assert blocked.error_code == "missing_required_context"
    assert set(blocked.summary["missing_requirements"]) == {
        "tracking_number",
        "contact_method",
    }

    ctx = ctx.with_inbound_message("CH1234567890").with_contact_method(
        channel="whatsapp",
        value="+382 67123456",
        source="webchat_form",
    )
    executed = executor.execute(
        ActionExecutionRequest(
            action=RuntimeToolAction(tool_name="ticket.create"),
            channel="webchat",
            country_code="ME",
            case_context=ctx,
            idempotency_key="idem-1",
        )
    )

    assert executed.ok
    assert executed.status == "executed"
    assert executed.case_context is not None
    assert executed.case_context.ticket_created is True
