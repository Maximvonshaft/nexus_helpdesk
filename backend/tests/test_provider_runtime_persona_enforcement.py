from __future__ import annotations

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
                "reply": "Hello! How can I help you today?",
                "intent": "greeting",
                "tracking_number": None,
                "handoff_required": False,
                "handoff_reason": None,
                "recommended_agent_action": None,
            },
            raw_payload_safe_summary={"bridge_status": 200},
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
async def test_router_applies_backend_persona_prefix_after_provider_output(monkeypatch):
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
    assert result.structured_output["customer_reply"] == "SPEEDY_PERSONA_OK Hello! How can I help you today?"
