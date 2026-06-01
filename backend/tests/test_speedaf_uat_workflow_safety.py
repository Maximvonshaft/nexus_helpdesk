from pathlib import Path


PROJECT = Path(__file__).resolve().parents[2]


def _workflow(name: str) -> str:
    return (PROJECT / ".github" / "workflows" / name).read_text(encoding="utf-8")


def _script(name: str) -> str:
    return (PROJECT / "scripts" / name).read_text(encoding="utf-8")


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


def test_knowledge_runtime_readiness_does_not_inline_sensitive_dispatch_inputs():
    workflow = _workflow("knowledge-runtime-readiness.yml")

    assert "SPEEDAF_MCP_TEST_WAYBILL_CODE: ${{ secrets.SPEEDAF_UAT_TEST_WAYBILL_CODE }}" in workflow
    assert "SPEEDAF_MCP_TEST_CALLER_ID: ${{ secrets.SPEEDAF_UAT_TEST_CALLER_ID }}" in workflow
    assert "configure SPEEDAF_UAT_TEST_WAYBILL_CODE and SPEEDAF_UAT_TEST_CALLER_ID as GitHub Secrets" in workflow
    assert "Assert readiness log is sanitized" in workflow
    assert "inputs.waybill_code" not in workflow
    assert "inputs.caller_id" not in workflow
    assert "CH020000006856" not in workflow


def test_knowledge_runtime_readiness_probe_requires_secret_samples():
    script = _script("nexus_knowledge_runtime_v2_readiness_probe.sh")

    assert 'WAYBILL="${SPEEDAF_MCP_TEST_WAYBILL_CODE:-}"' in script
    assert "SPEEDAF_MCP_TEST_WAYBILL_CODE\") or" not in script
    assert "set SPEEDAF_MCP_TEST_WAYBILL_CODE and SPEEDAF_MCP_TEST_CALLER_ID from GitHub Secrets" in script
    assert 'waybill=os.environ["SPEEDAF_MCP_TEST_WAYBILL_CODE"]' in script
