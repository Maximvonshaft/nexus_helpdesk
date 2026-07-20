from __future__ import annotations

import re
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


def remove_function(text: str, name: str) -> str:
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.MULTILINE)
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(
        r"^def [A-Za-z0-9_]+\(",
        text[match.end():],
        flags=re.MULTILINE,
    )
    end = len(text) if next_match is None else match.end() + next_match.start()
    return text[:match.start()].rstrip() + "\n\n\n" + text[end:].lstrip("\n")


# Public WebChat Agent authorization is a named, server-owned principal policy.
# Tool visibility must never manufacture the permissions required to execute it.
access_policy_path = "backend/app/services/agent_runtime/access_policy.py"
write(
    access_policy_path,
    '''from __future__ import annotations

import os
from dataclasses import dataclass

from ..webchat_ai_decision_runtime.tool_registry import get_tool_contract
from .tool_adapter import executable_tool_names


_DEFAULT_PUBLIC_WEBCHAT_TOOLS = frozenset(
    {
        "knowledge.search",
        "support.availability",
        "speedaf.order.query",
        "speedaf.express.track.query",
        "speedaf.order.waybillCode.query",
        "handoff.request.create",
        "ticket.create",
        "timeline.event.create",
    }
)

# This is an explicit compile-time principal policy, not a projection from Tool
# contracts. Production may replace it through the separate permissions setting.
_DEFAULT_PUBLIC_WEBCHAT_PERMISSIONS = frozenset(
    {
        "knowledge:read",
        "webchat:handoff:create",
        "speedaf:tracking:read",
        "ticket:create",
        "timeline:event:create",
    }
)


@dataclass(frozen=True)
class WebchatAgentAccessPolicy:
    allowed_tools: tuple[str, ...]
    granted_permissions: frozenset[str]
    actor_capabilities: frozenset[str]


def resolve_webchat_agent_access() -> WebchatAgentAccessPolicy:
    requested_tools = _configured_set(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        _DEFAULT_PUBLIC_WEBCHAT_TOOLS,
    )
    granted_permissions = _configured_set(
        "WEBCHAT_AGENT_GRANTED_PERMISSIONS",
        _DEFAULT_PUBLIC_WEBCHAT_PERMISSIONS,
    )
    executable = set(executable_tool_names())
    allowed = []
    for name in sorted(requested_tools & executable):
        contract = get_tool_contract(name)
        if contract is None:
            continue
        if not set(contract.required_permissions).issubset(granted_permissions):
            continue
        allowed.append(name)
    return WebchatAgentAccessPolicy(
        allowed_tools=tuple(allowed),
        granted_permissions=frozenset(granted_permissions),
        actor_capabilities=frozenset(granted_permissions),
    )


def _configured_set(name: str, default: frozenset[str]) -> frozenset[str]:
    raw = os.getenv(name)
    if raw is None:
        return default
    return frozenset(
        item.strip()
        for item in raw.split(",")
        if item.strip()
    )
''',
)

