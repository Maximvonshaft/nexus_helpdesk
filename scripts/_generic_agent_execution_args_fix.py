from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


core_path = "backend/app/services/nexus_osr/tool_execution_service_core.py"
core = read(core_path)
core = replace_once(
    core,
    "                arguments=_safe_tool_arguments(arguments),\n",
    "                arguments=_bounded_execution_arguments(arguments),\n",
    label="execution argument construction",
)
core = replace_once(
    core,
    "                arguments=action.arguments,\n"
    "                requires_confirmation=action.requires_confirmation,\n"
    "                executed=result.ok and result.status == \"executed\",\n",
    "                arguments=_safe_tool_arguments(action.arguments),\n"
    "                requires_confirmation=action.requires_confirmation,\n"
    "                executed=result.ok and result.status == \"executed\",\n",
    label="audit argument projection",
)
old_idempotency = '''def _idempotency_key_for_action(
    raw_calls: list[dict[str, Any]],
    action: RuntimeToolAction,
) -> str | None:
    for item in raw_calls:
        tool_name = canonical_tool_name(
            item.get("tool_name") or item.get("name") or item.get("tool")
        )
        if tool_name == action.tool_name:
            raw_key = item.get("idempotency_key")
            if raw_key:
                return redact_case_text(raw_key, limit=160)
    seed = _summary_json(
        {"tool_name": action.tool_name, "arguments": action.arguments}
    )
    return _sha256(seed)
'''
new_idempotency = '''def _idempotency_key_for_action(
    raw_calls: list[dict[str, Any]],
    action: RuntimeToolAction,
) -> str | None:
    model_key = None
    for item in raw_calls:
        tool_name = canonical_tool_name(
            item.get("tool_name") or item.get("name") or item.get("tool")
        )
        if tool_name == action.tool_name and item.get("idempotency_key"):
            model_key = str(item.get("idempotency_key"))[:240]
            break
    canonical = json.dumps(
        {
            "tool_name": action.tool_name,
            "arguments": action.arguments,
            "model_key": model_key,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256(canonical)
'''
core = replace_once(
    core,
    old_idempotency,
    new_idempotency,
    label="server-owned idempotency key",
)
helper_marker = '''def _safe_tool_arguments(value: dict[str, Any]) -> dict[str, Any]:
'''
execution_helpers = '''def _bounded_execution_arguments(value: dict[str, Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for raw_key, item in (value or {}).items():
        key = str(raw_key)[:80]
        lowered = key.lower()
        if any(
            token in lowered
            for token in (
                "token",
                "secret",
                "password",
                "authorization",
                "api_key",
            )
        ):
            continue
        if lowered in {
            "raw",
            "raw_payload",
            "provider_payload",
            "request",
            "response",
        }:
            continue
        output[key] = _bounded_execution_value(item, depth=0)
    return output


def _bounded_execution_value(value: Any, *, depth: int) -> Any:
    if depth >= 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, dict):
        return _bounded_execution_arguments(value)
    if isinstance(value, (list, tuple)):
        return [
            _bounded_execution_value(item, depth=depth + 1)
            for item in list(value)[:50]
        ]
    return str(value)[:1000]


'''
core = replace_once(
    core,
    helper_marker,
    execution_helpers + helper_marker,
    label="execution argument helpers",
)
write(core_path, core)

# Existing regression previously encoded the bug by requiring the execution
# action itself to be redacted. Replace it with the correct execution/audit split.
test_path = "backend/tests/test_nexus_osr_tool_execution_service.py"
tests = read(test_path)
old_test = '''def test_runtime_tool_calls_convert_to_runtime_tool_actions_and_redact_arguments():
    actions = runtime_tool_actions_from_tool_calls([
        {
            "tool_name": "speedaf.workOrder.create",
            "arguments": {
                "tracking_number": "CH1234567890",
                "phone": "+382 67123456",
                "address": "123 Unsafe Street",
                "raw_payload": {"token": "secret-value"},
            },
            "requires_confirmation": True,
        }
    ])

    assert len(actions) == 1
    action = actions[0]
    assert action.tool_name == "speedaf.workOrder.create"
    assert action.requires_confirmation is True
    serialized = str(action.arguments)
    assert "CH1234567890" not in serialized
    assert "+382" not in serialized
    assert "123 Unsafe Street" not in serialized
    assert "secret-value" not in serialized
'''
new_test = '''def test_runtime_tool_calls_preserve_bounded_execution_arguments():
    actions = runtime_tool_actions_from_tool_calls([
        {
            "tool_name": "speedaf.workOrder.create",
            "arguments": {
                "tracking_number": "CH1234567890",
                "phone": "+382 67123456",
                "address": "123 Unsafe Street",
                "raw_payload": {"token": "secret-value"},
            },
            "requires_confirmation": True,
        }
    ])

    assert len(actions) == 1
    action = actions[0]
    assert action.tool_name == "speedaf.workOrder.create"
    assert action.requires_confirmation is True
    assert action.arguments["tracking_number"] == "CH1234567890"
    assert action.arguments["phone"] == "+382 67123456"
    assert action.arguments["address"] == "123 Unsafe Street"
    assert "raw_payload" not in action.arguments
    assert "secret-value" not in str(action.arguments)
'''
tests = replace_once(
    tests,
    old_test,
    new_test,
    label="execution arguments regression",
)
idempotency_test = '''


def test_server_idempotency_hash_distinguishes_full_execution_arguments():
    first = runtime_tool_actions_from_tool_calls(
        [{"tool_name": "speedaf.order.query", "arguments": {"tracking_number": "CH111111123456"}}]
    )[0]
    second = runtime_tool_actions_from_tool_calls(
        [{"tool_name": "speedaf.order.query", "arguments": {"tracking_number": "CH222222123456"}}]
    )[0]

    from app.services.nexus_osr import tool_execution_service_core as core

    first_key = core._idempotency_key_for_action([], first)
    second_key = core._idempotency_key_for_action([], second)

    assert first_key != second_key
    assert "CH111111123456" not in first_key
    assert "CH222222123456" not in second_key
'''
if "test_server_idempotency_hash_distinguishes_full_execution_arguments" not in tests:
    tests = tests.rstrip() + idempotency_test
write(test_path, tests)

# Permanent architecture evidence: audit code may call _safe_tool_arguments;
# the RuntimeToolAction constructor may not.
architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
separation_test = '''


def test_canonical_executor_separates_execution_arguments_from_audit_projection() -> None:
    source = Path(
        "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    constructor = source.split("def runtime_tool_actions_from_tool_calls", 1)[1].split(
        "def execute_controlled_tool_calls", 1
    )[0]
    assert "_bounded_execution_arguments(arguments)" in constructor
    assert "_safe_tool_arguments(arguments)" not in constructor
    assert "arguments=_safe_tool_arguments(action.arguments)" in source
'''
if "test_canonical_executor_separates_execution_arguments_from_audit_projection" not in architecture:
    architecture = architecture.rstrip() + separation_test
write(architecture_path, architecture)

assert "arguments=_bounded_execution_arguments(arguments)" in read(core_path)
assert "arguments=_safe_tool_arguments(action.arguments)" in read(core_path)
assert "return redact_case_text(raw_key" not in read(core_path)
