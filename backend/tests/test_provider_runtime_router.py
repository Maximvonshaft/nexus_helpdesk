import pytest
from unittest.mock import Mock

from app.services import provider_runtime as provider_runtime_module
from app.services.provider_runtime.registry import ProviderAdapter, ProviderRegistry
from app.services.provider_runtime.router import ProviderRuntimeRouter
from app.services.provider_runtime.schemas import ProviderRequest, ProviderResult


class DummyAdapter(ProviderAdapter):
    def __init__(self, name, result):
        self.name = name
        self._result = result

    async def generate(self, db, req):
        return self._result


@pytest.fixture(autouse=True)
def isolated_provider_registry(monkeypatch):
    monkeypatch.setattr(provider_runtime_module, "_BOOTSTRAPPED", True)
    monkeypatch.setattr(ProviderRegistry, "_factories", {})
    yield


def _mock_db(rule: dict):
    mock_db = Mock()
    mock_rule = Mock()
    mock_rule.mappings.return_value.first.return_value = rule

    def mock_db_execute(stmt, params, *args, **kwargs):
        query = str(stmt).lower()
        if "insert into provider_runtime_audit_logs" in query:
            return Mock()
        return mock_rule

    mock_db.execute.side_effect = mock_db_execute
    return mock_db


def _request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req1",
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id="s1",
        scenario="webchat_runtime_reply",
        body="hello",
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
    )


def _trusted_tracking_followup_request() -> ProviderRequest:
    return ProviderRequest(
        request_id="req-tracking-followup",
        tenant_id="t1",
        tenant_key="tk1",
        channel_key="c1",
        session_id="s1",
        scenario="webchat_runtime_reply",
        body="The recipient says they did not receive it. What should we do?",
        tracking_fact_summary="Trusted tracking fact: parcel ending 007813 is delivered.",
        tracking_fact_evidence_present=True,
        output_contract="nexus_webchat_runtime_reply_v1",
        timeout_ms=1000,
        metadata={
            "knowledge_context": {
                "locked_facts": [
                    {
                        "item_key": "nexus.support.customer.kb.ch.service.availability",
                        "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                        "source": {"item_key": "nexus.support.customer.kb.ch.service.availability"},
                    }
                ]
            }
        },
    )


@pytest.mark.asyncio
async def test_provider_runtime_router_single_runtime_success_and_audit():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    ProviderRegistry.register(
        "private_ai_runtime",
        lambda db: DummyAdapter(
            "private_ai_runtime",
            ProviderResult(
                ok=True,
                provider="private_ai_runtime",
                elapsed_ms=100,
                structured_output={
                    "customer_reply": "hi",
                    "language": "en",
                    "intent": "greeting",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
            ),
        ),
    )

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert result.ok
    assert result.provider == "private_ai_runtime"
    assert result.structured_output["customer_reply"] == "hi"
    assert mock_db.execute.call_count == 2


@pytest.mark.asyncio
async def test_provider_runtime_router_parse_reject_returns_no_customer_reply():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    ProviderRegistry.register(
        "private_ai_runtime",
        lambda db: DummyAdapter(
            "private_ai_runtime",
            ProviderResult(ok=True, provider="private_ai_runtime", elapsed_ms=100, structured_output={"customer_reply": "hi"}),
        ),
    )

    result = await ProviderRuntimeRouter(mock_db).route(_request())

    assert not result.ok
    assert result.error_code == "all_providers_failed"
    assert mock_db.execute.call_count == 3


@pytest.mark.asyncio
async def test_provider_runtime_router_accepts_trusted_tracking_followup_with_unrelated_locked_fact():
    mock_db = _mock_db(
        {
            "primary_provider": "private_ai_runtime",
            "fallback_providers": [],
            "output_contract": "nexus_webchat_runtime_reply_v1",
            "timeout_ms": 3000,
            "kill_switch": False,
            "canary_percent": 100,
        }
    )
    ProviderRegistry.register(
        "private_ai_runtime",
        lambda db: DummyAdapter(
            "private_ai_runtime",
            ProviderResult(
                ok=True,
                provider="private_ai_runtime",
                elapsed_ms=100,
                structured_output={
                    "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.",
                    "language": "en",
                    "intent": "tracking",
                    "tracking_number": "CH020000007813",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
            ),
        ),
    )

    result = await ProviderRuntimeRouter(mock_db).route(_trusted_tracking_followup_request())

    assert result.ok
    assert "007813" in result.structured_output["customer_reply"]