# Ticket-backed and ticketless WebChat both consume the same configured Persona
# builder and the same public-Agent access-policy authority.
ticket_path = "backend/app/services/webchat_ai_service.py"
ticket_source = read(ticket_path)
ticket_source = replace_once(
    ticket_source,
    "import os\n",
    "",
    label="ticket os import",
)
ticket_source = replace_once(
    ticket_source,
    "from .agent_runtime.tool_adapter import executable_tool_names\n",
    "from .agent_runtime.access_policy import resolve_webchat_agent_access\n"
    "from .ai_runtime_context import build_webchat_runtime_context\n",
    label="ticket agent imports",
)
ticket_source = replace_once(
    ticket_source,
    "from .webchat_ai_decision_runtime.tool_registry import get_tool_contract\n",
    "",
    label="ticket registry import",
)
old_ticket_context = '''    runtime_context = {
        "agent_allowed_tools": _webchat_allowed_tools(),
        "agent_execution_context": {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "customer_id": getattr(ticket, "customer_id", None),
            "country_code": getattr(ticket, "country_code", None),
            "ai_turn_id": ai_turn_id,
            "granted_permissions": _permissions_for_tools(_webchat_allowed_tools()),
            "actor_capabilities": _permissions_for_tools(_webchat_allowed_tools()),
        },
        "channel_context": {
            "market_id": getattr(ticket, "market_id", None),
            "channel": conversation.channel_key,
            "country_code": getattr(ticket, "country_code", None),
        },
        "persona_context": {
            "assistant_name": "Speedy",
            "brand": "Speedaf",
            "role": "customer support assistant",
        },
    }
'''
new_ticket_context = '''    access = resolve_webchat_agent_access()
    runtime_context = build_webchat_runtime_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        market_id=getattr(ticket, "market_id", None),
        language=language,
        ticket=ticket,
        conversation=conversation,
        customer=getattr(ticket, "customer", None),
    )
    execution_context = dict(runtime_context.get("agent_execution_context") or {})
    execution_context.update(
        {
            "conversation_id": conversation.id,
            "ticket_id": ticket.id,
            "customer_id": getattr(ticket, "customer_id", None),
            "country_code": getattr(ticket, "country_code", None),
            "ai_turn_id": ai_turn_id,
            "granted_permissions": sorted(access.granted_permissions),
            "actor_capabilities": sorted(access.actor_capabilities),
        }
    )
    runtime_context["agent_allowed_tools"] = list(access.allowed_tools)
    runtime_context["agent_execution_context"] = execution_context
'''
ticket_source = replace_once(
    ticket_source,
    old_ticket_context,
    new_ticket_context,
    label="ticket runtime context",
)
for function_name in (
    "_webchat_allowed_tools",
    "_permissions_for_tools",
    "_env_bool",
):
    ticket_source = remove_function(ticket_source, function_name)
write(ticket_path, ticket_source)

conversation_path = "backend/app/services/conversation_ai_service.py"
conversation_source = read(conversation_path)
conversation_source = replace_once(
    conversation_source,
    "import os\n",
    "",
    label="conversation os import",
)
conversation_source = replace_once(
    conversation_source,
    "from ..models_agent_routing import ConversationControl\n",
    "from ..models import Customer\n"
    "from ..models_agent_routing import ConversationControl\n",
    label="conversation customer import",
)
conversation_source = replace_once(
    conversation_source,
    "from .agent_runtime.tool_adapter import executable_tool_names\n",
    "from .agent_runtime.access_policy import resolve_webchat_agent_access\n"
    "from .ai_runtime_context import build_webchat_runtime_context\n",
    label="conversation agent imports",
)
conversation_source = replace_once(
    conversation_source,
    "from .webchat_ai_decision_runtime.tool_registry import get_tool_contract\n",
    "",
    label="conversation registry import",
)
for function_name in ("_allowed_tools", "_permissions"):
    conversation_source = remove_function(conversation_source, function_name)
