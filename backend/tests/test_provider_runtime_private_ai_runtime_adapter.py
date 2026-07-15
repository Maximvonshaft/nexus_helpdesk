from __future__ import annotations

import json
import urllib.error
from unittest.mock import Mock

import pytest

from app.services.provider_runtime.adapters.private_ai_runtime import (
    PrivateAIRuntimeAdapter,
    _compact_tracking_fact_summary,
    _customer_intent_hint,
    _compact_unified_knowledge_context,
    _customer_tracking_fact_prompt_summary,
    _customer_visible_knowledge_context,
    _normalize_tracking_safe_reference_wording,
    _normalize_runtime_output,
    _remove_verified_tracking_identifier_request_sentences,
    _request_language_hint,
    _reply_requests_missing_logistics_identifier,
    _reply_requests_logistics_identifier_after_verified_fact,
    _tracking_safe_reference_misuse,
)
from app.services.provider_runtime.schemas import ProviderRequest


def _request(**overrides) -> ProviderRequest:
    data = {
        "request_id": "req-private-ai-1",
        "tenant_id": "default",
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "sess-private-ai-1",
        "scenario": "webchat_runtime_reply",
        "body": "Where is my parcel?",
        "recent_context": [{"role": "user", "content": "hello"}],
        "tracking_fact_summary": None,
        "tracking_fact_evidence_present": False,
        "output_contract": "nexus.webchat_runtime_reply",
        "timeout_ms": 8000,
        "metadata": {
            "knowledge_context": {"hits": [], "raw_payload": "must-not-leak"},
            "persona_context": {"name": "Speedaf Assistant", "secret": "must-not-leak"},
        },
    }
    data.update(overrides)
    return ProviderRequest(**data)


def test_private_ai_runtime_language_hint_uses_latest_german_message() -> None:
    request = _request(
        body="kannst du mal schauen, welche Zustand die Sendung CH020000129026 ist?",
        metadata={"customer_language": "de", "language": "de"},
    )

    assert _request_language_hint(request) == "de"


def test_tracking_identifier_validator_allows_confirmation_of_existing_chinese_reference() -> None:
    assert _reply_requests_missing_logistics_identifier("请确认您提供的运单号是否完整且正确。") is False


def test_tracking_identifier_validator_blocks_request_to_send_chinese_reference_again() -> None:
    assert _reply_requests_missing_logistics_identifier("请重新发送您的运单号。") is True


def test_private_ai_runtime_language_hint_ignores_reference_only_default_language() -> None:
    request = _request(body="CH020000129026", metadata={"language": "en"})

    assert _request_language_hint(request) is None


