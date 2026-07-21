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
    "                idempotency_key=_idempotency_key_for_action(raw_calls, action),\n",
    "                idempotency_key=_idempotency_key_for_action(\n"
    "                    action,\n"
    "                    case_context=case_context,\n"
    "                    tenant_id=tenant_id,\n"
    "                    channel=channel,\n"
    "                    country_code=country_code,\n"
    "                ),\n",
    label="policy-block idempotency call",
)
core = replace_once(
    core,
    "        idempotency_key = _idempotency_key_for_action(raw_calls, action)\n",
    "        idempotency_key = _idempotency_key_for_action(\n"
    "            action,\n"
    "            case_context=case_context,\n"
    "            tenant_id=tenant_id,\n"
    "            channel=channel,\n"
    "            country_code=country_code,\n"
    "        )\n",
    label="execution idempotency call",
)
core = replace_once(
    core,
    "                idempotency_key=_idempotency_key_for_action(\n"
    "                    raw_calls,\n"
    "                    action,\n"
    "                ),\n",
    "                idempotency_key=None,\n",
    label="synthetic policy decision idempotency",
)

old_function = '''def _idempotency_key_for_action(
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
new_function = '''def _idempotency_key_for_action(
    action: RuntimeToolAction,
    *,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
) -> str | None:
    contract = get_tool_contract(action.tool_name)
    if contract is None or not contract.is_write_tool:
        # Reads must execute against current state and return a fresh Observation.
        return None
    canonical = json.dumps(
        {
            "tenant_id": str(tenant_id or "default")[:120],
            "channel": str(channel or "")[:80],
            "country_code": str(country_code or "")[:16],
            "conversation_id": str(case_context.conversation_id or "")[:160],
            "ticket_id": str(case_context.ticket_id or "")[:160],
            "tool_name": action.tool_name,
            "arguments": action.arguments,
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
    old_function,
    new_function,
    label="server-owned scoped idempotency function",
)
write(core_path, core)


test_path = "backend/tests/test_nexus_osr_tool_execution_service.py"
tests = read(test_path)
old_test = '''def test_server_idempotency_hash_distinguishes_full_execution_arguments():
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
new_test = '''def test_server_idempotency_is_scoped_to_writes_and_ignores_model_keys():
    from app.services.nexus_osr import tool_execution_service_core as core

    context = CaseContext(
        conversation_id="conversation-1",
        ticket_id="ticket-1",
        channel="website",
        country_code="CH",
    )
    read_action = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "speedaf.order.query",
                "arguments": {"tracking_number": "CH111111123456"},
                "idempotency_key": "model-controlled-read-key",
            }
        ]
    )[0]
    assert core._idempotency_key_for_action(
        read_action,
        case_context=context,
        tenant_id="tenant-1",
        channel="website",
        country_code="CH",
    ) is None

    first = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "first request"},
                "idempotency_key": "same-model-key",
            }
        ]
    )[0]
    same = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "first request"},
                "idempotency_key": "different-model-key",
            }
        ]
    )[0]
    second = runtime_tool_actions_from_tool_calls(
        [
            {
                "tool_name": "ticket.create",
                "arguments": {"description": "second request"},
                "idempotency_key": "same-model-key",
            }
        ]
    )[0]

    def key(action):
        return core._idempotency_key_for_action(
            action,
            case_context=context,
            tenant_id="tenant-1",
            channel="website",
            country_code="CH",
        )

    assert key(first) == key(same)
    assert key(first) != key(second)
    assert "same-model-key" not in str(key(first))
    assert "first request" not in str(key(first))
'''
tests = replace_once(
    tests,
    old_test,
    new_test,
    label="idempotency regression",
)
write(test_path, tests)

architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
test = '''


def test_canonical_executor_does_not_trust_model_idempotency_keys() -> None:
    source = Path(
        "backend/app/services/nexus_osr/tool_execution_service_core.py"
    ).read_text(encoding="utf-8")
    function = source.split("def _idempotency_key_for_action", 1)[1].split(
        "def _safe_tool_arguments", 1
    )[0]
    assert "model_key" not in function
    assert "raw_calls" not in function
    assert "not contract.is_write_tool" in function
    assert '"tenant_id"' in function
    assert '"conversation_id"' in function
'''
if "test_canonical_executor_does_not_trust_model_idempotency_keys" not in architecture:
    architecture = architecture.rstrip() + test
write(architecture_path, architecture)

assert "model_key" not in read(core_path).split(
    "def _idempotency_key_for_action", 1
)[1].split("def _safe_tool_arguments", 1)[0]
