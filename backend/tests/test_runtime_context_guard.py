from __future__ import annotations

from types import SimpleNamespace

from app.services.ai_runtime_context import (
    build_runtime_context_guard,
    build_structured_recent_context,
)

# Regression coverage for M1 Runtime Context Guard source/factuality policies.


def _row(row_id: int, direction: str, text: str):
    return SimpleNamespace(id=row_id, direction=direction, body=text, body_text=None)


def test_previous_ai_reply_marked_not_evidence():
    context = build_structured_recent_context(history_rows=[_row(124, "agent", "Your parcel was delivered yesterday.")])

    assert context == [
        {
            "role": "ai",
            "text": "Your parcel was delivered yesterday.",
            "source": "previous_ai_reply",
            "message_id": 124,
            "factuality": "not_evidence",
            "use": "coherence_only",
        }
    ]


def test_customer_message_marked_claim_not_verified_fact():
    context = build_structured_recent_context(history_rows=[_row(123, "visitor", "The courier said it arrived.")])

    assert context[0]["role"] == "customer"
    assert context[0]["source"] == "webchat_message"
    assert context[0]["factuality"] == "customer_claim"
    assert context[0]["use"] == "conversation_context"


def test_tracking_intent_without_tool_fact_blocks_live_answer():
    guard = build_runtime_context_guard(
        structured_recent_context=[],
        tracking_intent_detected=True,
        tracking_fact_evidence_present=False,
        kb_hits_count=3,
    )

    assert guard["answer_policy"]["live_tracking_answer_allowed"] is False
    assert guard["answer_policy"]["allowed_reply_types"] == ["clarifying_question", "handoff_notice", "null_reply"]
    assert "Do not answer live parcel status from KB." in guard["answer_policy"]["forbidden"]


def test_tracking_intent_with_tool_fact_allows_live_answer():
    guard = build_runtime_context_guard(
        structured_recent_context=[],
        tracking_intent_detected=True,
        tracking_fact_evidence_present=True,
        kb_hits_count=0,
    )

    assert guard["answer_policy"]["live_tracking_answer_allowed"] is True
    assert guard["answer_policy"]["required_sources"] == ["tracking_tool"]


def test_runtime_context_declares_support_memory_ledger_not_used():
    guard = build_runtime_context_guard(
        structured_recent_context=[],
        tracking_intent_detected=False,
        tracking_fact_evidence_present=False,
        kb_hits_count=0,
    )

    assert guard["evidence_contract"]["memory_items_count"] == 0
    assert guard["evidence_contract"]["memory_system"] == "not_enabled"
    assert guard["evidence_contract"]["support_memory_ledger_used_by_runtime"] is False
    assert guard["runtime_trace_context_fields"]["support_memory_ledger_used_by_runtime"] is False


def test_recent_context_backward_compat_preserved():
    context = build_structured_recent_context(history_rows=[_row(1, "visitor", "Hello"), _row(2, "agent", "Hi")])

    assert all("role" in item and "text" in item for item in context)
    assert [item["role"] for item in context] == ["customer", "ai"]


def test_no_raw_tracking_number_in_structured_recent_context():
    context = build_structured_recent_context(
        history_rows=[_row(1, "visitor", "Please check CH020000129135 for me")],
    )

    assert "CH020000129135" not in context[0]["text"]
    assert "[redacted_tracking_reference]" in context[0]["text"]


def test_context_policy_present_in_runtime_context():
    guard = build_runtime_context_guard(
        structured_recent_context=[],
        tracking_intent_detected=False,
        tracking_fact_evidence_present=False,
        kb_hits_count=0,
    )

    policy = guard["context_policy"]
    assert policy["previous_ai_replies_are_not_facts"] is True
    assert policy["customer_messages_are_claims_not_verified_facts"] is True
    assert policy["tracking_status_requires_tool_fact"] is True
    assert policy["kb_cannot_answer_live_tracking_status"] is True
    assert policy["tool_result_overrides_kb"] is True
    assert policy["ask_clarifying_question_when_intent_unclear"] is True


def test_evidence_contract_counts_prior_ai_and_customer_claims():
    structured = build_structured_recent_context(
        history_rows=[
            _row(1, "visitor", "Where is my parcel?"),
            _row(2, "agent", "It may be out for delivery."),
            _row(3, "visitor", "I still did not receive it."),
        ],
    )
    guard = build_runtime_context_guard(
        structured_recent_context=structured,
        tracking_intent_detected=True,
        tracking_fact_evidence_present=False,
        kb_hits_count=2,
    )

    evidence = guard["evidence_contract"]
    assert evidence["recent_context_count"] == 3
    assert evidence["prior_ai_messages_count"] == 1
    assert evidence["customer_claim_count"] == 2
    assert evidence["kb_hits_count"] == 2
    assert guard["runtime_trace_context_fields"]["prior_ai_messages_count"] == 1
    assert guard["runtime_trace_context_fields"]["customer_claim_count"] == 2