def test_unified_knowledge_context_does_not_lock_facts_for_followup_complaint() -> None:
    context = _compact_unified_knowledge_context(
        {
            "locked_facts": [
                {
                    "item_key": "nexus.support.customer.kb.ch.service.availability",
                    "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                }
            ],
            "hits": [
                {
                    "item_key": "nexus.support.customer.kb.ch.service.availability",
                    "direct_answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                }
            ],
        },
        intent_hint="logistics_or_tracking",
    )

    assert "locked_facts" not in context


def test_customer_visible_context_direct_answer_filters_generic_locked_facts():
    context = _customer_visible_knowledge_context(
        {
            "hits": [
                {
                    "item_key": "nexus.support.customer.kb.ch.service.availability",
                    "title": "Switzerland domestic service availability",
                    "direct_answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                    "answer_mode": "direct_answer",
                    "metadata": {"citation": {"customer_visible": True}},
                },
                {
                    "item_key": "prod.global.tracking-number.required",
                    "title": "Tracking number required",
                    "direct_answer": "To check parcel status, the customer must provide a tracking number.",
                    "answer_mode": "direct_answer",
                    "metadata": {"citation": {"customer_visible": True}},
                },
            ],
            "locked_facts": [
                {
                    "item_key": "nexus.support.customer.kb.ch.service.availability",
                    "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                    "answer_mode": "direct_answer",
                },
                {
                    "item_key": "prod.global.tracking-number.required",
                    "answer": "To check parcel status, the customer must provide a tracking number.",
                },
            ],
        },
        direct_answer_only=True,
    )

    locked_keys = [fact["item_key"] for fact in context["locked_facts"]]
    assert locked_keys == ["nexus.support.customer.kb.ch.service.availability"]


def test_customer_visible_context_uses_structured_fact_answer_as_locked_fact():
    context = _customer_visible_knowledge_context(
        {
            "hits": [
                {
                    "item_key": "qa.production.answer",
                    "title": "生产知识闭环冒烟",
                    "answer_mode": "guided_answer",
                    "text": "Question: 客户问生产知识闭环暗号是什么？ Answer: 生产知识闭环暗号是 canyon-lime。",
                    "metadata": {
                        "customer_visible": True,
                        "fact_answer": "生产知识闭环暗号是 canyon-lime。",
                    },
                }
            ]
        },
        direct_answer_only=True,
        derive_locked_facts=True,
    )

    assert context["locked_facts"] == [
        {
            "item_key": "qa.production.answer",
            "title": "生产知识闭环冒烟",
            "question": "生产知识闭环冒烟",
            "answer": "生产知识闭环暗号是 canyon-lime。",
            "answer_mode": "direct_answer",
        }
    ]


def test_customer_intent_hint_keeps_plain_numeric_smoke_text_general():
    assert _customer_intent_hint("hello latency smoke 1783325498843") == "general_support"
    assert _customer_intent_hint("tracking 1783325498843") == "logistics_or_tracking"
    assert _customer_intent_hint("1783325498843") == "logistics_or_tracking"
    assert _customer_intent_hint("CH020000129135") == "logistics_or_tracking"
    assert _customer_intent_hint("请告诉我生产知识闭环暗号 mr9atbis") == "general_support"
    assert _customer_intent_hint("你好，普通客服测试 mr9atbis") == "general_support"


def test_tracking_safe_reference_allows_same_language_suffix_only_wording():
    summary = "Trusted tracking fact:\n- Tracking reference: parcel ending 129135\n- Current status: pending pickup"

    assert _tracking_safe_reference_misuse("您的运单尾号 129135 当前等待揽收。", tracking_fact_summary=summary) is False
    assert _tracking_safe_reference_misuse("尾号 129135 的包裹当前等待揽收。", tracking_fact_summary=summary) is False
    assert _tracking_safe_reference_misuse("您的运单号是 129135，当前等待揽收。", tracking_fact_summary=summary) is True


def test_verified_tracking_identifier_request_detection_is_not_reference_mention_ban():
    assert _reply_requests_logistics_identifier_after_verified_fact("Please have the tracking reference ready.") is True
    assert _reply_requests_logistics_identifier_after_verified_fact("I will route your tracking reference to human support.") is False
    assert (
        _remove_verified_tracking_identifier_request_sentences(
            "I will route this to human support. Please have the tracking reference ready."
        )
        == "I will route this to human support."
    )


def test_normalizes_tracking_reference_plus_safe_suffix_wording():
    summary = "Trusted tracking fact:\n- Tracking reference: parcel ending 129135\n- Current status: pending pickup"

    assert _normalize_tracking_safe_reference_wording(
        "Your parcel with tracking reference parcel ending 129135 is currently pending pickup.",
        tracking_fact_summary=summary,
    ) == "Your parcel ending 129135 is currently pending pickup."
    assert _normalize_tracking_safe_reference_wording(
        "Your parcel with tracking reference 'parcel ending 129135' is currently pending pickup.",
        tracking_fact_summary=summary,
    ) == "Your parcel ending 129135 is currently pending pickup."
    assert _normalize_tracking_safe_reference_wording(
        "Your parcel with the parcel ending 129135 is currently in the pending pickup stage.",
        tracking_fact_summary=summary,
    ) == "Your parcel ending 129135 is currently in the pending pickup stage."
    assert _normalize_tracking_safe_reference_wording(
        "Your parcel tracking reference is parcel ending 129135 and it is currently pending pickup.",
        tracking_fact_summary=summary,
    ) == "Your parcel ending 129135 and it is currently pending pickup."


def test_tracking_prompt_summary_localizes_safe_reference_for_chinese():
    summary = "\n".join(
        [
            "Trusted tracking fact:",
            "- Tracking reference: parcel ending 129135",
            "- Current status: in transit",
            "- Status meaning: in transit - Shipped to logistics network.",
        ]
    )

    english = _customer_tracking_fact_prompt_summary(summary, language_hint="en")
    chinese = _customer_tracking_fact_prompt_summary(summary, language_hint="zh")

    assert "Ref: parcel ending 129135" in english
    assert "Meaning: in transit - Shipped to logistics network." in english
    assert "尾号: 129135" in chinese
    assert "当前状态: in transit" in chinese
    assert "状态含义: in transit - Shipped to logistics network." in chinese
    assert "Ref:" not in chinese


def test_tracking_prompt_summary_localizes_safe_reference_for_german():
    summary = "\n".join(
        [
            "Trusted tracking fact:",
            "- Tracking reference: parcel ending 129026",
            "- Current status: in transit",
            "- Status meaning: in transit - Parcel is moving through the logistics network.",
        ]
    )

    german = _customer_tracking_fact_prompt_summary(summary, language_hint="de")

    assert "Sendung mit Endung: 129026" in german
    assert "Bedeutung: in transit - Parcel is moving through the logistics network." in german
    assert "Ref:" not in german


def test_request_language_hint_does_not_force_german_latin_text_to_english():
    request = _request(
        body="kannst du mal schauen, welche Zustand die Sendung CH020000129026 ist?",
        metadata={},
    )

    assert _request_language_hint(request) == "de"


def test_normalizes_ref_label_for_chinese_tracking_reply():
    summary = "Trusted tracking fact:\n- Tracking reference: parcel ending 129135\n- Current status: in transit"

    assert _normalize_tracking_safe_reference_wording(
        "Ref: 129135 - 在运输中，已发货至物流网络。",
        tracking_fact_summary=summary,
    ) == "尾号 129135 的包裹在运输中，已发货至物流网络。"


def test_compact_tracking_fact_summary_keeps_minimal_customer_relevant_facts():
    compact = _compact_tracking_fact_summary(
        "\n".join(
            [
                "Trusted tracking fact:",
                "- Source: speedaf_api.order_query",
                "- Checked at: unknown",
                "- Tracking reference: parcel ending 129135",
                "- Current status: pending pickup",
                "- PII redacted: true",
                "- Speedaf status code: 10",
                "- Status meaning: pending pickup - Order created and waiting for pickup.",
                "- Latest event: pending pickup",
                "- Recent events:",
                "  - pending pickup",
                "Rules:",
                "Do not reveal or repeat the full tracking number.",
            ]
        )
    )

    assert "parcel ending 129135" in compact
    assert "pending pickup" in compact
    assert "Speedaf status code: 10" in compact
    assert "Source:" not in compact
    assert "PII redacted" not in compact
    assert "Recent events" not in compact
    assert "Rules:" not in compact


@pytest.mark.asyncio
async def test_private_ai_runtime_service_policy_localizes_locked_fact_and_uses_service_budget(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_SERVICE", "112")
    adapter = PrivateAIRuntimeAdapter()
    calls = []

    def fake_post_json(endpoint, payload, token):
        calls.append(payload)
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "瑞士目前暂未开通本对本业务。",
                        "language": "zh",
                        "intent": "general_support",
                        "handoff_required": False,
                        "ticket_should_create": False,
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="瑞士本地到本地现在支持寄送吗？",
            metadata={
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "title": "Switzerland domestic-to-domestic service availability",
                            "answer": "Switzerland domestic-to-domestic service is currently unavailable. Switzerland domestic-to-domestic service availability 瑞士目前暂未开通本对本业务。",
                            "answer_mode": "direct_answer",
                            "source": {"citation": {"customer_visible": True}},
                        }
                    ],
                },
            },
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    assert calls[0]["options"]["num_predict"] == 112
    rendered = json.dumps(calls[0], ensure_ascii=False)
    assert "瑞士目前暂未开通本对本业务。" in rendered
    assert "service availability 瑞士目前" not in rendered
    assert result.structured_output["customer_reply"] == "瑞士目前暂未开通本对本业务。"
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_trusted_tracking_uses_service_generation_budget(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_SERVICE", "112")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_STANDARD", "240")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Your parcel is currently pending pickup.",
                        "language": "en",
                        "intent": "tracking",
                        "tracking_number": None,
                        "handoff_required": False,
                        "ticket_should_create": False,
                    }
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "standard"},
        ),
    )

    assert result.ok is True
    assert captured_payload["options"]["num_predict"] == 112
    assert result.raw_payload_safe_summary["ollama_options"]["num_predict"] == 112


