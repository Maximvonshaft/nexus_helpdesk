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
    match = re.search(
        rf"^(?:async\s+)?def {re.escape(name)}\(", text, flags=re.MULTILINE
    )
    if match is None:
        raise SystemExit(f"function not found: {name}")
    next_match = re.search(
        r"^(?:async\s+)?def [A-Za-z0-9_]+\(",
        text[match.end() :],
        flags=re.MULTILINE,
    )
    end = len(text) if next_match is None else match.end() + next_match.start()
    return match.start(), end


def replace_function(text: str, name: str, replacement: str) -> str:
    start, end = function_bounds(text, name)
    return (
        text[:start].rstrip()
        + "\n\n\n"
        + replacement.strip()
        + "\n\n\n"
        + text[end:].lstrip("\n")
    )


# ---------------------------------------------------------------------------
# 1. Write idempotency remains stable when a Conversation gains a Ticket.
# ---------------------------------------------------------------------------
core_path = "backend/app/services/nexus_osr/tool_execution_service_core.py"
core = read(core_path)
core = replace_function(
    core,
    "_idempotency_key_for_action",
    '''def _idempotency_key_for_action(
    action: RuntimeToolAction,
    *,
    case_context: CaseContext,
    tenant_id: str,
    channel: str | None,
    country_code: str | None,
) -> str | None:
    contract = get_tool_contract(action.tool_name)
    if contract is None or not contract.is_write_tool:
        # Reads execute against current state and return a fresh Observation.
        return None
    conversation_scope = str(case_context.conversation_id or "")[:160]
    ticket_scope = (
        ""
        if conversation_scope
        else str(case_context.ticket_id or "")[:160]
    )
    canonical = json.dumps(
        {
            "tenant_id": str(tenant_id or "default")[:120],
            "channel": str(channel or "")[:80],
            "country_code": str(country_code or "")[:16],
            "conversation_id": conversation_scope,
            "ticket_id": ticket_scope,
            "tool_name": action.tool_name,
            "arguments": action.arguments,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return _sha256(canonical)''',
)
write(core_path, core)


# ---------------------------------------------------------------------------
# 2. Provider model-output validation and signed transport validation stay
#    separate authorities.
# ---------------------------------------------------------------------------
provider_test_path = "backend/tests/test_provider_runtime_output_contracts.py"
provider_tests = read(provider_test_path)
if "from app.services.ai_reply_contract import (" not in provider_tests:
    provider_tests = provider_tests.replace(
        "import pytest\n\n",
        "import pytest\n\n"
        "from app.services.ai_reply_contract import (\n"
        "    build_ai_reply_contract,\n"
        "    contract_validation_args_from_payload,\n"
        "    validate_ai_reply_contract,\n"
        ")\n",
        1,
    )
provider_tests = replace_function(
    provider_tests,
    "test_signed_customer_reply_contract_remains_independent_transport_envelope",
    '''def test_signed_customer_reply_contract_remains_independent_transport_envelope():
    body = "Approved answer"
    contract = build_ai_reply_contract(
        body=body,
        runtime_trace={"request_id": "trace-12345678901234567890123456789012"},
        reply_type="answer",
        used_sources=["context:customer_message"],
        unsupported_claims=[],
        conflicts=[],
        confidence=0.8,
        channel="web_chat",
    )
    payload = contract.payload_dict(body=body)

    assert validate_ai_reply_contract(
        body=body,
        **contract_validation_args_from_payload(payload),
    ) is None
    assert payload["reply"]["text"] == body
    with pytest.raises(ValueError, match="Unsupported output contract"):
        OutputContracts.validate_and_parse("nexus.ai_reply.v3", json.dumps(payload))''',
)
write(provider_test_path, provider_tests)


# ---------------------------------------------------------------------------
# 3. Tests reference only canonical Tool names and fail-closed governance.
# ---------------------------------------------------------------------------
speedaf_test_path = "backend/tests/test_speedaf_tool_governance.py"
speedaf_tests = read(speedaf_test_path)
for old, new in (
    ("speedaf.order.waybill_code.query", "speedaf.order.waybillCode.query"),
    ("speedaf.work_order.create", "speedaf.workOrder.create"),
    ("speedaf.order.cancel", "speedaf.order.cancel.request"),
    ("speedaf.order.update_address", "speedaf.order.updateAddress.request"),
):
    speedaf_tests = speedaf_tests.replace(old, new)
speedaf_tests = speedaf_tests.replace(
    'assert classify_tool_type("speedaf.voice.callback") == "system"',
    'assert classify_tool_type("speedaf.voice.callback") == "write_action"',
)
write(speedaf_test_path, speedaf_tests)

