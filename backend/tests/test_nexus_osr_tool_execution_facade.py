from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_osr_tool_execution_facade_tests.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app import models, webchat_models, models_osr, tool_models, operator_models  # noqa: F401,E402
from app.db import Base  # noqa: E402
from app.models_osr import RuntimeDecisionAuditRecord, ToolExecutionPolicyRecord  # noqa: E402
from app.services.nexus_osr.case_context import CaseContext  # noqa: E402
from app.services.nexus_osr.tool_execution_facade import (  # noqa: E402
    OSRToolExecutionFacade,
    OSRToolExecutionMode,
    osr_tool_execution_mode_from_env,
)
from app.services.nexus_osr.tool_execution_policy_seed import seed_default_tool_execution_policies  # noqa: E402
from app.services.nexus_osr.tool_execution_service import GovernedToolExecutionOptions  # noqa: E402
from app.tool_models import ToolCallLog  # noqa: E402


@pytest.fixture()
def db_session(tmp_path):
    db_file = tmp_path / "nexus_osr_tool_execution_facade.db"
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


def ctx_empty() -> CaseContext:
    return CaseContext(channel="webchat", country_code="ME", issue_type="tracking")


def ctx_with_tracking() -> CaseContext:
    return ctx_empty().with_inbound_message("Please check CH1234567890")


def ctx_with_tracking_and_contact() -> CaseContext:
    return ctx_with_tracking().with_contact_method(channel="whatsapp", value="+382 67123456", source="webchat_form")


def execute_one(
    db_session,
    tool_call,
    case_context: CaseContext | None = None,
    *,
    channel: str = "webchat",
    country_code: str = "ME",
    mode: OSRToolExecutionMode | str | None = OSRToolExecutionMode.POLICY_EXECUTE,
    options: GovernedToolExecutionOptions | None = None,
):
    return OSRToolExecutionFacade(db_session).execute(
        tool_calls=[tool_call],
        case_context=case_context or ctx_empty(),
        channel=channel,
        country_code=country_code,
        mode=mode,
        options=options,
    )


def test_default_tool_execution_policy_seed_sets_safe_defaults(db_session):
    rows = seed_default_tool_execution_policies(db_session)

    by_name = {row.tool_name: row for row in rows}
    assert by_name["ticket.create"].enabled is True
    assert by_name["ticket.create"].ai_auto_executable is False
    assert by_name["ticket.create"].requires_customer_confirmation is True
    assert by_name["handoff.request.create"].enabled is True
    assert by_name["handoff.request.create"].ai_auto_executable is True
    assert by_name["timeline.event.create"].enabled is True
    assert by_name["timeline.event.create"].ai_auto_executable is True
    assert by_name["speedaf.workOrder.create"].enabled is False
    assert by_name["speedaf.workOrder.create"].risk_level == "high"


def test_env_mode_defaults_to_observe_only(monkeypatch):
    monkeypatch.delenv("OSR_TOOL_EXECUTION_MODE", raising=False)
    assert osr_tool_execution_mode_from_env() == OSRToolExecutionMode.OBSERVE_ONLY
    monkeypatch.setenv("OSR_TOOL_EXECUTION_MODE", "policy_execute")
    assert osr_tool_execution_mode_from_env() == OSRToolExecutionMode.POLICY_EXECUTE
    monkeypatch.setenv("OSR_TOOL_EXECUTION_MODE", "blocked")
    assert osr_tool_execution_mode_from_env() == OSRToolExecutionMode.BLOCKED
    monkeypatch.setenv("OSR_TOOL_EXECUTION_MODE", "invalid")
    assert osr_tool_execution_mode_from_env() == OSRToolExecutionMode.OBSERVE_ONLY


def test_default_mode_observe_only_never_executes_or_writes_tool_call_log(db_session, monkeypatch):
    monkeypatch.delenv("OSR_TOOL_EXECUTION_MODE", raising=False)
    add_policy(db_session, "ticket.create")
    result = OSRToolExecutionFacade(db_session).execute(
        tool_calls=[{"tool_name": "ticket.create", "idempotency_key": "observe-only"}],
        case_context=ctx_with_tracking_and_contact(),
    )

    assert result.mode == OSRToolExecutionMode.OBSERVE_ONLY
    assert result.executed is False
    assert result.results[0].status == "observe_only"
    assert db_session.query(ToolCallLog).count() == 0
    assert db_session.query(RuntimeDecisionAuditRecord).count() == 1


def test_no_policy_must_block(db_session):
    result = execute_one(db_session, {"tool_name": "ticket.create", "idempotency_key": "no-policy", "requires_confirmation": True}, ctx_with_tracking_and_contact())

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].status == "blocked"
    assert result.results[0].error_code == "tool_policy_missing"
    assert result.safe_customer_visible_results == ()


def test_policy_channel_mismatch_blocks(db_session):
    add_policy(db_session, "ticket.create", allowed_channels=["whatsapp"])

    result = execute_one(db_session, {"tool_name": "ticket.create", "idempotency_key": "channel-mismatch", "requires_confirmation": True}, ctx_with_tracking_and_contact())

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "channel_not_allowed"