@pytest.mark.asyncio
async def test_private_ai_runtime_trusted_tracking_explicit_handoff_prompt_acknowledges_route(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Your parcel is pending pickup, and I will route this to human support.",
                        "language": "en",
                        "intent": "tracking",
                        "tracking_number": None,
                        "handoff_required": True,
                        "handoff_reason": "customer_requested_human_review",
                        "ticket_should_create": False,
                    }
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="I need a human agent to help with parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "standard"},
        ),
    )

    prompt = captured_payload["messages"][1]["content"]
    assert result.ok is True
    assert "naturally acknowledge in customer_reply that the case will be routed to human support" in prompt
    assert "do not claim a named agent has accepted it" in prompt


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_identifier_request_after_verified_tracking(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    prompts: list[str] = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(payload["messages"][1]["content"])
        if len(prompts) == 1:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "customer_reply": "A human agent will be routed to assist you. Please have the tracking reference ready.",
                            "language": "en",
                            "intent": "tracking",
                            "tracking_number": None,
                            "handoff_required": True,
                            "ticket_should_create": False,
                        }
                    )
                }
            }
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Your parcel is pending pickup, and I will route this to human support.",
                        "language": "en",
                        "intent": "tracking",
                        "tracking_number": None,
                        "handoff_required": True,
                        "ticket_should_create": False,
                    }
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="I need a human agent to help with parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "standard"},
        ),
    )

    assert result.ok is True
    assert "tracking reference ready" not in result.structured_output["customer_reply"].lower()
    assert result.structured_output["customer_reply"] == "A human agent will be routed to assist you."
    assert len(prompts) == 1
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_trims_identifier_request_clause_after_verified_tracking(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    calls = []

    def fake_post_json(endpoint, payload, token):
        calls.append(payload)
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Your parcel is currently pending pickup, please keep your tracking number ready.",
                        "language": "en",
                        "intent": "tracking",
                        "tracking_number": None,
                        "handoff_required": False,
                        "ticket_should_create": False,
                    }
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "trusted_tracking_fact"},
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    assert result.structured_output["customer_reply"] == "Your parcel is currently pending pickup."
    assert "tracking number" not in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for key in [
        "APP_ENV",
        "PRIVATE_AI_RUNTIME_ENABLED",
        "PRIVATE_AI_RUNTIME_BASE_URL",
        "PRIVATE_AI_RUNTIME_TOKEN",
        "PRIVATE_AI_RUNTIME_TOKEN_FILE",
        "PRIVATE_AI_RUNTIME_DIRECT_PATH",
        "PRIVATE_AI_RUNTIME_RAG_PATH",
        "PRIVATE_AI_RUNTIME_CHAT_MODE",
        "PRIVATE_AI_RUNTIME_REQUEST_SHAPE",
        "PRIVATE_AI_RUNTIME_DIRECT_MODEL",
        "PRIVATE_AI_RUNTIME_RAG_MODEL",
        "PRIVATE_AI_RUNTIME_DIRECT_MODEL_POLICY",
        "PRIVATE_AI_RUNTIME_RAG_BASE_URL",
        "PRIVATE_AI_RUNTIME_ALLOW_SHARED_RAG_MODEL",
        "PRIVATE_AI_RUNTIME_TIMEOUT_SECONDS",
        "PRIVATE_AI_RUNTIME_MAX_PROMPT_CHARS",
        "PRIVATE_AI_RUNTIME_MAX_OUTPUT_CHARS",
        "PRIVATE_AI_RUNTIME_OLLAMA_KEEP_ALIVE",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_SHORT",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_SERVICE",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_STANDARD",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_REPAIR",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_CTX_SHORT",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_CTX_SERVICE",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_CTX_STANDARD",
        "PRIVATE_AI_RUNTIME_OLLAMA_NUM_CTX_REPAIR",
    ]:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_private_ai_runtime_success_normalizes_response_text(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint == "http://ai-runtime.internal:18081/api/chat"
        assert token == "test-token"
        return {
            "response_text": "Hello, how can I help you today?",
            "intent": "general_support",
            "handoff_required": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Hello support team"))

    assert result.ok is True
    assert result.provider == "private_ai_runtime"
    assert result.model == "qwen2.5:3b"
    assert result.structured_output["customer_reply"] == "Hello, how can I help you today?"
    assert result.structured_output["intent"] == "general_support"
    assert result.raw_payload_safe_summary["endpoint_path"] == "/api/chat"
    assert result.raw_payload_safe_summary["token_file_configured"] is True
    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert "must-not-leak" not in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_ollama_chat_uses_generation_budget_and_reports_timings(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_OLLAMA_KEEP_ALIVE", "15m")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_OLLAMA_NUM_PREDICT_SHORT", "144")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "Hello, how can I help?",
                        "language": "en",
                        "intent": "general_support",
                        "handoff_required": False,
                        "ticket_should_create": False,
                    }
                )
            },
            "total_duration": 1_230_000_000,
            "load_duration": 40_000_000,
            "prompt_eval_duration": 90_000_000,
            "eval_duration": 1_000_000_000,
            "prompt_eval_count": 180,
            "eval_count": 42,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="hello",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support"},
            },
        ),
    )

    assert result.ok is True
    assert captured_payload["keep_alive"] == "15m"
    assert captured_payload["options"] == {"temperature": 0.2, "top_p": 0.85, "num_predict": 144, "num_ctx": 1024}
    assert result.raw_payload_safe_summary["ollama_options"]["num_predict"] == 144
    assert result.raw_payload_safe_summary["ollama_options"]["num_ctx"] == 1024
    assert result.raw_payload_safe_summary["runtime_usage"] == {
        "total_duration_ms": 1230,
        "load_duration_ms": 40,
        "prompt_eval_duration_ms": 90,
        "eval_duration_ms": 1000,
        "prompt_eval_count": 180,
        "eval_count": 42,
    }


@pytest.mark.asyncio
async def test_private_ai_runtime_short_prompt_omits_request_id_and_uses_lower_default_budget(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "message": {
                "content": "Hello, how can I help?"
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            request_id="req-dynamic-cache-breaker",
            body="hello",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support"},
            },
        ),
    )

    assert result.ok is True
    assert captured_payload["options"]["num_predict"] == 24
    assert captured_payload["options"]["num_ctx"] == 1024
    assert "format" not in captured_payload
    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert "req-dynamic-cache-breaker" not in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_rag_mode_uses_rag_model(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_RAG_BASE_URL", "http://ai-runtime-rag.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")
    adapter = PrivateAIRuntimeAdapter()

    def fake_post_json(endpoint, payload, token):
        assert endpoint == "http://ai-runtime-rag.internal:18081/api/chat"
        assert payload["model"] == "qwen3:4b"
        return {
            "customer_reply": "Address changes must be verified by a support agent.",
            "language": "en",
            "intent": "address_change",
            "handoff_required": True,
            "handoff_reason": "manual verification required",
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Can you change my delivery address?"))

    assert result.ok is True
    assert result.model == "qwen3:4b"
    assert result.structured_output["handoff_required"] is True


@pytest.mark.asyncio
async def test_private_ai_runtime_production_rag_model_requires_isolated_runtime(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_CHAT_MODE", "rag")
    adapter = PrivateAIRuntimeAdapter()

    result = await adapter.generate(Mock(), _request(body="Can you change my delivery address?"))

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_rag_model_requires_isolated_runtime"


@pytest.mark.asyncio
async def test_private_ai_runtime_question_shape_matches_runtime_contract(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint == "http://ai-runtime.internal:18081/chat/direct"
        assert token == "test-token"
        return {
            "status": "ok",
            "answer": "Hello, how can I help you today?",
            "raw_content": "Hello, how can I help you today?",
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Hello support team"))

    assert result.ok is True
    assert captured_payload["model"] == "qwen2.5:3b"
    assert "question" in captured_payload
    assert "input" not in captured_payload
    assert "messages" not in captured_payload
    assert result.raw_payload_safe_summary["request_shape"] == "question"
    assert result.structured_output["customer_reply"] == "Hello, how can I help you today?"


@pytest.mark.asyncio
async def test_private_ai_runtime_direct_mode_keeps_fast_model_by_default_when_knowledge_is_present(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint == "http://ai-runtime.internal:18081/chat/direct"
        return {
            "customer_reply": "If the parcel shows delivered but was not received, I can help check the details and escalate when needed.",
            "language": "en",
            "intent": "complaint",
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="The parcel was delivered but not received.",
            metadata={
                "knowledge_context": {
                    "hits": [{"item_key": "nexus.support.customer.kb.shipment.exception.delivered.but.not.received"}],
                },
            },
        ),
    )

    assert result.ok is True
    assert captured_payload["model"] == "qwen2.5:3b"
    assert result.model == "qwen2.5:3b"
    assert result.raw_payload_safe_summary["endpoint_path"] == "/chat/direct"
    assert result.raw_payload_safe_summary["model_reason"] == "fixed_direct_model"


@pytest.mark.asyncio
async def test_private_ai_runtime_prompt_filters_internal_knowledge(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "customer_reply": "Switzerland domestic-to-domestic service is currently unavailable.",
            "language": "en",
            "intent": "general_support",
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Do you provide domestic to domestic delivery in Switzerland?",
            metadata={
                "knowledge_context": {
                    "hits": [
                        {
                            "item_key": "nexus.support.internal.sop",
                            "title": "Internal SOP",
                            "text": "Never expose this internal SOP.",
                            "metadata": {"citation": {"customer_visible": False}},
                        },
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "title": "Switzerland domestic-to-domestic service availability",
                            "text": "Switzerland domestic-to-domestic service is currently unavailable.",
                            "metadata": {"citation": {"customer_visible": True}},
                        },
                    ],
                },
            },
        ),
    )

    assert result.ok is True
    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert "Knowledge direct-answer task" in rendered
    assert "Never expose this internal SOP" not in rendered
    assert "Switzerland domestic-to-domestic service is currently unavailable" in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_locked_fact_conflict_with_runtime(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    calls = []

    def fake_post_json(endpoint, payload, token):
        calls.append(payload)
        if len(calls) == 1:
            return {
                "customer_reply": "Yes, we provide domestic-to-domestic delivery in Switzerland.",
                "language": "en",
                "intent": "general_support",
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Switzerland domestic-to-domestic service is currently unavailable.",
            "language": "en",
            "intent": "general_support",
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Do you provide domestic to domestic delivery in Switzerland?",
            metadata={
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                            "source": {"citation": {"customer_visible": True}},
                        }
                    ],
                },
            },
        ),
    )

    assert result.ok is True
    assert len(calls) == 2
    assert "service_or_policy" in json.dumps(calls[0], ensure_ascii=False)
    repair_payload = json.dumps(calls[1], ensure_ascii=False)
    assert "Locked-fact direct-answer task" in repair_payload
    assert "service_or_policy" in repair_payload
    assert "Do not ask for tracking" in repair_payload
    assert "Switzerland domestic-to-domestic service is currently unavailable." in repair_payload
    assert result.structured_output["customer_reply"] == "Switzerland domestic-to-domestic service is currently unavailable."
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "locked_fact_grounding_conflict"