governance_test_path = "backend/tests/test_tool_governance.py"
governance_tests = read(governance_test_path)
governance_tests = governance_tests.replace(
    'tool_governance.classify_tool_type("unknown_future_tool") == "read_only"',
    'tool_governance.classify_tool_type("unknown_future_tool") == "unknown"',
)
governance_tests = governance_tests.replace(
    "decision = evaluate_tool_call_policy(tool_name=\"unknown_future_tool\")",
    "decision = tool_governance.evaluate_tool_call_policy("
    "tool_name=\"unknown_future_tool\")",
)
write(governance_test_path, governance_tests)


# ---------------------------------------------------------------------------
# 4. Session-first Voice is the only operator control route.
# ---------------------------------------------------------------------------
architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
architecture = replace_function(
    architecture,
    "test_voice_control_plane_is_session_first_and_never_auto_creates_ticket",
    '''def test_voice_control_plane_is_session_first_and_never_auto_creates_ticket() -> None:
    api = Path("backend/app/api/webchat_voice.py").read_text(encoding="utf-8")
    service = Path("backend/app/services/webchat_voice_service.py").read_text(encoding="utf-8")
    conversation = Path(
        "backend/app/services/conversation_first_service.py"
    ).read_text(encoding="utf-8")
    session_route = '"/admin/voice/{voice_session_id}/accept"'
    legacy_ticket_route = (
        '"/admin/tickets/'
        + '{ticket_id}/voice/{voice_session_id}/accept"'
    )
    assert session_route in api
    assert legacy_ticket_route not in api
    assert "ensure_voice_ticket_for_public_conversation" not in api
    assert "ensure_voice_ticket_for_public_conversation" not in conversation
    assert "_visible_voice_session_context" in service''',
)
write(architecture_path, architecture)

# Migrate all remaining test references to Session-first operation routes.
for path in (ROOT / "backend" / "tests").rglob("*.py"):
    text = path.read_text(encoding="utf-8")
    original = text
    text = re.sub(
        r'f(["\'])/api/webchat/admin/tickets/\{[^}]+\}/voice/'
        r'\{voice_session_id\}/([^"\']+)\1',
        r'f\1/api/webchat/admin/voice/{voice_session_id}/\2\1',
        text,
    )
    text = text.replace(
        "/api/webchat/admin/tickets/{ticket_id}/voice/{voice_session_id}/",
        "/api/webchat/admin/voice/{voice_session_id}/",
    )
    if text != original:
        path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# 5. Ticketless Voice requires immutable Conversation scope before a provider
#    room is created. Authorization remains exact and fail-closed.
# ---------------------------------------------------------------------------
voice_service_path = "backend/app/services/webchat_voice_service.py"
voice = read(voice_service_path)
if "from ..models_agent_routing import ConversationControl\n" not in voice:
    voice = voice.replace(
        "from ..models import Ticket, TicketInternalNote, User\n",
        "from ..models import Ticket, TicketInternalNote, User\n"
        "from ..models_agent_routing import ConversationControl\n",
        1,
    )
voice = replace_once(
    voice,
    "    _validate_public_conversation_token(conversation, visitor_token)\n"
    "    enforce_webchat_rate_limit(",
    "    _validate_public_conversation_token(conversation, visitor_token)\n"
    "    if conversation.ticket_id is None:\n"
    "        control = (\n"
    "            db.query(ConversationControl)\n"
    "            .filter(ConversationControl.conversation_id == conversation.id)\n"
    "            .first()\n"
    "        )\n"
    "        if control is None or not control.country_code:\n"
    "            raise HTTPException(\n"
    "                status_code=status.HTTP_409_CONFLICT,\n"
    "                detail=\"conversation_scope_unavailable\",\n"
    "            )\n"
    "    enforce_webchat_rate_limit(",
    label="ticketless voice scope guard",
)
write(voice_service_path, voice)

voice_test_path = "backend/tests/test_webchat_voice_api.py"
voice_tests = read(voice_test_path)
if "from app.models_agent_routing import ConversationControl" not in voice_tests:
    voice_tests = voice_tests.replace(
        "from app.models import AdminAuditLog, BackgroundJob, Ticket, TicketEvent, TicketInternalNote, User, UserCapabilityOverride\n",
        "from app.models import AdminAuditLog, BackgroundJob, Ticket, TicketEvent, TicketInternalNote, User, UserCapabilityOverride\n"
        "from app.models_agent_routing import ConversationControl\n"
        "from app.operator_models import OperatorQueueScopeGrant\n",
        1,
    )