old_conversation_setup = '''    language = _language_hint(visitor_message.body or "", rows)
    allowed_tools = _allowed_tools()
    permissions = _permissions(allowed_tools)
    result = _run_runtime(
'''
new_conversation_setup = '''    language = _language_hint(visitor_message.body or "", rows)
    access = resolve_webchat_agent_access()
    customer = (
        db.get(Customer, control.customer_id)
        if control is not None and control.customer_id is not None
        else None
    )
    runtime_context = build_webchat_runtime_context(
        db,
        tenant_key=conversation.tenant_key,
        channel_key=conversation.channel_key,
        body=visitor_message.body or "",
        market_id=None,
        language=language,
        ticket=None,
        conversation=conversation,
        customer=customer,
    )
    execution_context = dict(runtime_context.get("agent_execution_context") or {})
    execution_context.update(
        {
            "conversation_id": conversation.id,
            "ticket_id": None,
            "customer_id": control.customer_id if control is not None else None,
            "country_code": control.country_code if control is not None else None,
            "ai_turn_id": turn.id if turn else None,
            "granted_permissions": sorted(access.granted_permissions),
            "actor_capabilities": sorted(access.actor_capabilities),
        }
    )
    runtime_context["agent_allowed_tools"] = list(access.allowed_tools)
    runtime_context["agent_execution_context"] = execution_context
    result = _run_runtime(
'''
conversation_source = replace_once(
    conversation_source,
    old_conversation_setup,
    new_conversation_setup,
    label="conversation runtime setup",
)
old_conversation_context = '''        runtime_context={
            "agent_allowed_tools": allowed_tools,
            "agent_execution_context": {
                "conversation_id": conversation.id,
                "ticket_id": None,
                "customer_id": (
                    control.customer_id if control is not None else None
                ),
                "country_code": (
                    control.country_code if control is not None else None
                ),
                "ai_turn_id": turn.id if turn else None,
                "granted_permissions": permissions,
                "actor_capabilities": permissions,
            },
            "persona_context": {
                "assistant_name": "Speedy",
                "brand": "Speedaf",
                "role": "customer support assistant",
            },
        },
'''
conversation_source = replace_once(
    conversation_source,
    old_conversation_context,
    "        runtime_context=runtime_context,\n",
    label="conversation runtime context",
)
write(conversation_path, conversation_source)

# Make the no-synthesized-authority invariant permanent in the residue gate.
residue_path = "scripts/ci/check_agent_runtime_residue.py"
residue = read(residue_path)
residue = replace_once(
    residue,
    '    "speedaf_update_address",\n',
    '    "speedaf_update_address",\n'
    '    "_permissions_for_tools",\n'
    '    \'"assistant_name": "Speedy"\',\n'
    '    \'"brand": "Speedaf"\',\n',
    label="authority residue markers",
)
write(residue_path, residue)

# Regression evidence for explicit server policy, configured Persona projection
# and the absence of permission synthesis in both caller paths.
test_path = "backend/tests/test_agent_runtime_architecture.py"
tests = read(test_path)
tests = replace_once(
    tests,
    "import json\n",
    "import json\nfrom pathlib import Path\n",
    label="architecture Path import",
)
tests = replace_once(
    tests,
    "from app.services.agent_runtime import service as agent_service\n",
    "from app.services.agent_runtime import service as agent_service\n"
    "from app.services.agent_runtime.access_policy import resolve_webchat_agent_access\n",
    label="architecture access import",
)
authority_tests = '''


def test_public_agent_access_policy_does_not_derive_grants_from_visible_tools(monkeypatch) -> None:
    monkeypatch.setenv(
        "WEBCHAT_AGENT_ALLOWED_TOOLS",
        "knowledge.search,ticket.create",
    )
    monkeypatch.setenv(
        "WEBCHAT_AGENT_GRANTED_PERMISSIONS",
        "knowledge:read",
    )

    policy = resolve_webchat_agent_access()

    assert policy.allowed_tools == ("knowledge.search",)
    assert policy.granted_permissions == frozenset({"knowledge:read"})
    assert "ticket:create" not in policy.granted_permissions


def test_webchat_agent_callers_use_configured_persona_and_server_access_policy() -> None:
    for relative in (
        "backend/app/services/webchat_ai_service.py",
        "backend/app/services/conversation_ai_service.py",
    ):
        source = Path(relative).read_text(encoding="utf-8")
        assert "resolve_webchat_agent_access" in source
        assert "build_webchat_runtime_context" in source
        assert "_permissions_for_tools" not in source
        assert '"assistant_name": "Speedy"' not in source
        assert '"brand": "Speedaf"' not in source
'''
if "test_public_agent_access_policy_does_not_derive_grants_from_visible_tools" not in tests:
    tests = tests.rstrip() + authority_tests
write(test_path, tests)

assert Path(access_policy_path).exists()
assert "resolve_webchat_agent_access" in read(ticket_path)
assert "resolve_webchat_agent_access" in read(conversation_path)
assert "_permissions_for_tools" not in read(ticket_path)
assert '"assistant_name": "Speedy"' not in read(ticket_path)
assert '"assistant_name": "Speedy"' not in read(conversation_path)
