#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "backend/app/services/nexus_osr/tool_execution_service_core.py"
TESTS = ROOT / "backend/tests/test_nexus_osr_tool_execution_service.py"
RESIDUE = ROOT / "scripts/ci/check_agent_runtime_residue.py"
WORKFLOW = ROOT / ".github/workflows/temporary-agent-schema-order-patch.yml"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_core() -> None:
    text = CORE.read_text(encoding="utf-8")
    helper = '''\n\ndef _raw_policy_tool_calls(\n    raw_calls: list[dict[str, Any]],\n) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:\n    \"\"\"Preserve raw model arguments for Registry schema validation.\n\n    Execution still receives bounded arguments through RuntimeToolAction, but\n    malformed or additional properties must be rejected before that bounding\n    can remove evidence of a contract violation.\n    \"\"\"\n\n    normalized: list[tuple[dict[str, Any], str, dict[str, Any]]] = []\n    for raw in raw_calls:\n        data = _tool_call_dict(raw)\n        tool_name = canonical_tool_name(\n            data.get(\"tool_name\") or data.get(\"name\") or data.get(\"tool\")\n        )\n        if not tool_name:\n            continue\n        arguments = (\n            data.get(\"arguments\")\n            if isinstance(data.get(\"arguments\"), dict)\n            else {}\n        )\n        normalized.append((data, tool_name, arguments))\n    return normalized\n'''
    anchor = "\ndef _decision_for_policy_gate(\n"
    if "def _raw_policy_tool_calls(" not in text:
        text = replace_once(text, anchor, helper + anchor, label="core helper anchor")

    old = '''        tool_calls=[\n            AIDecisionToolCall.model_construct(\n                tool_name=action.tool_name,\n                arguments=dict(action.arguments),\n                idempotency_key=None,\n                requires_confirmation=action.requires_confirmation,\n            )\n            for action in actions\n        ],'''
    new = '''        tool_calls=[\n            AIDecisionToolCall.model_construct(\n                tool_name=tool_name,\n                arguments=dict(arguments),\n                idempotency_key=None,\n                requires_confirmation=bool(data.get(\"requires_confirmation\")),\n            )\n            for data, tool_name, arguments in _raw_policy_tool_calls(raw_calls)\n        ],'''
    text = replace_once(text, old, new, label="raw schema validation projection")
    CORE.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TESTS.read_text(encoding="utf-8")
    marker = "def test_executor_rejects_raw_additional_properties_before_argument_bounding"
    if marker in text:
        return
    text += '''\n\n\ndef test_executor_rejects_raw_additional_properties_before_argument_bounding(\n    db_session,\n):\n    add_policy(db_session, \"knowledge.search\", risk_level=\"low\")\n    secret = \"raw-value-must-never-reach-handler-or-audit\"\n\n    result = execute_controlled_tool_calls(\n        db_session,\n        tool_calls=[\n            {\n                \"tool_name\": \"knowledge.search\",\n                \"arguments\": {\n                    \"query\": \"approved policy\",\n                    \"raw_payload\": {\"token\": secret},\n                },\n            }\n        ],\n        case_context=CaseContext(channel=\"webchat\", country_code=\"ME\"),\n        channel=\"webchat\",\n        country_code=\"ME\",\n        options=GovernedToolExecutionOptions(\n            allowed_tool_names=frozenset({\"knowledge.search\"}),\n            granted_permissions=frozenset({\"knowledge:read\"}),\n        ),\n    )[0]\n\n    assert result.ok is False\n    assert result.status == \"blocked\"\n    assert result.error_code == \"tool_input_schema_invalid\"\n    assert \"additionalProperties\" in (result.error_message or \"\")\n    log = db_session.query(ToolCallLog).one()\n    assert secret not in f\"{log.input_summary} {log.output_summary} {log.error_message}\"\n'''
    TESTS.write_text(text, encoding="utf-8")


def patch_residue_gate() -> None:
    text = RESIDUE.read_text(encoding="utf-8")
    marker = '"arguments=dict(action.arguments),\\n                idempotency_key=None",'
    if marker not in text:
        anchor = '    "**_legacy: Any,",\n'
        text = replace_once(
            text,
            anchor,
            anchor + f"    {marker}\n",
            label="residue gate anchor",
        )
    RESIDUE.write_text(text, encoding="utf-8")


def cleanup() -> None:
    for path in (WORKFLOW, SELF):
        path.unlink(missing_ok=True)


def main() -> None:
    patch_core()
    patch_tests()
    patch_residue_gate()
    cleanup()


if __name__ == "__main__":
    main()
