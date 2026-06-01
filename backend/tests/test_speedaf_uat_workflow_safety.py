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


def test_speedaf_legacy_uat_smoke_uses_secret_samples_only():
    workflow = _workflow("speedaf-uat-smoke.yml")

    assert "SPEEDAF_MCP_APP_CODE: ${{ secrets.SPEEDAF_UAT_MCP_APP_CODE }}" in workflow
    assert "SPEEDAF_MCP_SECRET_KEY: ${{ secrets.SPEEDAF_UAT_MCP_SECRET_KEY }}" in workflow
    assert "SPEEDAF_MCP_TEST_CALLER_ID: ${{ secrets.SPEEDAF_UAT_TEST_CALLER_ID }}" in workflow
    assert "SPEEDAF_MCP_TEST_WAYBILL_CODE: ${{ secrets.SPEEDAF_UAT_TEST_WAYBILL_CODE }}" in workflow
    assert "vars.SPEEDAF_UAT_TEST_CALLER_ID" not in workflow
    assert "vars.SPEEDAF_UAT_TEST_WAYBILL_CODE" not in workflow
    assert "secrets.SPEEDAF_UAT_APP_CODE" not in workflow
    assert "secrets.SPEEDAF_UAT_SECRET_KEY" not in workflow
    assert "variables SPEEDAF_UAT_TEST_CALLER_ID" not in workflow


def test_speedaf_contract_gate_treats_voice_callback_as_write_surface():
    workflow = _workflow("speedaf-contract-gate.yml")

    assert "WORK_ORDER_CREATE|VOICE_CALLBACK" in workflow
    assert "'SPEEDAF_VOICE_CALLBACK_ENABLED: true'" in workflow
    assert "SPEEDAF_VOICE_CALLBACK_ENABLED" in workflow


def test_production_readiness_blocks_speedaf_voice_callback_in_smoke_workflows():
    workflow = _workflow("production-readiness.yml")

    assert "'SPEEDAF_VOICE_CALLBACK_ENABLED: true'" in workflow
    assert "'/open-api/mcp/callData/voice/callBack'" in workflow


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
    assert "readiness log contains waybill code" in workflow
    assert 'grep -F "$SPEEDAF_MCP_TEST_WAYBILL_CODE" "$REPORT"' in workflow
    assert "inputs.waybill_code" not in workflow
    assert "inputs.caller_id" not in workflow
    assert "CH020000006856" not in workflow


def test_knowledge_runtime_readiness_probe_requires_secret_samples():
    script = _script("nexus_knowledge_runtime_v2_readiness_probe.sh")

    assert 'WAYBILL="${SPEEDAF_MCP_TEST_WAYBILL_CODE:-}"' in script
    assert "SPEEDAF_MCP_TEST_WAYBILL_CODE\") or" not in script
    assert "set SPEEDAF_MCP_TEST_WAYBILL_CODE and SPEEDAF_MCP_TEST_CALLER_ID from GitHub Secrets" in script
    assert 'waybill=os.environ["SPEEDAF_MCP_TEST_WAYBILL_CODE"]' in script
    assert "def safe_payload():" in script
    assert 'text=text.replace(secret, "[REDACTED]")' in script
    assert 'assert result.ok and result.fact_evidence_present and result.tool_status == "success", "speedaf_direct_lookup_failed"' in script
