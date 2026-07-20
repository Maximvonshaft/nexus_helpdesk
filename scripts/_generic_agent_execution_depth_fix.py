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
old_helpers = '''def _bounded_execution_arguments(value: dict[str, Any]) -> dict[str, Any]:
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
new_helpers = '''def _bounded_execution_arguments(
    value: dict[str, Any],
    *,
    depth: int = 0,
) -> dict[str, Any]:
    if depth >= 5:
        return {"truncated": "[truncated]"}
    output: dict[str, Any] = {}
    for raw_key, item in list((value or {}).items())[:80]:
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
        output[key] = _bounded_execution_value(item, depth=depth + 1)
    return output


def _bounded_execution_value(value: Any, *, depth: int) -> Any:
    if depth >= 5:
        return "[truncated]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:4000]
    if isinstance(value, dict):
        return _bounded_execution_arguments(value, depth=depth)
    if isinstance(value, (list, tuple)):
        return [
            _bounded_execution_value(item, depth=depth + 1)
            for item in list(value)[:50]
        ]
    return str(value)[:1000]
'''
core = replace_once(
    core,
    old_helpers,
    new_helpers,
    label="nested execution bounds",
)
write(core_path, core)

test_path = "backend/tests/test_nexus_osr_tool_execution_service.py"
tests = read(test_path)
depth_test = '''


def test_execution_argument_bounding_stops_nested_payloads():
    nested = {"level": {"level": {"level": {"level": {"level": {"level": "too deep"}}}}}}
    action = runtime_tool_actions_from_tool_calls(
        [{"tool_name": "timeline.event.create", "arguments": nested}]
    )[0]

    assert "too deep" not in str(action.arguments)
    assert "[truncated]" in str(action.arguments)
'''
if "test_execution_argument_bounding_stops_nested_payloads" not in tests:
    tests = tests.rstrip() + depth_test
write(test_path, tests)

assert "_bounded_execution_arguments(value, depth=depth)" in read(core_path)
assert "list((value or {}).items())[:80]" in read(core_path)
