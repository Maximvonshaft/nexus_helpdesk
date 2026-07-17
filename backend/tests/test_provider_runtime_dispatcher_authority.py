from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.ai_runtime.schemas import RuntimeAIProviderRequest
from app.services.provider_runtime.schemas import ProviderResult
import app.services.provider_runtime.webchat_runtime_dispatcher as dispatcher


class _DummySession:
    def close(self) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_code,path",
    [
        ("provider_canary_control_path", "control"),
        ("provider_shadow_only", "shadow_only"),
        ("kill_switch_active", "kill_switch"),
    ],
)
async def test_non_authoritative_traffic_cannot_create_reply_or_action_authority(
    monkeypatch,
    error_code: str,
    path: str,
):
    monkeypatch.setattr(dispatcher, "SessionLocal", lambda: _DummySession())
    monkeypatch.setattr(
        dispatcher,
        "build_webchat_runtime_context",
        lambda *args, **kwargs: {
            "context_version": "nexus.webchat_runtime_context",
            "knowledge_context": {
                "retrieval": "unavailable",
                "locked_facts": [],
                "hits": [],
            },
        },
    )
    route = AsyncMock(
        return_value=ProviderResult(
            ok=False,
            provider="router",
            elapsed_ms=7,
            structured_output=None,
            raw_payload_safe_summary={
                "traffic": {
                    "path": path,
                    "authoritative": False,
                    "execute_candidate": path == "shadow_only",
                }
            },
            error_code=error_code,
            fallback_allowed=False,
        )
    )
    monkeypatch.setattr(dispatcher.ProviderRuntimeRouter, "route", route)

    result = await dispatcher.dispatch_webchat_runtime_reply(
        request=RuntimeAIProviderRequest(
            tenant_key="tenant-1",
            channel_key="webchat",
            session_id="session-1",
            request_id="request-1",
            body="hello",
        )
    )

    assert result.ok is False
    assert result.ai_generated is False
    assert result.reply is None
    assert result.intent is None
    assert result.handoff_required is False
    assert result.recommended_agent_action is None
    assert result.tool_intents == []
    assert result.error_code == error_code
    assert result.raw_payload_safe_summary == {
        "traffic": {
            "path": path,
            "authoritative": False,
            "execute_candidate": path == "shadow_only",
        },
        "provider_runtime": True,
        "provider_bypassed": False,
    }
    route.assert_awaited_once()


def test_ai_decision_summary_contains_no_customer_reply_or_reason_text():
    summary = dispatcher._bounded_ai_decision_summary(
        {
            "customer_reply": "private customer-facing reply",
            "intent": "tracking",
            "confidence": 0.9,
            "risk_level": "low",
            "next_action": "reply",
            "handoff_required": False,
            "handoff_reason": "private operator reason",
            "tool_calls": [{"name": "secret"}],
            "evidence_used": ["private evidence"],
            "safety_notes": ["private note"],
        }
    )

    assert summary == {
        "intent": "tracking",
        "confidence": 0.9,
        "risk_level": "low",
        "next_action": "reply",
        "handoff_required": False,
        "tool_call_count": 1,
        "evidence_count": 1,
        "safety_note_count": 1,
    }
    assert "private" not in str(summary)
    assert "customer_reply" not in summary
    assert "handoff_reason" not in summary


def test_rag_trace_contains_no_query_title_or_matched_terms(monkeypatch):
    monkeypatch.setattr(
        dispatcher,
        "summarize_rag_trace",
        lambda context: {
            "retrieval": "hybrid",
            "query_analysis": {"normalized_query": "private customer query"},
            "candidate_count": 4,
            "total_matches": 2,
            "retrieval_methods": ["keyword", "vector"],
            "no_answer_reason": "none",
            "latency_ms": 31,
            "evidence_pack": [
                {
                    "item_key": "kb.delivery.policy",
                    "title": "private title",
                    "published_version": 2,
                    "chunk_index": 1,
                    "score": 0.8,
                    "retrieval_method": "keyword",
                    "citation": {"quote": "private text"},
                }
            ],
            "injected_knowledge": [
                {
                    "item_key": "kb.delivery.policy",
                    "title": "private title",
                    "score": 0.8,
                    "retrieval_method": "keyword",
                    "matched_terms": ["private term"],
                }
            ],
            "grounding_would_apply": True,
            "grounding_source": {"answer": "private answer"},
        },
    )

    summary = dispatcher._bounded_rag_trace({"context_version": "v1"})

    assert summary == {
        "retrieval": "hybrid",
        "candidate_count": 4,
        "total_matches": 2,
        "latency_ms": 31,
        "retrieval_methods": ["keyword", "vector"],
        "no_answer_reason": "none",
        "grounding_would_apply": True,
        "evidence_pack": [
            {
                "item_key": "kb.delivery.policy",
                "retrieval_method": "keyword",
                "published_version": 2,
                "chunk_index": 1,
                "score": 0.8,
            }
        ],
        "injected_knowledge": [
            {
                "item_key": "kb.delivery.policy",
                "retrieval_method": "keyword",
                "score": 0.8,
            }
        ],
    }
    assert "private" not in str(summary)
    assert "query_analysis" not in summary
    assert "title" not in str(summary)
    assert "matched_terms" not in str(summary)
