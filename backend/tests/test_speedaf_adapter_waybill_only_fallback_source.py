from pathlib import Path


def test_speedaf_adapter_has_configured_waybill_only_public_status_fallback():
    src = Path("backend/app/services/speedaf/adapter.py").read_text(encoding="utf-8")

    assert "WAYBILL_ONLY_PUBLIC_STATUS_CALLER_FALLBACK_BEGIN" in src
    assert "SPEEDAF_WAYBILL_ONLY_LOOKUP_CALLER_ID" in src
    assert "SPEEDAF_UAT_CALLER_ID" in src
    assert "SPEEDAF_MCP_TEST_CALLER_ID" in src
    assert "if waybill_code and not caller_id:" in src