@pytest.mark.asyncio
async def test_private_ai_runtime_short_general_support_keeps_low_latency_model(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "customer_reply": "Hi, how can I help?",
            "language": "en",
            "intent": "general_support",
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="hi",
            recent_context=[],
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support", "hits": []},
            },
        ),
    )

    assert result.ok is True
    assert captured_payload["model"] == "qwen2.5:3b"
    assert result.raw_payload_safe_summary["model_reason"] == "fixed_direct_model"


@pytest.mark.asyncio
async def test_private_ai_runtime_prompt_requires_customer_language(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {
            "status": "ok",
            "answer": json.dumps(
                {
                    "customer_reply": "您好，请问有什么可以帮您？",
                    "language": "zh",
                    "intent": "greeting",
                    "handoff_required": False,
                    "ticket_should_create": False,
                },
                ensure_ascii=False,
            ),
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="你好", channel_key="whatsapp"))

    assert result.ok is True
    assert result.structured_output["customer_reply"] == "您好，请问有什么可以帮您？"
    assert "Output Simplified Chinese only." in captured_payload["question"]
    assert "customer_reply must be Simplified Chinese" in captured_payload["question"]
    assert "Do not return JSON" in captured_payload["question"]


@pytest.mark.asyncio
async def test_private_ai_runtime_parses_json_embedded_in_answer(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()

    def fake_post_json(endpoint, payload, token):
        return {
            "status": "ok",
            "answer": json.dumps(
                {
                    "customer_reply": "Our support team is available Monday to Friday, 8 AM to 6 PM.",
                    "language": "en",
                    "intent": "general_support",
                    "handoff_required": False,
                    "ticket_should_create": False,
                }
            ),
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="What are your support hours?"))

    assert result.ok is True
    assert result.structured_output["customer_reply"] == "Our support team is available Monday to Friday, 8 AM to 6 PM."
    assert result.structured_output["reply"] == "Our support team is available Monday to Friday, 8 AM to 6 PM."
    assert result.structured_output["intent"] == "general_support"
    assert "customer_reply" not in result.structured_output["reply"]