helper = '''def _authorize_ticketless_voice_scope(
    conversation_id: str,
    *,
    user_id: int = 9202,
    country_code: str = "ME",
) -> None:
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        control = db.query(ConversationControl).filter(
            ConversationControl.conversation_id == conversation.id
        ).one()
        control.country_code = country_code
        grant = db.query(OperatorQueueScopeGrant).filter(
            OperatorQueueScopeGrant.user_id == user_id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
        ).first()
        if grant is None:
            db.add(
                OperatorQueueScopeGrant(
                    user_id=user_id,
                    tenant_key=control.tenant_key,
                    country_code=country_code,
                    channel_key=control.channel_key,
                    enabled=True,
                    granted_by=user_id,
                )
            )
        else:
            grant.enabled = True
        db.commit()
    finally:
        db.close()'''
if "def _authorize_ticketless_voice_scope(" not in voice_tests:
    marker = "def _create_voice_session("
    pos = voice_tests.find(marker)
    if pos < 0:
        raise SystemExit("voice test helper insertion marker missing")
    voice_tests = voice_tests[:pos] + helper + "\n\n\n" + voice_tests[pos:]

for snippet in (
    "    assert ticket_id is None\n\n    created = client.post(\n",
    "    assert ticket_id is None\n    created = client.post(\n",
):
    voice_tests = voice_tests.replace(
        snippet,
        "    assert ticket_id is None\n"
        "    _authorize_ticketless_voice_scope(conversation_id)\n\n"
        "    created = client.post(\n",
    )
voice_tests = voice_tests.replace(
    "    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client, create_ticket=False)\n\n"
    "    first = client.post(\n",
    "    conversation_id, visitor_token, _ticket_id = _create_webchat_conversation(client, create_ticket=False)\n"
    "    _authorize_ticketless_voice_scope(conversation_id)\n\n"
    "    first = client.post(\n",
)

missing_scope_test = '''


def test_ticketless_voice_rejects_missing_scope_before_session_creation():
    client = TestClient(app)
    conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
        client,
        name="Unscoped Voice Visitor",
        create_ticket=False,
    )
    assert ticket_id is None

    response = client.post(
        f"/api/webchat/conversations/{conversation_id}/voice/sessions",
        headers={"X-Webchat-Visitor-Token": visitor_token},
        json={},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "conversation_scope_unavailable"
    db = SessionLocal()
    try:
        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        assert db.query(WebchatVoiceSession).filter(
            WebchatVoiceSession.conversation_id == conversation.id
        ).count() == 0
    finally:
        db.close()
'''
if "test_ticketless_voice_rejects_missing_scope_before_session_creation" not in voice_tests:
    marker = "def test_public_create_voice_session_rejects_invalid_token():"
    pos = voice_tests.find(marker)
    if pos < 0:
        raise SystemExit("missing-scope test insertion marker missing")
    voice_tests = voice_tests[:pos] + missing_scope_test + "\n\n" + voice_tests[pos:]
write(voice_test_path, voice_tests)


# ---------------------------------------------------------------------------
# 6. Production LiveKit probe validates ticketless Session-first flow.
# ---------------------------------------------------------------------------
probe_path = "scripts/probe_webcall_livekit_custom_domain.sh"
probe = read(probe_path)
scope_setup = '''  "${DC[@]}" run --rm --no-deps -T app python - <<PY > "$OUT/e2e/scope_setup.json" 2>"$OUT/e2e/scope_setup.stderr"
import json
from app.db import SessionLocal
from app.models_agent_routing import ConversationControl
from app.operator_models import OperatorQueueScopeGrant
from app.webchat_models import WebchatConversation
with SessionLocal() as db:
    conversation = db.query(WebchatConversation).filter(
        WebchatConversation.public_id == "$CONV_ID"
    ).one()
    control = db.query(ConversationControl).filter(
        ConversationControl.conversation_id == conversation.id
    ).one()
    control.country_code = control.country_code or "ME"
    grant = db.query(OperatorQueueScopeGrant).filter(
        OperatorQueueScopeGrant.user_id == int("$ADMIN_USER_ID"),
        OperatorQueueScopeGrant.tenant_key == control.tenant_key,
        OperatorQueueScopeGrant.country_code == control.country_code,
        OperatorQueueScopeGrant.channel_key == control.channel_key,
    ).first()
    if grant is None:
        grant = OperatorQueueScopeGrant(
            user_id=int("$ADMIN_USER_ID"),
            tenant_key=control.tenant_key,
            country_code=control.country_code,
            channel_key=control.channel_key,
            enabled=True,
            granted_by=int("$ADMIN_USER_ID"),
        )
        db.add(grant)
    else:
        grant.enabled = True
    db.commit()
    print(json.dumps({
        "conversation_id": conversation.public_id,
        "ticket_id": conversation.ticket_id,
        "tenant_key": control.tenant_key,
        "country_code": control.country_code,
        "channel_key": control.channel_key,
    }, ensure_ascii=False))
PY

'''
if "scope_setup.json" not in probe:
    probe = replace_once(
        probe,
        "  VISITOR_TOKEN=\"$(python3 -c 'import json;print(json.load(open(\"'\"$OUT\"'/sensitive/init.json\"))[\"visitor_token\"])')\"\n\n"
        "  curl -sS -m 45 \"$APP_BASE/api/webchat/conversations/$CONV_ID/voice/sessions\" \\\n",
        "  VISITOR_TOKEN=\"$(python3 -c 'import json;print(json.load(open(\"'\"$OUT\"'/sensitive/init.json\"))[\"visitor_token\"])')\"\n\n"
        + scope_setup
        + "  curl -sS -m 45 \"$APP_BASE/api/webchat/conversations/$CONV_ID/voice/sessions\" \\\n",
        label="probe ticketless scope setup",
    )
