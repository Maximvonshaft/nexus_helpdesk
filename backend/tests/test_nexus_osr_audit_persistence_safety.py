from __future__ import annotations

import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models as _models  # noqa: F401
from app import models_osr as _models_osr  # noqa: F401
from app import webchat_models as _webchat_models  # noqa: F401
from app.db import Base
from app.models_osr import RuntimeDecisionAuditRecord
from app.services.nexus_osr.case_context import CaseContext, ContactMethod
from app.services.nexus_osr.persistence import audit_runtime_decision
from app.services.nexus_osr.runtime_decision_contract import (
    BusinessReplyType,
    RuntimeAction,
    RuntimeDecision,
    RuntimeDecisionEvaluation,
    RuntimeDecisionViolation,
    RuntimeToolAction,
)


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def test_final_tool_audit_write_sanitizes_every_json_field(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-safety.db'}",
        future=True,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
    db = SessionLocal()

    raw_tracking = "CH1234567890"
    raw_phone = "+382 67123456"
    raw_email = "customer@example.test"
    raw_secret = "synthetic-credential-marker"
    raw_group = "120363999999999999@g.us"
    raw_address = "221 Baker Street"

    decision = RuntimeDecision(
        business_reply_type=BusinessReplyType.TOOL_ACTION_RESULT,
        next_action=RuntimeAction.CALL_TOOL,
        risk_level="high",
        tool_actions=[
            RuntimeToolAction(
                tool_name="ticket.create",
                arguments={
                    "ticket_id": 201,
                    "handoff_request_id": 301,
                    "tracking_number": raw_tracking,
                    "phone": raw_phone,
                    "email": raw_email,
                    "address": raw_address,
                    "token": raw_secret,
                },
                requires_confirmation=True,
                executed=False,
                result_source_id=raw_tracking,
            )
        ],
        audit_reasons=[
            f"Contact {raw_email} at {raw_phone} about {raw_tracking}"
        ],
    )
    evaluation = RuntimeDecisionEvaluation(
        allowed=False,
        violations=[
            RuntimeDecisionViolation(
                code="operator_review_required",
                message=f"Do not expose {raw_secret} for {raw_tracking}",
                severity="high",
            )
        ],
        warnings=[f"Address {raw_address}; contact {raw_email}"],
    )
    context = CaseContext(
        conversation_id=101,
        ticket_id=201,
        channel="webchat",
        country_code="ME",
        issue_type="external_action",
        customer_claim_summary=f"Parcel {raw_tracking} for {raw_email}",
        contact_methods=[
            ContactMethod(
                channel="whatsapp",
                value_redacted=raw_phone,
                source="customer_form",
            )
        ],
        last_mcp_fact={
            "provider_payload": {"api_key": raw_secret},
            "address": raw_address,
        },
        agent_handover_summary=f"Call {raw_phone} and route to {raw_group}",
    )

    try:
        row = audit_runtime_decision(
            db,
            decision=decision,
            evaluation=evaluation,
            case_context=context,
            tenant_id="tenant-a",
            channel="webchat",
            country_code="ME",
            conversation_id=101,
            ticket_id=201,
        )
        db.commit()
        stored = db.get(RuntimeDecisionAuditRecord, row.id)
        text = _serialized(
            {
                "violations": stored.violations_json,
                "warnings": stored.warnings_json,
                "decision": stored.decision_json,
                "case_context": stored.case_context_json,
            }
        )
        for raw in (
            raw_tracking,
            raw_phone,
            raw_email,
            raw_secret,
            raw_group,
            raw_address,
        ):
            assert raw not in text

        assert stored.business_reply_type == "tool_action_result"
        assert stored.next_action == "call_tool"
        assert stored.risk_level == "high"
        assert stored.decision_json["tool_actions"][0]["tool_name"] == "ticket.create"

        safe_arguments = stored.decision_json["tool_actions"][0]["arguments"]
        assert safe_arguments["ticket_id"] == 201
        assert safe_arguments["handoff_request_id"] == 301
        assert safe_arguments["redacted"] is True
        assert safe_arguments["redacted_field_count"] == 5
        for forbidden in (
            "tracking_number",
            "phone",
            "email",
            "address",
            "token",
        ):
            assert forbidden not in safe_arguments

        claim_summary = stored.case_context_json["customer_claim_summary"]
        assert isinstance(claim_summary, str)
        assert "[redacted_tracking]" in claim_summary
        assert "[redacted_email]" in claim_summary
    finally:
        db.close()
        engine.dispose()
