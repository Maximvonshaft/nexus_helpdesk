from __future__ import annotations

from app.services.tracking_fact_redactor import normalize_tracking_fact


def test_no_info_result_is_trusted_business_fact():
    fact = normalize_tracking_fact(
        {
            "ok": False,
            "tracking_number": "BERN1003",
            "status": "No Info",
            "message": "No tracking updates yet.",
        },
        tracking_number="BERN1003",
    )

    assert fact.ok is False
    assert fact.fact_evidence_present is True
    assert fact.pii_redacted is True
    assert fact.tool_status == "no_info"
    assert fact.failure_reason is None
    assert "No Info" in fact.prompt_summary()


def test_chinese_no_trace_result_is_trusted_business_fact():
    fact = normalize_tracking_fact(
        {
            "ok": False,
            "tracking_number": "BERN1003",
            "status": "暂无轨迹",
            "message": "您的包裹目前暂无轨迹更新，请稍后再试或联系客服。",
        },
        tracking_number="BERN1003",
    )

    assert fact.fact_evidence_present is True
    assert fact.failure_reason is None
    assert "暂无轨迹" in fact.prompt_summary()