@pytest.mark.asyncio
async def test_private_ai_runtime_production_rejects_inline_token(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN", "inline-token")

    result = await PrivateAIRuntimeAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_inline_token_forbidden"
    assert "inline-token" not in json.dumps(result.model_dump(), ensure_ascii=False)


@pytest.mark.asyncio
async def test_private_ai_runtime_rejects_known_endpoint_shape_mismatch(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")

    result = await PrivateAIRuntimeAdapter().generate(Mock(), _request())

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_direct_endpoint_request_shape_mismatch"


@pytest.mark.asyncio
async def test_private_ai_runtime_http_429_is_retryable(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()

    def fake_post_json(endpoint, payload, token):
        raise urllib.error.HTTPError(url=endpoint, code=429, msg="rate limited", hdrs=None, fp=None)

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Can you explain shipping services?"))

    assert result.ok is False
    assert result.error_code == "private_ai_runtime_http_429"
    assert result.retryable is True
    assert result.raw_payload_safe_summary["retryable_http"] is True


@pytest.mark.asyncio
async def test_private_ai_runtime_missing_tracking_number_goes_to_runtime(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        assert "Please help me track my parcel" in json.dumps(payload)
        return {
            "customer_reply": "I need the shipment reference before checking verified shipment details.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert calls == 1
    assert result.provider == "private_ai_runtime"
    assert result.reply_source == "private_ai_runtime"
    assert result.structured_output["intent"] == "tracking_missing_number"
    assert result.structured_output["tracking_number"] is None
    assert result.structured_output["handoff_required"] is False
    assert result.structured_output["customer_reply"] == (
        "I need the shipment reference before checking verified shipment details."
    )
    assert "runtime_path" not in result.raw_payload_safe_summary
    assert result.raw_payload_safe_summary["endpoint_path"] == "/api/chat"


@pytest.mark.asyncio
async def test_private_ai_runtime_allows_runtime_generated_tracking_prompt(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Share the parcel number when you are ready and I will check the latest tracking details.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert calls == 1
    assert result.structured_output["customer_reply"] == "Share the parcel number when you are ready and I will check the latest tracking details."
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_does_not_rewrite_runtime_generated_stock_like_tracking_prompt(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Please provide your tracking number so I can check the parcel status.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert len(prompts) == 1
    assert result.structured_output["customer_reply"] == "Please provide your tracking number so I can check the parcel status."
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_allows_runtime_generated_non_legacy_tracking_request(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Customer, could you please provide me with your shipment reference or order number so I can check the status for you?",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert len(prompts) == 1
    assert "shipment reference" in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_tracking_request_that_loses_identifier_ask(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "Hello! How can I assist you today? Is there a specific issue you need help with?",
                "language": "en",
                "intent": "general_support",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Share the parcel number when you are ready and I will check the latest tracking details.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert len(prompts) == 2
    assert "tracking_missing_identifier_request" in prompts[1]
    assert result.structured_output["intent"] == "tracking_missing_number"
    assert "parcel number" in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "tracking_missing_identifier_request"


@pytest.mark.asyncio
async def test_private_ai_runtime_allows_explicit_handoff_without_tracking_identifier_repair(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "I understand. I will connect this conversation to a human support agent.",
            "language": "en",
            "intent": "handoff",
            "tracking_number": None,
            "handoff_required": True,
            "handoff_reason": "customer_requested_human_agent",
            "recommended_agent_action": "Review the delayed parcel request with the customer.",
            "ticket_should_create": True,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="what should I do if my parcel is delayed and I want a human agent?",
            metadata={
                "latency_class": "explicit_handoff_request",
                "runtime_prompt_profile": "explicit_handoff_request",
                "knowledge_context": {"retrieval": "skipped_explicit_handoff_request", "hits": [], "locked_facts": []},
                "persona_context": {"name": "Speedaf Assistant"},
            },
        ),
    )

    assert result.ok is True
    assert len(prompts) == 1
    assert result.structured_output["intent"] == "handoff"
    assert result.structured_output["handoff_required"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_language_then_preserves_tracking_identifier_ask(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "请发送运单号，我来帮您查看。",
                "language": "zh",
                "intent": "tracking_missing_number",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        if len(prompts) == 2:
            return {
                "customer_reply": "Hello! How can I assist you today? Is there a specific service you need help with?",
                "language": "en",
                "intent": "general_support",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Send the parcel reference when you are ready and I will check the tracking details.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert len(prompts) == 3
    assert "language_mismatch" in prompts[1]
    assert "tracking_missing_identifier_request" in prompts[2]
    assert result.structured_output["intent"] == "tracking_missing_number"
    assert "parcel reference" in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "tracking_missing_identifier_request"
    assert result.raw_payload_safe_summary["output_contract_soft_accept_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_soft_accepts_safe_runtime_reply_after_identifier_repair_exhausted(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Hello! How can I assist you today? Is there a specific issue you need help with?",
            "language": "en",
            "intent": "general_support",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert calls == 2
    assert result.structured_output["customer_reply"].startswith("Hello!")
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "tracking_missing_identifier_request"
    assert result.raw_payload_safe_summary["output_contract_soft_accept_reason"] == "tracking_missing_identifier_request"


@pytest.mark.asyncio
async def test_private_ai_runtime_short_general_support_uses_light_prompt(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        return {
            "message": {"content": "Hello, how can I help you today?"},
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="nigh",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"hits": [{"title": "must-not-enter-short-prompt"}]},
            },
            recent_context=[{"role": "customer", "text": "older context should not be needed"}],
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Text only" in rendered
    assert "Do not ask for tracking, order, waybill, parcel, shipment, or reference numbers" in rendered
    assert "Return strict compact JSON only" not in rendered
    assert "Return compact JSON only" not in rendered
    assert "format" not in captured_payload
    assert captured_payload["options"]["num_predict"] == 24
    assert captured_payload["options"]["num_ctx"] == 1024
    assert "Language: en" in rendered
    assert "must-not-enter-short-prompt" not in rendered
    assert "older context should not be needed" not in rendered
    assert result.structured_output["customer_reply"] == "Hello, how can I help you today?"
    assert result.raw_payload_safe_summary["latency_class"] == "short_general_support"
    assert result.raw_payload_safe_summary["prompt_profile"] == "short_general_support"
    assert result.raw_payload_safe_summary["prompt_chars"] < 260


@pytest.mark.asyncio
async def test_private_ai_runtime_short_general_support_marks_non_tracking_numbers(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        return {"message": {"content": "Hello, how can I help you today?"}}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="nigh smoke 188200",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support", "hits": []},
            },
            recent_context=[],
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Non-identifier numbers from the original message were omitted." not in rendered
    assert "nigh smoke" in rendered
    assert "nigh smoke 188200" not in rendered
    assert "[number]" not in rendered
    assert "non_tracking_numbers_present" not in rendered
    assert "Return strict compact JSON only" not in rendered
    assert "format" not in captured_payload
    assert captured_payload["options"]["num_predict"] == 24
    assert captured_payload["options"]["num_ctx"] == 1024


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_internal_placeholder_leak(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = []

    def fake_post_json(endpoint, payload, token):
        calls.append(payload)
        if len(calls) == 1:
            return {"message": {"content": "Could you clarify what you need regarding [number]?"}}
        return {"message": {"content": "Could you clarify what you need help with?"}}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="nigh smoke 188200",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support", "hits": []},
            },
            recent_context=[],
        ),
    )

    assert result.ok is True
    assert len(calls) == 2
    assert result.structured_output["customer_reply"] == "Could you clarify what you need help with?"
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "internal_placeholder_leak"


@pytest.mark.asyncio
async def test_private_ai_runtime_explicit_handoff_request_uses_light_prompt(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        return {
            "message": {"content": "I understand. Human support will review this conversation."},
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="I need a human agent for this issue",
            metadata={
                "language": "en",
                "latency_class": "explicit_handoff_request",
                "runtime_prompt_profile": "explicit_handoff_request",
                "knowledge_context": {"hits": [{"title": "must-not-enter-handoff-prompt"}]},
            },
            recent_context=[{"role": "customer", "text": "older context should not be needed"}],
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Reply only with one brief customer-visible acknowledgement" in rendered
    assert "Return strict compact JSON only" not in rendered
    assert "format" not in captured_payload
    assert "Output English only." in rendered
    assert "must-not-enter-handoff-prompt" not in rendered
    assert "older context should not be needed" not in rendered
    assert captured_payload["options"]["num_predict"] <= 96
    assert result.structured_output["customer_reply"] == "I understand. Human support will review this conversation."
    assert result.raw_payload_safe_summary["latency_class"] == "explicit_handoff_request"
    assert result.raw_payload_safe_summary["prompt_profile"] == "explicit_handoff_request"
    assert result.raw_payload_safe_summary["prompt_chars"] < 360


@pytest.mark.asyncio
async def test_private_ai_runtime_short_general_support_accepts_runtime_greeting_alias_after_empty_reply(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        if calls == 1:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "customer_reply": "",
                            "language": "zh",
                            "intent": "general_support",
                            "tracking_number": None,
                            "handoff_required": False,
                            "ticket_should_create": False,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        return {
            "message": {
                "content": json.dumps(
                    {"greeting": "你好，请问有什么可以帮助你的吗？"},
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="你好",
            metadata={
                "language": "zh",
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
                "knowledge_context": {"retrieval": "skipped_short_general_support", "hits": []},
            },
        ),
    )

    assert result.ok is True
    assert calls == 2
    assert result.structured_output["customer_reply"] == "你好，请问有什么可以帮助你的吗？"
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "empty_reply"


@pytest.mark.asyncio
async def test_private_ai_runtime_short_general_support_repairs_echo_reply(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "nigh",
                "language": "en",
                "intent": "general_support",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Hello, how can I help you today?",
            "language": "en",
            "intent": "general_support",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="nigh",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
            },
        ),
    )

    assert result.ok is True
    assert len(prompts) == 2
    assert "echoed_customer_message" in prompts[1]
    assert result.structured_output["customer_reply"] == "Hello, how can I help you today?"
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "echoed_customer_message"


@pytest.mark.asyncio
async def test_private_ai_runtime_runtime_path_does_not_hide_tracking_identifier(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        return {
            "customer_reply": "I do not have trusted live tracking evidence for that waybill yet.",
            "language": "en",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Where is my parcel CH1200000011425?"))

    assert result.ok is True
    assert calls == 1
    assert result.raw_payload_safe_summary["endpoint_path"] == "/api/chat"
    assert "runtime_path" not in result.raw_payload_safe_summary


@pytest.mark.asyncio
async def test_private_ai_runtime_trusted_tracking_prompt_uses_safe_reference(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Your parcel ending 007813 is delivered. If the recipient cannot find it, please check with reception, household members, and the delivery contact point, then request human review if it is still missing.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="The recipient says they did not receive it.",
            recent_context=[{"role": "customer", "text": "Please check CH020000007813"}],
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered\n"
                "- Latest event: delivered | 2026-07-04\n"
                "Rules:\n"
                "Use only the trusted tracking fact above for parcel status.\n"
                "Do not reveal or repeat the full tracking number."
            ),
            tracking_fact_evidence_present=True,
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Trusted tracking answer" in rendered
    assert "Do not ask for the tracking/waybill/order number again" in rendered
    assert "latest_customer_message_overrides_recent_context" in rendered
    assert "same_as_latest_customer_message" in rendered
    assert "CH020000007813" not in rendered
    assert "parcel ending 007813" in rendered
    assert "Do not ask for the tracking" in rendered
    assert "Use only the trusted tracking fact above for parcel status" not in rendered
    assert result.raw_payload_safe_summary["prompt_chars"] < 1900


@pytest.mark.asyncio
async def test_private_ai_runtime_question_shape_trusted_tracking_accepts_plain_answer_without_repair(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    calls = []

    def fake_post_json(endpoint, payload, token):
        calls.append(payload)
        assert endpoint.endswith("/chat/direct")
        assert set(payload) == {"model", "question"}
        return {"answer": "Your parcel ending 129135 is currently pending pickup and is waiting for collection or the next Speedaf scan."}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup\n"
                "- Latest event: pending pickup | 2026-07-04"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "trusted_tracking_fact", "runtime_prompt_profile": "trusted_tracking_fact"},
        ),
    )

    assert result.ok is True
    assert len(calls) == 1
    assert "Trusted tracking answer" in calls[0]["question"]
    assert "Return strict compact JSON" not in calls[0]["question"]
    assert "No JSON or internal notes" in calls[0]["question"]
    assert result.structured_output["customer_reply"].startswith("Your parcel ending 129135")
    assert result.raw_payload_safe_summary["request_shape"] == "question"
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_ollama_trusted_tracking_uses_plain_reply_fast_path(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        assert "format" not in payload
        assert payload["options"]["num_predict"] == 64
        return {
            "message": {
                "content": "Your parcel ending 129135 is currently pending pickup and waiting for collection."
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup\n"
                "- Speedaf status code: 10\n"
                "- Status meaning: pending pickup - Order created and waiting for pickup.\n"
                "- Latest event: pending pickup"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "trusted_tracking_fact", "runtime_prompt_profile": "trusted_tracking_fact"},
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Trusted tracking answer. Text only" in rendered
    assert "include safe reference, status, and meaning" in rendered
    assert "Meaning: pending pickup" in rendered
    assert "Latest event" not in rendered
    assert "Speedaf status code" not in rendered
    assert "do not mention status codes" in rendered
    assert "Return strict compact JSON" not in rendered
    assert result.structured_output["customer_reply"] == "Your parcel ending 129135 is currently pending pickup and waiting for collection."
    assert result.raw_payload_safe_summary["ollama_options"]["num_predict"] == 64
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_ollama_knowledge_direct_answer_uses_plain_reply_fast_path(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        assert "format" not in payload
        assert payload["options"]["num_predict"] == 64
        return {
            "message": {
                "content": "Switzerland domestic-to-domestic service is currently unavailable."
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Do you provide domestic-to-domestic delivery in Switzerland?",
            metadata={
                "language": "en",
                "latency_class": "knowledge_direct_answer",
                "runtime_prompt_profile": "knowledge_direct_answer",
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "answer_mode": "direct_answer",
                            "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                            "customer_visible": True,
                        }
                    ]
                },
            },
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Knowledge direct-answer task" in rendered
    assert "Return strict compact JSON" not in rendered
    assert "Locked facts" in rendered
    assert result.structured_output["customer_reply"] == "Switzerland domestic-to-domestic service is currently unavailable."
    assert result.raw_payload_safe_summary["latency_class"] == "knowledge_direct_answer"
    assert result.raw_payload_safe_summary["prompt_profile"] == "knowledge_direct_answer"
    assert result.raw_payload_safe_summary["ollama_options"]["num_predict"] == 64
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_ollama_knowledge_direct_answer_does_not_require_service_intent(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        assert "format" not in payload
        assert payload["options"]["num_predict"] == 64
        return {"message": {"content": "生产知识闭环暗号是 canyon-lime。"}}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="请告诉我生产知识闭环暗号 mr98jqnv",
            metadata={
                "language": "zh",
                "latency_class": "knowledge_direct_answer",
                "runtime_prompt_profile": "knowledge_direct_answer",
                "knowledge_context": {
                    "hits": [
                        {
                            "item_key": "qa.production.answer",
                            "title": "生产知识闭环冒烟",
                            "answer_mode": "direct_answer",
                            "direct_answer": "生产知识闭环暗号是 canyon-lime。",
                            "customer_visible": True,
                        }
                    ],
                    "locked_facts": [
                        {
                            "item_key": "qa.production.answer",
                            "title": "生产知识闭环冒烟",
                            "answer_mode": "direct_answer",
                            "answer": "生产知识闭环暗号是 canyon-lime。",
                            "customer_visible": True,
                        }
                    ],
                },
            },
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Knowledge direct-answer task" in rendered
    assert "Locked facts" in rendered
    assert "canyon-lime" in rendered
    assert "Return strict compact JSON" not in rendered
    assert result.structured_output["customer_reply"] == "生产知识闭环暗号是 canyon-lime。"


@pytest.mark.asyncio
async def test_private_ai_runtime_plain_reply_recovers_malformed_customer_reply_json(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        assert "format" not in payload
        return {
            "message": {
                "content": '{"customer_reply":"生产知识闭环暗号是 canyon-lime。"',
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="请告诉我生产知识闭环暗号 mr98jqnv",
            metadata={
                "language": "zh",
                "latency_class": "knowledge_direct_answer",
                "runtime_prompt_profile": "knowledge_direct_answer",
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "qa.production.answer",
                            "title": "生产知识闭环冒烟",
                            "answer_mode": "direct_answer",
                            "answer": "生产知识闭环暗号是 canyon-lime。",
                            "customer_visible": True,
                        }
                    ],
                },
            },
        ),
    )

    assert result.ok is True
    assert calls == 1
    assert result.structured_output["customer_reply"] == "生产知识闭环暗号是 canyon-lime。"
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_unified_profile_uses_one_json_runtime_prompt(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        assert payload["format"] == "json"
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "生产知识闭环暗号是 canyon-lime。",
                        "language": "zh",
                        "intent": "general_support",
                        "tracking_number": None,
                        "handoff_required": False,
                        "ticket_should_create": False,
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="请告诉我生产知识闭环暗号 mr98jqnv",
            recent_context=[{"role": "customer", "text": "你好"}],
            metadata={
                "language": "zh",
                "latency_class": "unified_ai_runtime",
                "runtime_prompt_profile": "unified_ai_runtime",
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "qa.production.answer",
                            "title": "生产知识闭环冒烟",
                            "answer_mode": "direct_answer",
                            "answer": "生产知识闭环暗号是 canyon-lime。",
                            "customer_visible": True,
                        }
                    ],
                },
            },
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Unified customer support reply task" in rendered
    assert "生产知识闭环暗号是 canyon-lime。" in rendered
    assert "locked facts are authoritative" in rendered
    assert "do not ask what the customer's term means" in rendered
    assert "Knowledge direct-answer task" not in rendered
    assert "Short general-support reply" not in rendered
    assert result.structured_output["customer_reply"] == "生产知识闭环暗号是 canyon-lime。"
    assert result.raw_payload_safe_summary["latency_class"] == "unified_ai_runtime"
    assert result.raw_payload_safe_summary["prompt_profile"] == "unified_ai_runtime"
    assert result.raw_payload_safe_summary["prompt_chars"] < 2500
    assert result.raw_payload_safe_summary["ollama_options"]["num_predict"] == 64


def test_unified_prompt_knows_tracking_reference_was_already_supplied():
    adapter = PrivateAIRuntimeAdapter()
    prompt = adapter._build_prompt(
        _request(
            body="我上面已经提供过给你",
            recent_context=[{"role": "customer", "text": "我的运单号已经提供"}],
            metadata={
                "language": "zh",
                "latency_class": "unified_ai_runtime",
                "runtime_prompt_profile": "unified_ai_runtime",
                "conversation_state": {
                    "tracking_reference_present": True,
                    "safe_tracking_reference": "parcel ending 681375",
                },
                "tracking_fact_metadata": {
                    "tool_status": "failed",
                    "failure_reason": "tracking_lookup_no_match",
                },
                "knowledge_context": {},
            },
        ),
        model="nexus-gemma4-e4b:latest",
        mode="direct",
    )

    assert '"tracking_reference_present":true' in prompt
    assert "parcel ending 681375" in prompt
    assert "never ask again" in prompt
    assert "CH01026681375" not in prompt


@pytest.mark.asyncio
async def test_private_ai_runtime_unified_profile_repairs_traditional_chinese(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "ollama_chat")
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        if calls == 1:
            return {
                "message": {
                    "content": json.dumps(
                        {
                            "customer_reply": "您好，請問有什麼可以幫助您的？",
                            "language": "zh",
                            "intent": "general_support",
                            "tracking_number": None,
                            "handoff_required": False,
                            "ticket_should_create": False,
                        },
                        ensure_ascii=False,
                    )
                }
            }
        return {
            "message": {
                "content": json.dumps(
                    {
                        "customer_reply": "您好，请问有什么可以帮助您的？",
                        "language": "zh",
                        "intent": "general_support",
                        "tracking_number": None,
                        "handoff_required": False,
                        "ticket_should_create": False,
                    },
                    ensure_ascii=False,
                )
            }
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="你好",
            metadata={
                "latency_class": "unified_ai_runtime",
                "runtime_prompt_profile": "unified_ai_runtime",
                "knowledge_context": {},
            },
        ),
    )

    assert result.ok is True
    assert calls == 2
    assert result.structured_output["customer_reply"] == "您好，请问有什么可以帮助您的？"
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "language_mismatch"


def test_private_ai_runtime_non_plain_reply_recovers_malformed_customer_reply_json():
    request = _request(
        body="Where is my parcel?",
        metadata={"language": "en", "knowledge_context": {"hits": []}},
    )

    normalized = _normalize_runtime_output(
        {
            "message": {
                "content": '{"customer_reply":"Please check the parcel status.","language":"en","intent":"tracking"',
            }
        },
        request=request,
        max_output_chars=2000,
    )

    assert normalized["customer_reply"] == "Please check the parcel status."
    assert normalized["language"] == "en"
    assert normalized["intent"] == "tracking_missing_number"


def test_private_ai_runtime_non_plain_reply_rejects_unrecoverable_json_like_text():
    request = _request(
        body="Where is my parcel?",
        metadata={"language": "en", "knowledge_context": {"hits": []}},
    )

    with pytest.raises(ValueError, match="payload_text_json_invalid"):
        _normalize_runtime_output(
            {"message": {"content": '{"schema":"reply","content":'}},
            request=request,
            max_output_chars=2000,
        )


@pytest.mark.asyncio
async def test_private_ai_runtime_normalizes_tracking_suffix_misused_as_full_reference(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_DIRECT_PATH", "/chat/direct")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_REQUEST_SHAPE", "question")
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(payload["question"])
        assert endpoint.endswith("/chat/direct")
        return {"answer": "Your parcel tracking reference is 129135 and it is currently pending pickup."}

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup\n"
                "- Latest event: pending pickup | 2026-07-04"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "trusted_tracking_fact", "runtime_prompt_profile": "trusted_tracking_fact"},
        ),
    )

    assert result.ok is True
    assert len(prompts) == 1
    assert "Never present the suffix as the full tracking reference" in prompts[0]
    assert "English, say parcel ending 129135" not in prompts[0]
    assert "never write tracking reference is 129135" not in prompts[0].lower()
    assert result.structured_output["customer_reply"].startswith("Your parcel ending 129135")
    assert "tracking reference is 129135" not in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_tracking_ignores_short_general_profile(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "I could not find a verified result for that waybill yet. Please check whether the number is complete and correct.",
            "language": "en",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="CH020000129135",
            metadata={
                "latency_class": "short_general_support",
                "runtime_prompt_profile": "short_general_support",
            },
        ),
    )

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Tracking unresolved answer task" in rendered
    assert "Short general-support reply" not in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_trusted_tracking_ignores_unrelated_locked_facts(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="The recipient says they did not receive it. What should we do?",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered\n"
                "Rules:\n"
                "Use only the trusted tracking fact above for parcel status.\n"
                "Do not reveal or repeat the full tracking number."
            ),
            tracking_fact_evidence_present=True,
            metadata={
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                            "customer_visible": True,
                        }
                    ]
                }
            },
        ),
    )

    assert result.ok is True
    assert calls == 1
    assert "delivered" in result.structured_output["customer_reply"].lower()
    assert "domestic-to-domestic" not in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_delivered_not_received_guidance_for_pending_pickup(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "Your parcel is currently in the pending pickup stage. Please check with household, reception, or mailbox contact points if appropriate.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Your parcel ending 129135 is currently pending pickup. It has been created and is waiting for collection or the next Speedaf scan.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Current status: pending pickup\n"
                "- Latest event: pending pickup | 2026-07-04"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en"},
        ),
    )

    assert result.ok is True
    assert len(prompts) == 2
    assert "Trusted tracking status-grounding repair task" in prompts[1]
    reply = result.structured_output["customer_reply"].lower()
    assert "pending pickup" in reply
    assert "household" not in reply
    assert "reception" not in reply
    assert "mailbox" not in reply
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "tracking_fact_status_guidance_mismatch"