def test_policy_country_mismatch_blocks(db_session):
    add_policy(db_session, "ticket.create", allowed_countries=["US"])

    result = execute_one(db_session, {"tool_name": "ticket.create", "idempotency_key": "country-mismatch", "requires_confirmation": True}, ctx_with_tracking_and_contact())

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "country_not_allowed"


def test_requires_tracking_number_missing_blocks(db_session):
    add_policy(db_session, "ticket.create", requires_tracking_number=True)

    result = execute_one(db_session, {"tool_name": "ticket.create", "idempotency_key": "missing-tracking", "requires_confirmation": True}, ctx_empty())

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "missing_required_context"
    assert result.results[0].summary["missing_requirements"] == ["tracking_number"]


def test_requires_contact_missing_blocks(db_session):
    add_policy(db_session, "ticket.create", requires_contact=True)

    result = execute_one(db_session, {"tool_name": "ticket.create", "idempotency_key": "missing-contact", "requires_confirmation": True}, ctx_with_tracking())

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "missing_required_context"
    assert result.results[0].summary["missing_requirements"] == ["contact_method"]


def test_confirmation_required_mode_returns_without_execution(db_session):
    add_policy(db_session, "timeline.event.create", risk_level="low", requires_human_confirmation=True)

    result = execute_one(
        db_session,
        {"tool_name": "timeline.event.create", "idempotency_key": "confirmation-required", "requires_confirmation": True},
        ctx_with_tracking_and_contact(),
    )

    assert result.mode == OSRToolExecutionMode.CONFIRMATION_REQUIRED
    assert result.executed is False
    assert result.results[0].status == "confirmation_required"
    assert result.results[0].error_code == "human_confirmation_required"


def test_policy_execute_returns_safe_result_only_after_customer_confirmation(db_session):
    add_policy(
        db_session,
        "ticket.create",
        requires_tracking_number=True,
        requires_contact=True,
        requires_customer_confirmation=True,
    )
    tool_call = {
        "tool_name": "ticket.create",
        "idempotency_key": "safe-result",
        "requires_confirmation": True,
    }

    proposed = execute_one(
        db_session,
        tool_call,
        ctx_with_tracking_and_contact(),
    )
    assert proposed.mode == OSRToolExecutionMode.CONFIRMATION_REQUIRED
    assert proposed.executed is False
    assert proposed.results[0].error_code == "customer_confirmation_required"

    result = execute_one(
        db_session,
        tool_call,
        ctx_with_tracking_and_contact(),
        options=GovernedToolExecutionOptions(
            customer_confirmation_granted=True,
        ),
    )
    assert result.mode == OSRToolExecutionMode.POLICY_EXECUTE
    assert result.executed is True
    assert result.safe_customer_visible_results
    assert result.safe_customer_visible_results[0]["send_directly"] is False
    assert result.safe_customer_visible_results[0]["summary_template"]


@pytest.mark.parametrize(
    "tool_name",
    [
        "speedaf.workOrder.create",
        "speedaf.order.cancel.request",
        "speedaf.order.updateAddress.request",
    ],
)
def test_policy_execute_blocks_high_risk_speedaf_write_tools(db_session, tool_name):
    add_policy(db_session, tool_name, enabled=True, ai_auto_executable=True, risk_level="high")

    result = execute_one(
        db_session,
        {"tool_name": tool_name, "idempotency_key": f"{tool_name}-policy-execute"},
        ctx_with_tracking_and_contact(),
    )

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "tool_not_allowed_in_policy_execute"
    assert db_session.query(ToolCallLog).count() == 0


def test_speedaf_seed_keeps_high_risk_write_blocked(db_session):
    seed_default_tool_execution_policies(db_session, country_code="ME", channel="webchat")

    result = execute_one(
        db_session,
        {"tool_name": "speedaf.workOrder.create", "idempotency_key": "seeded-speedaf"},
        ctx_with_tracking_and_contact(),
    )

    assert result.mode == OSRToolExecutionMode.BLOCKED
    assert result.results[0].error_code == "tool_not_allowed_in_policy_execute"


def test_observe_only_audit_redacts_raw_arguments(db_session):
    result = OSRToolExecutionFacade(db_session).execute(
        tool_calls=[
            {
                "tool_name": "ticket.create",
                "idempotency_key": "observe-raw",
                "arguments": {
                    "tracking_number": "CH1234567890",
                    "phone": "+382 67123456",
                    "address": "123 Unsafe Street",
                    "raw_payload": {"token": "secret-value"},
                },
            }
        ],
        case_context=ctx_with_tracking_and_contact(),
    )

    assert result.mode == OSRToolExecutionMode.OBSERVE_ONLY
    audit = db_session.query(RuntimeDecisionAuditRecord).one()
    serialized = str(audit.decision_json)
    assert "CH1234567890" not in serialized
    assert "+382" not in serialized
    assert "123 Unsafe Street" not in serialized
    assert "secret-value" not in serialized