old_probe_block = '''    TICKET_ID="$("${DC[@]}" run --rm --no-deps -T app python - <<PY 2>/dev/null | tail -n1
from app.db import SessionLocal
from app.webchat_models import WebchatConversation
with SessionLocal() as db:
    c = db.query(WebchatConversation).filter(WebchatConversation.public_id == "$CONV_ID").first()
    print(c.ticket_id)
PY
)"
    curl -sS -m 45 "$APP_BASE/api/webchat/admin/tickets/$TICKET_ID/voice/$VOICE_ID/accept" \\
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \\
      > "$OUT/sensitive/accept_voice.json"
    curl -sS -m 45 "$APP_BASE/api/webchat/admin/tickets/$TICKET_ID/voice/$VOICE_ID/end" \\
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \\
      > "$OUT/e2e/end_voice.json"
    "${DC[@]}" run --rm --no-deps -T app python - <<PY > "$OUT/e2e/evidence_verify.json" 2>"$OUT/e2e/evidence_verify.stderr"
import json
from app.db import SessionLocal
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatMessage, WebchatEvent
with SessionLocal() as db:
    s = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == "$VOICE_ID").first()
    messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == int("$TICKET_ID"), WebchatMessage.message_type == "voice_call").all()
    events = db.query(WebchatEvent).filter(WebchatEvent.ticket_id == int("$TICKET_ID"), WebchatEvent.event_type.like("voice.%")).order_by(WebchatEvent.id.asc()).all()
    print(json.dumps({
        "ticket_id": int("$TICKET_ID"),
        "voice_session_id": "$VOICE_ID",
        "provider": s.provider if s else None,
        "status": s.status if s else None,
        "voice_call_message_count": len(messages),
        "voice_events": [e.event_type for e in events],
    }, ensure_ascii=False, indent=2))
PY
'''
new_probe_block = '''    curl -sS -m 45 "$APP_BASE/api/webchat/admin/voice/$VOICE_ID/accept" \\
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \\
      > "$OUT/sensitive/accept_voice.json"
    curl -sS -m 45 "$APP_BASE/api/webchat/admin/voice/$VOICE_ID/end" \\
      -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' -X POST \\
      > "$OUT/e2e/end_voice.json"
    "${DC[@]}" run --rm --no-deps -T app python - <<PY > "$OUT/e2e/evidence_verify.json" 2>"$OUT/e2e/evidence_verify.stderr"
import json
from app.db import SessionLocal
from app.voice_models import WebchatVoiceSession
from app.webchat_models import WebchatConversation, WebchatMessage, WebchatEvent
with SessionLocal() as db:
    conversation = db.query(WebchatConversation).filter(
        WebchatConversation.public_id == "$CONV_ID"
    ).one()
    session = db.query(WebchatVoiceSession).filter(
        WebchatVoiceSession.public_id == "$VOICE_ID"
    ).one()
    messages = db.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id,
        WebchatMessage.message_type == "voice_call",
    ).all()
    events = db.query(WebchatEvent).filter(
        WebchatEvent.conversation_id == conversation.id,
        WebchatEvent.event_type.like("voice.%"),
    ).order_by(WebchatEvent.id.asc()).all()
    print(json.dumps({
        "ticket_id": session.ticket_id,
        "conversation_id": conversation.public_id,
        "voice_session_id": session.public_id,
        "provider": session.provider,
        "status": session.status,
        "voice_call_message_count": len(messages),
        "voice_events": [event.event_type for event in events],
    }, ensure_ascii=False, indent=2))
PY
'''
probe = replace_once(
    probe,
    old_probe_block,
    new_probe_block,
    label="probe session-first lifecycle",
)
write(probe_path, probe)


# Final fail-closed assertions.
assert "ensure_voice_ticket_for_public_conversation" not in read(
    "backend/app/services/conversation_first_service.py"
)
assert "/api/webchat/admin/tickets/$TICKET_ID/voice/" not in read(probe_path)
assert '"nexus.ai_reply.v3"' not in read(
    "backend/app/services/provider_runtime/output_contracts.py"
)
assert "unknown_tool_not_registered" in read(
    "backend/app/services/tool_governance.py"
)
