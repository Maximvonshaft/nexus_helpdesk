from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.output_contracts import OutputContracts
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class PersonaIgnoredAdapter(ProviderAdapter):
    name = "codex_app_server"

    async def generate(self, db, req):
        return ProviderResult(
            ok=True,
            provider=self.name,
            elapsed_ms=25,
            structured_output={
                "customer_reply": "Hello! How can I help you today?",
                "language": "en",
                "intent": "general_support",
                "confidence": 0.8,
                "risk_level": "low",
                "next_action": "reply",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
                "ticket_should_create": False,
                "tool_calls": [],
                "evidence_used": [],
                "safety_notes": [],
            },
            raw_payload_safe_summary={"bridge_status": 200},
        )


def _raw_reply(reply: str, *, intent: str = "other", tracking_number: str | None = None) -> str:
    return json.dumps(
        {
            "customer_reply": reply,
            "language": "zh",
            "intent": intent,
            "tracking_number": tracking_number,
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
            "ticket_should_create": False,
            "internal_summary": None,
            "risk_flags": [],
        },
        ensure_ascii=False,
    )


def _identity_persona(**content):
    return {
        "profile_key": "monkey.king.website",
        "name": "Monkey King Website Persona",
        "summary": "Customer-facing identity comes from draft content.",
        "content_json": content,
        "identity_context": {
            "brand_name": content.get("brand_name"),
            "assistant_name": content.get("assistant_name"),
            "role_label": content.get("role_label"),
            "identity_statement": content.get("identity_statement"),
            "identity_answer_rule": content.get("identity_answer_rule"),
            "capabilities": content.get("capabilities") or [],
            "disallowed_identity_claims": content.get("disallowed_identity_claims") or [],
            "handoff_boundary": content.get("handoff_boundary"),
        },
    }


def _parse_identity(body: str, provider_reply: str = "我是 NexusDesk 客服。", **content):
    return OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        _raw_reply(provider_reply),
        evidence_present=False,
        persona_context=_identity_persona(**content),
        request_body=body,
    )


def test_output_contract_enforces_persona_prefix_on_customer_reply():
    parsed = OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        """
        {
          "reply": "Hello! How can I help you today?",
          "intent": "greeting",
          "tracking_number": null,
          "handoff_required": false,
          "handoff_reason": null,
          "recommended_agent_action": null
        }
        """,
        evidence_present=False,
        persona_context={"content_json": {"must_prefix": "SPEEDY_PERSONA_OK"}},
    )

    assert parsed["customer_reply"] == "SPEEDY_PERSONA_OK Hello! How can I help you today?"
    assert parsed["intent"] == "greeting"


def test_output_contract_does_not_duplicate_existing_persona_prefix():
    parsed = OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        """
        {
          "customer_reply": "SPEEDY_PERSONA_OK Already applied.",
          "language": "en",
          "intent": "greeting",
          "handoff_required": false,
          "ticket_should_create": false
        }
        """,
        persona_context={"content_json": {"must_prefix": "SPEEDY_PERSONA_OK"}},
    )

    assert parsed["customer_reply"] == "SPEEDY_PERSONA_OK Already applied."


def test_output_contract_rejects_unsafe_persona_prefix():
    parsed = OutputContracts.validate_and_parse(
        "speedaf_webchat_fast_reply_v1",
        """
        {
          "reply": "Hello!",
          "intent": "greeting",
          "tracking_number": null,
          "handoff_required": false,
          "handoff_reason": null,
          "recommended_agent_action": null
        }
        """,
        persona_context={"content_json": {"must_prefix": "bridge"}},
    )

    assert parsed["customer_reply"] == "Hello!"


@pytest.mark.asyncio
async def test_router_accepts_webchat_ai_decision_output_and_preserves_security(monkeypatch):
    import app.services.provider_runtime as provider_runtime_module

    monkeypatch.setattr(provider_runtime_module, "bootstrap_provider_runtime", lambda: None)
    ProviderRegistry.register("codex_app_server", lambda db: PersonaIgnoredAdapter())

    mock_db = Mock()
    select_result = Mock()
    select_result.mappings.return_value.first.return_value = {
        "primary_provider": "codex_app_server",
        "fallback_providers": [],
        "output_contract": "speedaf_webchat_fast_reply_v1",
        "timeout_ms": 10000,
        "kill_switch": False,
        "canary_percent": 100,
    }

    def db_execute(stmt, params=None, *args, **kwargs):
        if "INSERT INTO provider_runtime_audit_logs" in str(stmt):
            return Mock()
        return select_result

    mock_db.execute.side_effect = db_execute

    req = ProviderRequest(
        request_id="req-persona-prefix",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess-persona-prefix",
        scenario="webchat_fast_reply",
        body="hello",
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=10000,
        metadata={"persona_context": {"content_json": {"must_prefix": "SPEEDY_PERSONA_OK"}}},
    )

    result = await ProviderRuntimeRouter(mock_db).route(req)

    assert result.ok is True
    assert result.provider == "codex_app_server"
    assert result.structured_output["customer_reply"] == "Hello! How can I help you today?"
    assert result.structured_output["next_action"] == "reply"
    assert result.structured_output["tool_calls"] == []


def test_identity_question_uses_persona_brand_name():
    parsed = _parse_identity("你是谁", brand_name="猴王山")

    assert "猴王山" in parsed["customer_reply"]
    assert "AI 客服" in parsed["customer_reply"]
    assert "NexusDesk" not in parsed["customer_reply"]


def test_what_support_identity_question_does_not_leak_nexusdesk():
    parsed = _parse_identity("你是什么客服", brand_name="猴王山")

    assert "猴王山" in parsed["customer_reply"]
    assert "NexusDesk" not in parsed["customer_reply"]


def test_are_you_brand_support_is_affirmative():
    parsed = _parse_identity("你是否是猴王山客服", brand_name="猴王山")

    assert "猴王山" in parsed["customer_reply"]
    assert "不是" not in parsed["customer_reply"]
    assert "NexusDesk" not in parsed["customer_reply"]


def test_normal_greeting_is_not_deterministically_rewritten():
    parsed = _parse_identity("你好", provider_reply="普通问候回复", brand_name="猴王山")

    assert parsed["customer_reply"] == "普通问候回复"


def test_identity_question_rewrites_provider_nexusdesk_claim_to_persona_brand():
    parsed = _parse_identity("你是谁", provider_reply="我是 NexusDesk 客服。", brand_name="猴王山")

    assert "猴王山" in parsed["customer_reply"]
    assert "NexusDesk" not in parsed["customer_reply"]


def test_visible_prefix_enforcement_still_applies():
    parsed = _parse_identity("你好", provider_reply="普通问候回复", brand_name="猴王山", visible_prefix="SPEEDY_PERSONA_OK")

    assert parsed["customer_reply"].startswith("SPEEDY_PERSONA_OK ")
    assert parsed["customer_reply"].endswith("普通问候回复")


def test_tracking_truth_boundary_still_blocks_invented_status():
    with pytest.raises(ValueError, match="requires trusted tracking evidence"):
        OutputContracts.validate_and_parse(
            "speedaf_webchat_fast_reply_v1",
            _raw_reply("Your parcel is in transit.", intent="tracking", tracking_number="ABC123456"),
            evidence_present=False,
            persona_context=_identity_persona(brand_name="猴王山"),
            request_body="Where is my parcel?",
        )