@pytest.mark.asyncio
async def test_private_ai_runtime_trusted_tracking_repairs_language_mismatch(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        assert payload["model"] == "qwen2.5:3b"
        if len(prompts) == 1:
            return {
                "customer_reply": "Your parcel ending 007813 已签收.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception, household members, or the delivery contact point, then ask us for human review.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Please check CH020000007813",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered"
            ),
            tracking_fact_evidence_present=True,
            metadata={
                "language": "en",
                "knowledge_context": {
                    "locked_facts": [
                        {
                            "item_key": "nexus.support.customer.kb.ch.service.availability",
                            "answer": "Switzerland domestic-to-domestic service is currently unavailable.",
                            "customer_visible": True,
                        }
                    ]
                },
            },
        ),
    )

    assert result.ok is True
    assert len(prompts) == 2
    assert "Trusted tracking answer" in prompts[0]
    assert "Trusted tracking language repair task" in prompts[1]
    assert "domestic-to-domestic" not in prompts[0]
    assert result.structured_output["customer_reply"].isascii()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "language_mismatch"
    assert result.raw_payload_safe_summary["output_contract_soft_accept_reason"] is None


@pytest.mark.asyncio
async def test_private_ai_runtime_sanitizes_translated_cjk_evidence_label(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        return {
            "answer": "Your package has been delivered to the 一级网点 (primary branch). If you cannot find it, please check with household or reception contacts.",
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Please check CH020000007813",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered\n"
                "- Latest event: delivered | 一级网点"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en"},
        ),
    )

    assert result.ok is True
    assert calls == 1
    assert result.structured_output["customer_reply"] == (
        "Your package has been delivered to the primary branch. "
        "If you cannot find it, please check with household or reception contacts."
    )
    assert result.structured_output["customer_reply"].isascii()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is False


