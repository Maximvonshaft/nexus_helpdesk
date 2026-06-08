from __future__ import annotations

from app.services.provider_runtime.adapters import codex_direct_prompt_budget_patch as _patch  # noqa: F401
from app.services.provider_runtime.adapters.codex_direct import CodexDirectAdapter
from app.services.provider_runtime.schemas import ProviderRequest


def test_format_invalid_tracking_no_evidence_uses_compact_prompt_budget():
    request = ProviderRequest(
        request_id="req-format-invalid",
        tenant_id="default",
        tenant_key="default",
        channel_key="website",
        session_id="sess-format-invalid",
        scenario="webchat_fast_reply",
        body="CH1200000011425",
        recent_context=[],
        tracking_fact_summary=None,
        tracking_fact_evidence_present=False,
        output_contract="speedaf_webchat_fast_reply_v1",
        timeout_ms=5000,
        metadata={
            "context_version": "nexus_webchat_runtime_context_v2",
            "tracking_fact_metadata": {
                "fact_evidence_present": False,
                "tool_status": "format_invalid",
                "failure_reason": "invalid_ch_waybill_format",
                "tracking_number_hash": "sha256:test",
                "tracking_number_suffix": "011425",
            },
            "knowledge_context": {
                "retrieval_query": "CH1200000011425 运单号格式 wrong tracking number",
                "query_expansion_terms": ["运单号格式", "wrong tracking number"],
                "hits": [
                    {
                        "item_key": "ch.waybill.format",
                        "title": "瑞士 Speedaf 运单号格式与输错提醒",
                        "score_breakdown": {"semantic": 100, "keyword": 42},
                        "matched_terms": ["CH1200000011425", "运单号格式", "wrong", "tracking", "number"] * 20,
                        "text": "Question: 客户输入瑞士 Speedaf 运单号查不到怎么办？ Answer: 请客户核对 CH 开头后接 12 位数字的完整运单号。",
                        "metadata": {
                            "knowledge_kind": "business_fact",
                            "fact_status": "approved",
                            "answer_mode": "guided_answer",
                        },
                        "source_metadata": {
                            "knowledge_kind": "business_fact",
                            "fact_status": "approved",
                            "answer_mode": "guided_answer",
                            "published_version": 7,
                            "large_internal_blob": "x" * 2000,
                        },
                    }
                ],
                "locked_facts": [],
                "evidence_pack": [{"item_key": "ch.waybill.format", "published_version": 1, "duplicate": "x" * 1000}],
                "injected_knowledge": [{"item_key": "ch.waybill.format", "duplicate": "x" * 1000}],
                "fallback_ngrams": ["x" * 50] * 50,
            },
        },
    )

    prompt = CodexDirectAdapter()._build_prompt(request)

    assert "tracking_no_evidence_compact" in prompt
    assert "format_invalid" in prompt
    assert "invalid_ch_waybill_format" in prompt
    assert "ch.waybill.format" in prompt
    assert "score_breakdown" not in prompt
    assert "fallback_ngrams" not in prompt
    assert "matched_terms" not in prompt
    assert "evidence_pack" not in prompt
    assert "injected_knowledge" not in prompt
    assert "large_internal_blob" not in prompt
    assert len(prompt) < 5500
