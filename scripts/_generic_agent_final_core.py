from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def function_bounds(text: str, name: str) -> tuple[int, int]:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(r"^def [A-Za-z0-9_]+\(", text[match.end():], flags=re.MULTILINE)
    end = len(text) if next_match is None else match.end() + next_match.start()
    return match.start(), end


def replace_function(text: str, name: str, replacement: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + replacement.strip() + "\n\n\n" + text[end:].lstrip("\n")


def remove_function(text: str, name: str) -> str:
    start, end = function_bounds(text, name)
    return text[:start].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


# ---------------------------------------------------------------------------
# 1. Canonical Tool execution: stable Ticket-creation idempotency scope.
# ---------------------------------------------------------------------------
core_path = "backend/app/services/nexus_osr/tool_execution_service_core.py"
core = read(core_path)
existing_start, existing_end = function_bounds(core, "_existing_executed_log")
existing = core[existing_start:existing_end]
existing = replace_once(
    existing,
    "    if conversation is not None:\n"
    "        query = query.filter(\n"
    "            ToolCallLog.webchat_conversation_id == conversation.id\n"
    "        )\n"
    "    if ticket is not None:\n",
    "    if conversation is not None:\n"
    "        # Conversation remains the stable idempotency scope when ticket.create\n"
    "        # transitions the case from ticketless to ticket-backed.\n"
    "        query = query.filter(\n"
    "            ToolCallLog.webchat_conversation_id == conversation.id\n"
    "        )\n"
    "    elif ticket is not None:\n",
    label="stable conversation idempotency scope",
)
core = core[:existing_start] + existing + core[existing_end:]
write(core_path, core)


# ---------------------------------------------------------------------------
# 2. Canonical Tool contracts and fail-closed generic governance.
# ---------------------------------------------------------------------------
registry_path = "backend/app/services/webchat_ai_decision_runtime/tool_registry.py"
registry = read(registry_path)
for tool_name in (
    "speedaf.order.query",
    "speedaf.express.track.query",
    "speedaf.order.waybillCode.query",
):
    pattern = re.compile(
        rf'(\"{re.escape(tool_name)}\": ToolContract\([\s\S]*?risk_level=\"medium\",\n)(?!\s*allowed_auto_execution_mode=)',
        flags=re.MULTILINE,
    )
    registry, count = pattern.subn(
        r'\1        allowed_auto_execution_mode="policy_gated",\n',
        registry,
        count=1,
    )
    if count != 1:
        raise SystemExit(f"policy-gated read contract not found: {tool_name}")
write(registry_path, registry)


governance_path = "backend/app/services/tool_governance.py"
governance = read(governance_path)
if "from .webchat_ai_decision_runtime.tool_registry import get_tool_contract" not in governance:
    governance = governance.replace(
        "from .observability import record_tool_call_metric\n",
        "from .observability import record_tool_call_metric\n"
        "from .webchat_ai_decision_runtime.tool_registry import get_tool_contract\n",
        1,
    )

governance = replace_function(
    governance,
    "classify_tool_type",
    '''def classify_tool_type(tool_name: str) -> str:
    normalized = (tool_name or "").strip()
    contract = get_tool_contract(normalized)
    if contract is not None:
        if contract.classification == "read":
            return "read_only"
        if contract.classification == "write":
            return "write_action"
        if contract.classification == "system":
            return "system"
    lowered = normalized.lower()
    if normalized in EXTERNAL_SEND_TOOLS or lowered.endswith(".send") or lowered.endswith("_send"):
        return "external_send"
    if normalized in WRITE_TOOLS or normalized.endswith(".messages_send"):
        return "write_action"
    if normalized in SYSTEM_TOOLS or normalized.endswith(".ai_reply"):
        return "system"
    if normalized in READ_TOOLS:
        return "read_only"
    return "unknown"''',
)

governance = replace_function(
    governance,
    "_risk_for_tool_type",
    '''def _risk_for_tool_type(tool_type: str) -> str:
    if tool_type == "external_send":
        return "critical"
    if tool_type == "write_action":
        return "high"
    if tool_type == "system":
        return "medium"
    if tool_type == "unknown":
        return "high"
    return "low"''',
)

governance = replace_function(
    governance,
    "_retry_policy_for_type",
    '''def _retry_policy_for_type(tool_type: str) -> str:
    if tool_type == "unknown":
        return "never"
    if tool_type in {"external_send", "write_action"}:
        return "no_auto_retry_without_idempotency"
    return "read_retry_allowed"''',
)

evaluate = '''def evaluate_tool_call_policy(
    *,
    tool_name: str,
    tool_type: str | None = None,
    actor_capabilities: Iterable[str] | None = None,
) -> ToolPolicyDecision:
    resolved_type = tool_type or classify_tool_type(tool_name)
    risk_level = _risk_for_tool_type(resolved_type)
    mode = _enforcement_mode()
    required = _required_capability(tool_name, resolved_type)
    audit_only = mode != "enforce"

    # Unknown Tools are never silently downgraded to read-only. Registration is
    # part of the authority boundary, independent of rollout/audit mode.
    if resolved_type == "unknown":
        return ToolPolicyDecision(
            False,
            mode,
            tool_name,
            resolved_type,
            risk_level,
            "unknown_tool_not_registered",
            None,
            False,
        )

    if mode == "off":
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, "governance_off", required, True)

    if resolved_type in {"read_only", "system"}:
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, "read_or_system_allowed", required, audit_only)

    is_external_send = resolved_type == "external_send"
    is_write = resolved_type == "write_action"
    require_write = _env_bool("TOOL_GOVERNANCE_REQUIRE_CAPABILITY_FOR_WRITE", True)
    require_external = _env_bool("TOOL_GOVERNANCE_REQUIRE_CAPABILITY_FOR_EXTERNAL_SEND", True)
    block_write = _env_bool("TOOL_GOVERNANCE_BLOCK_WRITE_TOOLS", True)

    needs_capability = (is_write and require_write) or (is_external_send and require_external)
    capability_ok = _has_capability(actor_capabilities, required)
    should_block = (block_write or needs_capability) and not capability_ok
    reason = "write_or_external_send_allowed_with_capability" if capability_ok else "write_or_external_send_requires_capability"

    if should_block and mode == "audit_only":
        return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, f"would_block:{reason}", required, True)
    if should_block and mode == "enforce":
        return ToolPolicyDecision(False, mode, tool_name, resolved_type, risk_level, reason, required, False)
    return ToolPolicyDecision(True, mode, tool_name, resolved_type, risk_level, reason, required, audit_only)'''
governance = replace_function(governance, "evaluate_tool_call_policy", evaluate)
write(governance_path, governance)


speedaf_governance_test_path = "backend/tests/test_speedaf_tool_governance.py"
speedaf_tests = read(speedaf_governance_test_path)
for old, new in (
    ("speedaf.work_order.create", "speedaf.workOrder.create"),
    ("speedaf.order.cancel", "speedaf.order.cancel.request"),
    ("speedaf.order.update_address", "speedaf.order.updateAddress.request"),
):
    speedaf_tests = speedaf_tests.replace(old, new)
speedaf_tests = speedaf_tests.replace(
    'assert classify_tool_type("speedaf.voice.callback") == "system"',
    'assert classify_tool_type("speedaf.voice.callback") == "write_action"',
)
write(speedaf_governance_test_path, speedaf_tests)


tool_governance_test_path = "backend/tests/test_tool_governance.py"
tool_tests = read(tool_governance_test_path)
tool_tests = tool_tests.replace(
    '    assert classify_tool_type("unknown_future_tool") == "read_only"\n',
    '    assert classify_tool_type("unknown_future_tool") == "unknown"\n',
)
if "test_unknown_tool_fails_closed_in_every_mode" not in tool_tests:
    tool_tests = tool_tests.rstrip() + '''


def test_unknown_tool_fails_closed_in_every_mode(monkeypatch):
    for mode in ("off", "audit_only", "enforce"):
        monkeypatch.setenv("TOOL_GOVERNANCE_ENFORCEMENT_MODE", mode)
        decision = evaluate_tool_call_policy(tool_name="unknown_future_tool")
        assert decision.allowed is False
        assert decision.tool_type == "unknown"
        assert decision.reason_code == "unknown_tool_not_registered"
        assert decision.audit_only is False
'''
write(tool_governance_test_path, tool_tests)


# ---------------------------------------------------------------------------
# 3. One Agent context name and one provider model-output contract.
# ---------------------------------------------------------------------------
for root in (ROOT / "backend" / "app", ROOT / "backend" / "tests"):
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "build_webchat_runtime_context" in text:
            path.write_text(text.replace("build_webchat_runtime_context", "build_agent_context"), encoding="utf-8")

persona_api_path = "backend/app/api/persona_profiles.py"
persona_api = read(persona_api_path)
write(persona_api_path, persona_api)

persona_test_path = "backend/tests/test_persona_builder_contract.py"
persona_tests = read(persona_test_path)
persona_tests = persona_tests.replace('"nexus.webchat_runtime_context"', '"nexus.agent_context.v1"')
persona_tests = persona_tests.replace('"build_webchat_runtime_context"', '"build_agent_context"')
write(persona_test_path, persona_tests)

output_contract_path = "backend/app/services/provider_runtime/output_contracts.py"
write(
    output_contract_path,
    '''from __future__ import annotations

import json
import re
from typing import Any

from ..webchat_ai_decision_runtime.schemas import AIDecision

AGENT_TURN_OUTPUT_CONTRACT = "nexus.agent_turn.v1"
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"\\bBearer\\s+[A-Za-z0-9._~+/=-]{12,}", re.IGNORECASE),
    re.compile(r"\\beyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE),
)
_INTERNAL_MARKERS = (
    "<think",
    "hidden reasoning",
    "chain of thought",
    "developer message",
    "developer instruction",
    "system prompt",
    "provider_runtime",
    "localhost",
    "127.0.0.1",
)


class OutputContracts:
    @staticmethod
    def get_schema(contract_name: str) -> dict[str, Any]:
        if contract_name == AGENT_TURN_OUTPUT_CONTRACT:
            return AIDecision.model_json_schema()
        return {}

    @staticmethod
    def validate_and_parse(contract_name: str, raw_output: str) -> dict[str, Any]:
        if contract_name != AGENT_TURN_OUTPUT_CONTRACT:
            raise ValueError("Unsupported output contract")
        try:
            parsed = json.loads(raw_output)
        except json.JSONDecodeError as exc:
            raise ValueError("Output must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Output must be a JSON object")
        decision = AIDecision.model_validate(parsed)
        if decision.customer_reply:
            OutputContracts.check_customer_visible_security(decision.customer_reply)
        return decision.model_dump(exclude_none=True)

    @staticmethod
    def check_customer_visible_security(reply: str) -> None:
        lowered = reply.lower()
        if any(marker.lower() in lowered for marker in _INTERNAL_MARKERS):
            raise ValueError("Customer reply contains internal runtime or reasoning content")
        if any(pattern.search(reply) for pattern in _SECRET_PATTERNS):
            raise ValueError("Potential secret leakage detected")
''',
)

for root in (ROOT / "backend" / "app", ROOT / "backend" / "tests"):
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "WEBCHAT_RUNTIME_OUTPUT_CONTRACT" in text:
            path.write_text(text.replace("WEBCHAT_RUNTIME_OUTPUT_CONTRACT", "AGENT_TURN_OUTPUT_CONTRACT"), encoding="utf-8")


# ---------------------------------------------------------------------------
# 4. Remove the non-authoritative Provider shadow execution path.
# ---------------------------------------------------------------------------
traffic_path = "backend/app/services/provider_runtime/traffic_selection.py"
traffic = read(traffic_path)
traffic = traffic.replace('_VALID_MODES = frozenset({"control", "shadow", "canary", "full"})', '_VALID_MODES = frozenset({"control", "canary", "full"})')
traffic = traffic.replace('    SHADOW_ONLY = "shadow_only"\n', '')
shadow_block = '''    if mode == "shadow":
        return ProviderTrafficSelection(
            configured_mode=mode,
            path=ProviderTrafficPath.SHADOW_ONLY,
            canary_percent=percent,
            bucket=bucket,
            execute_candidate=True,
            authoritative=False,
            reason="shadow_bucket_selected",
        )

'''
if shadow_block not in traffic:
    raise SystemExit("provider shadow selection block missing")
traffic = traffic.replace(shadow_block, "", 1)
write(traffic_path, traffic)

router_path = "backend/app/services/provider_runtime/router.py"
router = read(router_path)
router = router.replace(
    "        shadow_only = traffic.path == ProviderTrafficPath.SHADOW_ONLY\n"
    "        operation = \"shadow_generate\" if shadow_only else \"generate\"\n",
    "        operation = \"generate\"\n",
    1,
)
router = router.replace("return _shadow_result(traffic, result.elapsed_ms, safe_summary) if shadow_only else result", "return result")
router = router.replace('"shadow_parse_reject" if shadow_only else "parse_reject"', '"parse_reject"')
router = router.replace('"shadow_ok" if shadow_only else "ok"', '"ok"')
router = router.replace(
    "            if shadow_only:\n"
    "                return _shadow_result(traffic, result.elapsed_ms, safe_summary)\n",
    "",
)
router = router.replace(
    "        if shadow_only:\n"
    "            return _shadow_result(traffic, last_elapsed_ms, summary)\n",
    "",
)
if "def _shadow_result(" in router:
    router = remove_function(router, "_shadow_result")
write(router_path, router)

traffic_tests_path = "backend/tests/test_provider_runtime_traffic_selection.py"
traffic_tests = read(traffic_tests_path)
traffic_tests = traffic_tests.replace('scenario="webchat_runtime_reply"', 'scenario="agent_turn"')
traffic_tests = traffic_tests.replace('output_contract="nexus.webchat_runtime_reply"', 'output_contract="nexus.agent_turn.v1"')
for name in ("test_zero_percent_shadow_never_executes_candidate", "test_full_shadow_executes_without_authority"):
    if re.search(rf"^def {name}\(", traffic_tests, flags=re.MULTILINE):
        traffic_tests = remove_function(traffic_tests, name)
if "test_shadow_mode_is_rejected" not in traffic_tests:
    traffic_tests = traffic_tests.rstrip() + '''


def test_shadow_mode_is_rejected():
    with pytest.raises(ValueError, match="provider_runtime_traffic_mode_invalid"):
        select_provider_traffic(
            _request(),
            canary_percent=100,
            kill_switch=False,
            configured_mode_value="shadow",
            runtime_enabled_value=True,
        )
'''
write(traffic_tests_path, traffic_tests)

router_tests_path = "backend/tests/test_provider_runtime_router.py"
router_tests = read(router_tests_path)
router_tests = router_tests.replace('@pytest.mark.parametrize("mode", ["canary", "shadow"])', '@pytest.mark.parametrize("mode", ["canary"])')
if re.search(r"^async def test_shadow_executes_but_never_returns_candidate_authority\(", router_tests, flags=re.MULTILINE):
    router_tests = remove_function(router_tests, "test_shadow_executes_but_never_returns_candidate_authority")
if "test_shadow_mode_fails_configuration_before_provider_execution" not in router_tests:
    router_tests = router_tests.rstrip() + '''


@pytest.mark.asyncio
async def test_shadow_mode_fails_configuration_before_provider_execution(monkeypatch):
    monkeypatch.setenv("PROVIDER_RUNTIME_TRAFFIC_MODE", "shadow")
    db = _mock_db(_rule(canary_percent=100))
    adapter = _register_adapter()

    result = await ProviderRuntimeRouter(db).route(_request())

    assert result.error_code == "provider_runtime_configuration_invalid"
    assert adapter.calls == 0
'''
write(router_tests_path, router_tests)

