import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.services.ai_runtime.schemas import FastAIProviderResult
from app.services.webchat_fast_ai_service import generate_webchat_fast_reply


def _approved_shipping_sla_context(*, entity_terms: list[str] | None = None) -> dict:
    source = {
        "item_key": "fact.probe.ch.sea-sla",
        "title": "瑞士海运时效",
        "score": 161.04,
        "chunk_index": 0,
        "answer_mode": "direct_answer",
        "retrieval_method": "structured_fact_recall+direct_answer_fact",
        "source_metadata": {
            "item_key": "fact.probe.ch.sea-sla",
            "title": "瑞士海运时效",
            "knowledge_kind": "business_fact",
            "fact_status": "approved",
            "answer_mode": "direct_answer",
        },
    }
    return {
        "context_version": "nexus_webchat_runtime_context_v1",
        "tenant_key": "default",
        "knowledge_context": {
            "retrieval": "hybrid_metadata_fusion_v1",
            "grounding_would_apply": True,
            "grounding_source": source,
            "locked_facts": [
                {
                    "item_key": "fact.probe.ch.sea-sla",
                    "title": "瑞士海运时效",
                    "question": "瑞士海运时效是多少？",
                    "answer": "瑞士海运时效为 15 天。",
                    "answer_mode": "direct_answer",
                    "source": source,
                }
            ],
            "hits": [
                {
                    "item_key": "fact.probe.ch.sea-sla",
                    "title": "瑞士海运时效",
                    "text": "Question: 瑞士海运时效是多少？\nAnswer: 瑞士海运时效为 15 天。",
                    "score": 161.04,
                    "chunk_index": 0,
                    "retrieval_method": "structured_fact_recall+direct_answer_fact",
                    "direct_answer": "瑞士海运时效为 15 天。",
                    "answer_mode": "direct_answer",
                    "metadata": {
                        "knowledge_kind": "business_fact",
                        "fact_status": "approved",
                        "answer_mode": "direct_answer",
                    },
                    "source_metadata": source["source_metadata"],
                }
            ],
            "query_analysis": {
                "language": "zh",
                "entity_terms": entity_terms if entity_terms is not None else ["瑞士"],
                "high_value_terms": ["瑞士", "海运", "时效"],
                "terms": ["瑞士", "海运", "时效"],
            },
            "candidate_count": 1,
            "total_matches": 1,
        },
        "safety_policy": {"knowledge_scope": "policy_sop_faq_only"},
    }


def _fast_settings() -> SimpleNamespace:
    return SimpleNamespace(enabled=True, provider="provider_runtime")


async def _run_fast_reply(body: str, runtime_context: dict, dispatch_result: FastAIProviderResult | None = None):
    if dispatch_result is None:
        dispatch_result = FastAIProviderResult.unavailable(
            provider="provider_runtime",
            error_code="provider_called",
            elapsed_ms=0,
        )
    with patch("app.services.webchat_fast_ai_service.get_webchat_fast_settings", return_value=_fast_settings()):
        with patch("app.services.webchat_fast_ai_service._runtime_context_for_request", return_value=runtime_context):
            with patch("app.services.webchat_fast_ai_service.dispatch_webchat_fast_reply", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.return_value = dispatch_result
                with patch("app.services.webchat_fast_ai_service.generate_fast_reply", new_callable=AsyncMock) as mock_generate:
                    result = await generate_webchat_fast_reply(
                        tenant_key="default",
                        channel_key="website",
                        session_id="session_direct_answer",
                        body=body,
                        recent_context=[],
                        request_id="req-direct-answer",
                        language="zh",
                    )
    return result, mock_dispatch, mock_generate


@pytest.mark.asyncio
async def test_pre_provider_locked_fact_direct_answer_bypasses_provider():
    result, mock_dispatch, mock_generate = await _run_fast_reply(
        "瑞士海运时效是多少？",
        _approved_shipping_sla_context(),
    )

    assert result.ok is True
    assert result.ai_generated is False
    assert result.reply_source == "knowledge:deterministic_direct_answer"
    assert result.reply == "瑞士海运时效为 15 天。"
    assert result.intent == "other"
    assert result.tracking_number is None
    assert result.handoff_required is False
    assert result.grounding_applied is True
    assert result.grounding_reason == "pre_provider_locked_fact_direct_answer"
    assert result.grounding_source["item_key"] == "fact.probe.ch.sea-sla"
    mock_dispatch.assert_not_called()
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_pre_provider_locked_fact_blocks_tracking_query_and_uses_provider_flow():
    result, mock_dispatch, mock_generate = await _run_fast_reply(
        "PK120053679836 现在在哪里，瑞士海运时效是多少？",
        _approved_shipping_sla_context(),
    )

    assert result.ok is False
    assert result.error_code == "provider_called"
    mock_dispatch.assert_awaited_once()
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_pre_provider_locked_fact_blocks_compensation_query_and_uses_provider_flow():
    result, mock_dispatch, mock_generate = await _run_fast_reply(
        "如果瑞士海运晚了可以赔偿吗，瑞士海运多久？",
        _approved_shipping_sla_context(),
    )

    assert result.ok is False
    assert result.error_code == "provider_called"
    mock_dispatch.assert_awaited_once()
    mock_generate.assert_not_called()


@pytest.mark.asyncio
async def test_pre_provider_locked_fact_blocks_wrong_entity_and_uses_provider_flow():
    result, mock_dispatch, mock_generate = await _run_fast_reply(
        "尼日利亚海运时效是多少？",
        _approved_shipping_sla_context(entity_terms=["尼日利亚"]),
    )

    assert result.ok is False
    assert result.error_code == "provider_called"
    mock_dispatch.assert_awaited_once()
    mock_generate.assert_not_called()