@pytest.mark.asyncio
async def test_private_ai_runtime_rejects_trusted_tracking_language_after_failed_repair(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "Your parcel ending 007813 已签收.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Please check CH020000007813",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en"},
        ),
    )

    assert result.ok is False
    assert calls == 2
    assert result.error_code == "private_ai_runtime_language_mismatch"


@pytest.mark.asyncio
async def test_private_ai_runtime_rejects_trusted_tracking_language_when_repair_fails(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        if calls == 1:
            return {
                "customer_reply": "Your parcel ending 007813 已签收.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        raise ValueError("bad repair payload")

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Please check CH020000007813",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en"},
        ),
    )

    assert result.ok is False
    assert calls == 2
    assert result.error_code == "private_ai_runtime_contract_repair_failed"


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_nested_json_customer_reply(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    calls = 0

    def fake_post_json(endpoint, payload, token):
        nonlocal calls
        calls += 1
        assert endpoint.endswith("/api/chat")
        if calls == 1:
            return {
                "customer_reply": '{"customer_reply":"Your parcel has been delivered"',
                "language": "en",
                "intent": "tracking",
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Your parcel ending 007813 has been delivered. If the recipient cannot find it, please check with reception or the delivery contact point, then ask us for human review.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="Please check CH020000007813",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 007813\n"
                "- Current status: delivered"
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en"},
        ),
    )

    assert result.ok is True
    assert calls == 2
    assert result.structured_output["customer_reply"].startswith("Your parcel ending 007813")
    assert "customer_reply" not in result.structured_output["customer_reply"]
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "bad_json"


