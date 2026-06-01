from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> str:
    return (PROJECT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_speedaf_readonly_uat_probe_does_not_inline_sensitive_dispatch_inputs():
    workflow = _workflow("speedaf-readonly-uat-probe.yml")

    assert "--waybill-code \"$SPEEDAF_READONLY_UAT_WAYBILL_CODE\"" in workflow
    assert "--caller-id \"$SPEEDAF_READONLY_UAT_CALLER_ID\"" in workflow
    assert "SPEEDAF_READONLY_UAT_WAYBILL_CODE: ${{ secrets.SPEEDAF_UAT_TEST_WAYBILL_CODE }}" in workflow
    assert "SPEEDAF_READONLY_UAT_CALLER_ID: ${{ secrets.SPEEDAF_UAT_TEST_CALLER_ID }}" in workflow
    assert "Assert readonly report is sanitized" in workflow
    assert "inputs.waybill_code" not in workflow
    assert "inputs.caller_id" not in workflow


def test_speedaf_full_uat_probe_does_not_inline_sensitive_dispatch_inputs():
    workflow = _workflow("speedaf-full-uat-probe.yml")

    assert "--waybill-code \"$SPEEDAF_FULL_UAT_WAYBILL_CODE\"" in workflow
    assert "--caller-id \"$SPEEDAF_FULL_UAT_CALLER_ID\"" in workflow
    assert "--whatsapp-phone \"$SPEEDAF_FULL_UAT_WHATSAPP_PHONE\"" in workflow
    assert "SPEEDAF_FULL_UAT_WAYBILL_CODE: ${{ secrets.SPEEDAF_UAT_TEST_WAYBILL_CODE }}" in workflow
    assert "SPEEDAF_FULL_UAT_CALLER_ID: ${{ secrets.SPEEDAF_UAT_TEST_CALLER_ID }}" in workflow
    assert "SPEEDAF_FULL_UAT_WHATSAPP_PHONE: ${{ secrets.SPEEDAF_UAT_TEST_WHATSAPP_PHONE }}" in workflow
    assert "Assert full report is sanitized" in workflow
    assert "inputs.waybill_code" not in workflow
    assert "inputs.caller_id" not in workflow
    assert "inputs.whatsapp_phone" not in workflow
