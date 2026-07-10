from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Column, Integer, Table, create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import osr_admin as osr_admin_api
from app.api.deps import get_current_user
from app.db import Base, get_db
from app.enums import UserRole
from app.models_osr import CaseContextRecord, RuntimeDecisionAuditRecord

if "webchat_conversations" not in Base.metadata.tables:
    Table("webchat_conversations", Base.metadata, Column("id", Integer, primary_key=True))
if "tickets" not in Base.metadata.tables:
    Table("tickets", Base.metadata, Column("id", Integer, primary_key=True))

TENANT_A = {"X-Nexus-Tenant": "tenant-a"}
TENANT_B = {"X-Nexus-Tenant": "tenant-b"}


def _serialized(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


@pytest.fixture()
def api_context(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True)
    SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    Base.metadata.create_all(engine)
    current = {"user": SimpleNamespace(id=1, role=UserRole.admin)}

    def override_get_db():
        session = SessionLocal()
        try:
            yield session
        finally:
            session.close()

    def override_current_user():
        return current["user"]

    monkeypatch.setattr(osr_admin_api, "ensure_can_manage_runtime", lambda user, db: None)
    app = FastAPI()
    app.include_router(osr_admin_api.router)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_current_user
    try:
        yield TestClient(app), SessionLocal, current
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_admin_crud_and_provider_group_redaction_are_consistent(api_context):
    client, _SessionLocal, _current = api_context
    raw_destination = "120363999999999999@g.us"
    raw_fallback = "120363888888888888@g.us"
    create = client.post(
        "/api/admin/osr/whatsapp-routing-rules",
        json={
            "country_code": "ME",
            "issue_type": "signed_not_received",
            "destination_group_id": raw_destination,
            "fallback_group_id": raw_fallback,
            "message_template": "Contact test@example.test or +382 67123456",
        },
    )
    assert create.status_code == 201
    rule_id = create.json()["id"]
    assert create.json()["configuration_scope"] == "global"

    responses = [
        create,
        client.get("/api/admin/osr/whatsapp-routing-rules"),
        client.get(f"/api/admin/osr/whatsapp-routing-rules/{rule_id}"),
        client.patch(f"/api/admin/osr/whatsapp-routing-rules/{rule_id}", json={"priority": 9}),
        client.get(
            "/api/admin/osr/policy-preview/whatsapp-routing",
            params={"country_code": "ME", "issue_type": "signed_not_received"},
        ),
        client.delete(f"/api/admin/osr/whatsapp-routing-rules/{rule_id}"),
    ]
    for response in responses:
        assert response.status_code == 200 or response.status_code == 201
        text = _serialized(response.json())
        assert raw_destination not in text
        assert raw_fallback not in text
        assert "test@example.test" not in text
        assert "+382 67123456" not in text
    detail = responses[2].json()
    assert detail["destination_group_id_present"] is True
    assert detail["destination_group_id_hash"]
    assert detail["destination_group_key"].startswith("provider-group:")
    assert "destination_group_id" not in detail
    assert "fallback_group_id" not in detail


def test_admin_can_manage_human_escalation_and_tool_policies(api_context):
    client, _SessionLocal, _current = api_context

    human = client.post("/api/admin/osr/human-hours-policies", json={
        "country_code": "ME",
        "channel": "webchat",
        "queue_key": "support",
        "timezone_name": "Europe/Podgorica",
        "working_hours_json": {"mon": [["09:00", "18:00"]]},
        "holiday_calendar_json": ["2026-07-13"],
    })
    assert human.status_code == 201
    human_id = human.json()["id"]
    assert client.get(f"/api/admin/osr/human-hours-policies/{human_id}").status_code == 200
    assert client.patch(f"/api/admin/osr/human-hours-policies/{human_id}", json={"handoff_enabled": False}).json()["handoff_enabled"] is False
    preview_human = client.get("/api/admin/osr/policy-preview/human-hours", params={"country_code": "ME", "channel": "webchat", "queue_key": "support"})
    assert preview_human.status_code == 200 and preview_human.json()["matched"] is True
    assert client.delete(f"/api/admin/osr/human-hours-policies/{human_id}").json()["disabled"] is True

    escalation = client.post("/api/admin/osr/escalation-policies", json={
        "risk_key": "compensation",
        "country_code": "ME",
        "channel": "webchat",
        "trigger_patterns_json": ["compensation"],
        "action": "handoff_or_ticket",
        "max_ai_attempts": 1,
    })
    assert escalation.status_code == 201
    escalation_id = escalation.json()["id"]
    assert client.patch(f"/api/admin/osr/escalation-policies/{escalation_id}", json={"ticket_required": True}).json()["ticket_required"] is True
    preview_escalation = client.get("/api/admin/osr/policy-preview/escalation", params={
        "country_code": "ME",
        "channel": "webchat",
        "message": "I want compensation for this delivery",
    })
    assert preview_escalation.status_code == 200
    assert preview_escalation.json()["matched"] is True
    assert preview_escalation.json()["message_echoed"] is False
    assert "I want compensation" not in _serialized(preview_escalation.json())
    assert client.delete(f"/api/admin/osr/escalation-policies/{escalation_id}").json()["disabled"] is True

    tool = client.post("/api/admin/osr/tool-execution-policies", json={
        "tool_name": "ticket.create",
        "country_code": "ME",
        "channel": "webchat",
        "enabled": True,
        "ai_auto_executable": True,
        "requires_tracking_number": True,
        "requires_contact": True,
    })
    assert tool.status_code == 201
    tool_id = tool.json()["id"]
    blocked = client.get("/api/admin/osr/policy-preview/tool-execution", params={
        "tool_name": "ticket.create", "country_code": "ME", "channel": "webchat",
    })
    assert blocked.status_code == 200
    assert blocked.json()["allowed"] is False
    assert sorted(blocked.json()["missing_requirements"]) == ["contact_method", "tracking_number"]
    allowed = client.get("/api/admin/osr/policy-preview/tool-execution", params={
        "tool_name": "ticket.create", "country_code": "ME", "channel": "webchat",
        "has_tracking_number": True, "has_contact": True,
    })
    assert allowed.status_code == 200 and allowed.json()["allowed"] is True
    assert client.patch(f"/api/admin/osr/tool-execution-policies/{tool_id}", json={"ai_auto_executable": False}).json()["ai_auto_executable"] is False
    assert client.delete(f"/api/admin/osr/tool-execution-policies/{tool_id}").json()["disabled"] is True


def test_policy_schema_validation_is_strict_and_redacted(api_context):
    client, _SessionLocal, _current = api_context
    cases = [
        ("/api/admin/osr/human-hours-policies", {"queue_key": "support", "timezone_name": "Not/AZone"}),
        ("/api/admin/osr/human-hours-policies", {"queue_key": "support", "working_hours_json": {"mon": [["25:00", "18:00"]]}}),
        ("/api/admin/osr/human-hours-policies", {"queue_key": "support", "holiday_calendar_json": ["2026-99-99"]}),
        ("/api/admin/osr/escalation-policies", {"risk_key": "bad-regex", "trigger_patterns_json": ["[broken"]}),
        ("/api/admin/osr/escalation-policies", {"risk_key": "bad-action", "action": "refund_now"}),
        ("/api/admin/osr/tool-execution-policies", {"tool_name": "unknown.tool"}),
    ]
    for path, payload in cases:
        response = client.post(path, json=payload)
        assert response.status_code == 422
        assert not any("input" in item or "ctx" in item for item in response.json()["detail"])


def test_validation_errors_do_not_echo_hostile_payload(api_context):
    client, _SessionLocal, _current = api_context
    secret = "sk-proj-THISMUSTNOTLEAK123456789"
    raw_group = "120363777777777777@g.us"
    response = client.post(
        "/api/admin/osr/whatsapp-routing-rules",
        json={
            "country_code": "ME",
            "issue_type": "signed_not_received",
            "destination_group_id": raw_group,
            "provider_payload": {"authorization": f"Bearer {secret}"},
        },
    )
    assert response.status_code == 422
    text = _serialized(response.json())
    assert secret not in text
    assert raw_group not in text
    assert not any("input" in item for item in response.json()["detail"])


def test_non_admin_is_rejected_even_when_runtime_capability_check_would_pass(api_context):
    client, _SessionLocal, current = api_context
    current["user"] = SimpleNamespace(id=2, role=UserRole.manager)
    response = client.get("/api/admin/osr/control-tower/summary", headers=TENANT_A)
    assert response.status_code == 403
    assert response.json()["detail"] == "osr_admin_required"


def test_tenant_isolation_for_list_detail_update_debug_and_control_tower(api_context):
    client, SessionLocal, _current = api_context
    with SessionLocal() as session:
        context_a = CaseContextRecord(
            tenant_id="tenant-a",
            conversation_id=101,
            ticket_id=201,
            channel="webchat",
            country_code="ME",
            issue_type="delivery",
            status="active",
            handoff_requested=True,
        )
        context_b = CaseContextRecord(
            tenant_id="tenant-b",
            conversation_id=102,
            ticket_id=202,
            channel="webchat",
            country_code="CH",
            issue_type="refund",
            status="active",
            handoff_requested=True,
        )
        audit_a = RuntimeDecisionAuditRecord(
            tenant_id="tenant-a",
            conversation_id=101,
            ticket_id=201,
            channel="webchat",
            country_code="ME",
            business_reply_type="handoff_notice",
            next_action="handoff",
            risk_level="high",
            allowed=False,
            violations_json=[{"code": "missing_tool_fact"}],
            decision_json={"business_reply_type": "handoff_notice", "next_action": "handoff"},
        )
        audit_b = RuntimeDecisionAuditRecord(
            tenant_id="tenant-b",
            conversation_id=102,
            ticket_id=202,
            channel="webchat",
            country_code="CH",
            business_reply_type="answer",
            next_action="reply",
            risk_level="low",
            allowed=True,
            decision_json={"business_reply_type": "answer", "next_action": "reply"},
        )
        session.add_all([context_a, context_b, audit_a, audit_b])
        session.commit()
        ids = context_a.id, context_b.id, audit_a.id, audit_b.id

    context_a_id, context_b_id, audit_a_id, audit_b_id = ids
    list_a = client.get("/api/admin/osr/case-contexts", headers=TENANT_A)
    assert list_a.status_code == 200
    assert list_a.json()["total"] == 1
    assert list_a.json()["items"][0]["id"] == context_a_id
    assert client.get(f"/api/admin/osr/case-contexts/{context_b_id}", headers=TENANT_A).status_code == 404
    assert client.patch(
        f"/api/admin/osr/case-contexts/{context_b_id}",
        headers=TENANT_A,
        json={"status": "human_review"},
    ).status_code == 404

    audits_a = client.get("/api/admin/osr/runtime-decision-audits", headers=TENANT_A)
    assert audits_a.json()["total"] == 1
    assert audits_a.json()["items"][0]["id"] == audit_a_id
    assert client.get(f"/api/admin/osr/runtime-decision-audits/{audit_b_id}", headers=TENANT_A).status_code == 404

    debug = client.get(
        "/api/admin/osr/debug-snapshot",
        headers=TENANT_A,
        params={"conversation_id": 101, "ticket_id": 201},
    )
    assert debug.status_code == 200
    assert debug.json()["tenant_id"] == "tenant-a"
    assert debug.json()["latest_runtime_audit"]["id"] == audit_a_id
    assert debug.json()["case_context_snapshot"]["id"] == context_a_id

    wrong_tenant_debug = client.get(
        "/api/admin/osr/debug-snapshot",
        headers=TENANT_A,
        params={"conversation_id": 102, "ticket_id": 202},
    )
    assert wrong_tenant_debug.status_code == 200
    assert wrong_tenant_debug.json()["latest_runtime_audit"] is None
    assert wrong_tenant_debug.json()["case_context_snapshot"] is None

    tower = client.get("/api/admin/osr/control-tower/summary", headers=TENANT_A)
    assert tower.status_code == 200
    assert tower.json()["tenant_id"] == "tenant-a"
    assert tower.json()["runtime_decisions"]["total"] == 1
    assert tower.json()["case_contexts"]["handoff_requested"] == 1
    assert tower.json()["runtime_decisions"]["by_country"][0]["key"] == "ME"


def test_runtime_audit_is_strictly_read_only_and_redacted(api_context):
    client, SessionLocal, _current = api_context
    raw_tracking = "CH1234567890"
    raw_phone = "+382 67123456"
    raw_email = "customer@example.test"
    raw_secret = "sk-proj-SENSITIVE123456789"
    raw_group = "120363666666666666@g.us"
    with SessionLocal() as session:
        row = RuntimeDecisionAuditRecord(
            tenant_id="tenant-a",
            channel="webchat",
            country_code="ME",
            business_reply_type="tracking_status_answer",
            next_action="reply",
            risk_level="medium",
            allowed=False,
            violations_json=[{"code": "tracking_without_tool", "tracking_number": raw_tracking}],
            warnings_json=[f"Call {raw_phone} or {raw_email}"],
            decision_json={
                "customer_reply": "raw reply",
                "provider_payload": {"api_key": raw_secret},
                "provider_group_id": raw_group,
                "business_reply_type": "tracking_status_answer",
                "next_action": "reply",
                "evidence_sources": [{"source_id": raw_tracking, "summary": f"Parcel {raw_tracking}"}],
                "tool_actions": [{"tool_name": "ticket.create", "arguments": {"phone": raw_phone}, "tool_result": raw_tracking}],
            },
            case_context_json={"tracking_number": raw_tracking, "email": raw_email},
        )
        session.add(row)
        session.commit()
        audit_id = row.id

    detail = client.get(f"/api/admin/osr/runtime-decision-audits/{audit_id}", headers=TENANT_A)
    assert detail.status_code == 200
    text = _serialized(detail.json())
    for raw in (raw_tracking, raw_phone, raw_email, raw_secret, raw_group, "raw reply"):
        assert raw not in text
    assert "arguments" not in text
    assert detail.json()["decision_summary"]["tool_actions"][0]["tool_name"] == "ticket.create"
    for method in (client.post, client.patch, client.delete):
        response = method(f"/api/admin/osr/runtime-decision-audits/{audit_id}", headers=TENANT_A, json={}) if method != client.delete else method(f"/api/admin/osr/runtime-decision-audits/{audit_id}", headers=TENANT_A)
        assert response.status_code == 405


def test_case_context_allows_only_safe_fields_and_redacts_before_persistence(api_context):
    client, SessionLocal, _current = api_context
    with SessionLocal() as session:
        row = CaseContextRecord(
            tenant_id="tenant-a",
            channel="webchat",
            country_code="ME",
            issue_type="delivery",
            status="active",
        )
        session.add(row)
        session.commit()
        context_id = row.id

    raw = "Call +382 67123456 about CH1234567890 or test@example.test"
    updated = client.patch(
        f"/api/admin/osr/case-contexts/{context_id}",
        headers=TENANT_A,
        json={
            "status": "human_review",
            "routed_group_key": "ops-tier2",
            "agent_handover_summary": raw,
            "missing_info_json": ["proof_of_identity"],
        },
    )
    assert updated.status_code == 200
    text = _serialized(updated.json())
    assert "+382 67123456" not in text
    assert "CH1234567890" not in text
    assert "test@example.test" not in text
    assert updated.json()["routed_group_key"] == "ops-tier2"

    with SessionLocal() as session:
        stored = session.get(CaseContextRecord, context_id)
        assert "+382 67123456" not in (stored.agent_handover_summary or "")
        assert "CH1234567890" not in (stored.agent_handover_summary or "")
        assert "test@example.test" not in (stored.agent_handover_summary or "")

    unsafe = client.patch(
        f"/api/admin/osr/case-contexts/{context_id}",
        headers=TENANT_A,
        json={"tracking_number_hash": "must-not-write"},
    )
    assert unsafe.status_code == 422
    assert "must-not-write" not in _serialized(unsafe.json())


def test_debug_snapshot_has_unified_contract_and_no_raw_payloads(api_context):
    client, SessionLocal, _current = api_context
    with SessionLocal() as session:
        session.add(CaseContextRecord(
            tenant_id="tenant-a",
            conversation_id=301,
            ticket_id=401,
            channel="webchat",
            country_code="ME",
            issue_type="delivery",
            status="active",
            routed_group_key="ops-tier2",
            last_mcp_fact_json={"tracking_number": "ME123456789", "phone": "+382 67123456"},
        ))
        session.add(RuntimeDecisionAuditRecord(
            tenant_id="tenant-a",
            conversation_id=301,
            ticket_id=401,
            channel="webchat",
            country_code="ME",
            business_reply_type="tracking_status_answer",
            next_action="reply",
            risk_level="medium",
            allowed=False,
            violations_json=[{"code": "missing_tool_fact"}],
            decision_json={
                "business_reply_type": "tracking_status_answer",
                "next_action": "reply",
                "evidence_sources": [{"source_id": "ME123456789", "summary": "Raw ME123456789"}],
                "tool_actions": [{"tool_name": "ticket.create", "arguments": {"tracking_number": "ME123456789"}, "executed": False}],
            },
        ))
        session.commit()

    response = client.get(
        "/api/admin/osr/debug-snapshot",
        headers=TENANT_A,
        params={"conversation_id": 301, "ticket_id": 401},
    )
    assert response.status_code == 200
    payload = response.json()
    expected = {
        "mode", "reply_metadata_audit", "latest_runtime_audit", "case_context_snapshot",
        "policy_snapshot", "tool_execution_summary", "operations_dispatch_summary",
        "evidence_sources", "missing_evidence",
    }
    assert expected.issubset(payload)
    assert payload["mode"] == "audit_only"
    assert payload["operations_dispatch_summary"]["routed"] is True
    text = _serialized(payload)
    assert "ME123456789" not in text
    assert "+382 67123456" not in text
    assert "arguments" not in text


def test_webchat_debug_sanitizer_blocks_hostile_provider_tool_and_contact_payloads():
    from app.services import webchat_debug_bundle_service as debug_service

    raw_tracking = "CH1234567890"
    raw_phone = "+382 67123456"
    raw_email = "debug@example.test"
    raw_secret = "sk-proj-DEBUGSECRET123456789"
    raw_group = "120363555555555555@g.us"
    sanitized = debug_service._sanitize_value({
        "provider_payload": {"authorization": f"Bearer {raw_secret}"},
        "tool_arguments": {"tracking_number": raw_tracking, "phone": raw_phone},
        "tool_result": {"email": raw_email},
        "destination_group_id": raw_group,
        "status_reason": f"Contact {raw_email} at {raw_phone} about {raw_tracking}",
    })
    text = _serialized(sanitized)
    for raw in (raw_tracking, raw_phone, raw_email, raw_secret, raw_group):
        assert raw not in text
    assert sanitized["provider_payload"]["redacted"] is True
    assert sanitized["tool_arguments"]["redacted"] is True
    assert sanitized["tool_result"]["redacted"] is True
    assert sanitized["destination_group_id"]["redacted"] is True


def test_route_mount_and_webchat_debug_composition_are_present():
    root = Path(__file__).resolve().parents[1]
    main_source = (root / "app" / "main.py").read_text(encoding="utf-8")
    debug_source = (root / "app" / "services" / "webchat_debug_bundle_service.py").read_text(encoding="utf-8")
    assert "from .api.osr_admin import router as osr_admin_router" in main_source
    assert "app.include_router(osr_admin_router)" in main_source
    assert "'X-Nexus-Tenant'" in main_source or '"X-Nexus-Tenant"' in main_source
    assert "build_osr_debug_snapshot" in debug_source
    assert "tenant_id=conversation.tenant_key or \"default\"" in debug_source