@pytest.mark.asyncio
async def test_private_ai_runtime_tracking_unresolved_prompt_is_customer_facing(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    captured_payload = {}

    def fake_post_json(endpoint, payload, token):
        captured_payload.update(payload)
        assert endpoint.endswith("/api/chat")
        return {
            "customer_reply": "I could not find a verified result for the waybill you provided yet. Please check whether the number is complete and correct; if it still cannot be found, I can help pass this to a human agent.",
            "language": "en",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please check CH020000129135 now"))

    rendered = json.dumps(captured_payload, ensure_ascii=False)
    assert result.ok is True
    assert "Tracking unresolved answer task" in rendered
    assert "CH020000129135" not in rendered
    assert "parcel ending 129135" in rendered
    assert "Do not ask how to query it" in rendered
    assert "请提醒客户" not in rendered
    assert "不得判断" not in rendered


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_tracking_unresolved_bad_clarification(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "I can check the status for you. Could you please provide the tracking number?",
                "language": "en",
                "intent": "tracking_unresolved",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        if len(prompts) == 2:
            return {
                "customer_reply": "How may I help you today?",
                "language": "en",
                "intent": "other",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Please confirm that the tracking number you provided is complete and correct.",
            "language": "en",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please check tracking number CH020000129135"))

    assert result.ok is True
    assert len(prompts) == 3
    assert "Tracking unresolved wording repair task" in prompts[1]
    assert "Tracking unresolved wording repair task" in prompts[2]
    assert result.structured_output["customer_reply"] == "Please confirm that the tracking number you provided is complete and correct."
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "tracking_unresolved_bad_clarification"


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_live_tracking_claim_without_evidence(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "Your parcel has been delivered today.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": "CH1200000011425",
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "I do not have verified live tracking evidence for the waybill you provided yet. Please check whether the number is complete and correct.",
            "language": "en",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Where is my parcel CH1200000011425?"))

    assert result.ok is True
    assert len(prompts) == 2
    assert "shipment_status_without_evidence" in prompts[1]
    assert result.structured_output["customer_reply"].startswith("I do not have verified live tracking evidence")
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "shipment_status_without_evidence"


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_internal_instruction_leak(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "Could you please provide more details on what needs repair regarding the contract?",
                "language": "en",
                "intent": "tracking_missing_number",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "I can help check the parcel once I have the waybill number.",
            "language": "en",
            "intent": "tracking_missing_number",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="Please help me track my parcel. I will provide the tracking number."))

    assert result.ok is True
    assert len(prompts) == 2
    assert "internal_instruction_leak" in prompts[1]
    assert "contract" not in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "internal_instruction_leak"


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_chinese_internal_tracking_instruction(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "在没有可信的追踪证据之前，不要尝试提供任何包裹状态、预计送达时间、快递位置、退款状态或海关状态",
                "language": "zh",
                "intent": "tracking_unresolved",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "暂时没有查到这个单号的可信结果，请确认号码是否完整且正确。",
            "language": "zh",
            "intent": "tracking_unresolved",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(Mock(), _request(body="请帮我查询 CH020000129135 到哪里了"))

    assert result.ok is True
    assert len(prompts) == 2
    assert "internal_instruction_leak" in prompts[1]
    assert "tracking_reference_present" in prompts[1]
    assert result.structured_output["customer_reply"] == "暂时没有查到这个单号的可信结果，请确认号码是否完整且正确。"
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "internal_instruction_leak"


@pytest.mark.asyncio
async def test_private_ai_runtime_repairs_unsupported_proactive_update_promise(monkeypatch, tmp_path):
    token_file = tmp_path / "ai-runtime-token"
    token_file.write_text("test-token", encoding="utf-8")
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_TOKEN_FILE", str(token_file))
    adapter = PrivateAIRuntimeAdapter()
    prompts = []

    def fake_post_json(endpoint, payload, token):
        prompts.append(json.dumps(payload, ensure_ascii=False))
        assert endpoint.endswith("/api/chat")
        if len(prompts) == 1:
            return {
                "customer_reply": "Your parcel ending 129135 is currently pending pickup. I'll let you know if there are any updates.",
                "language": "en",
                "intent": "tracking",
                "tracking_number": None,
                "handoff_required": False,
                "ticket_should_create": False,
            }
        return {
            "customer_reply": "Your parcel ending 129135 is currently pending pickup.",
            "language": "en",
            "intent": "tracking",
            "tracking_number": None,
            "handoff_required": False,
            "ticket_should_create": False,
        }

    monkeypatch.setattr(adapter, "_post_json", fake_post_json)

    result = await adapter.generate(
        Mock(),
        _request(
            body="can you please check my parcel CH020000129135",
            tracking_fact_summary=(
                "Trusted tracking fact:\n"
                "- Tracking reference: parcel ending 129135\n"
                "- Speedaf status code: 10\n"
                "- Current status: pending pickup\n"
                "- Status description: Order created and waiting for pickup."
            ),
            tracking_fact_evidence_present=True,
            metadata={"language": "en", "latency_class": "trusted_tracking_fact"},
        ),
    )

    assert result.ok is True
    assert len(prompts) == 2
    assert "Unsupported proactive-update promise repair task" in prompts[1]
    assert "let you know" not in result.structured_output["customer_reply"].lower()
    assert "notify" not in result.structured_output["customer_reply"].lower()
    assert result.raw_payload_safe_summary["output_contract_repair_applied"] is True
    assert result.raw_payload_safe_summary["output_contract_repair_reason"] == "unsupported_proactive_update_promise"
