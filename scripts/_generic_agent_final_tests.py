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


handoff_test_path = "backend/tests/test_webchat_handoff_control.py"
handoff = read(handoff_test_path)

fallback_test = '''def test_whatsapp_runtime_failure_queues_customer_visible_fallback(db_session, monkeypatch):
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    turn, _job = attach_open_ai_turn(db_session, conversation, ticket, message)
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, "webchat_ai_auto_reply_mode", "runtime")

    async def fake_generate_webchat_runtime_reply(**_kwargs):
        return WebchatRuntimeReplyResult(
            ok=False,
            ai_generated=False,
            reply_source=None,
            reply=None,
            intent=None,
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=8,
            error_code="all_providers_failed",
        )

    monkeypatch.setattr(webchat_ai_service, "generate_webchat_runtime_reply", fake_generate_webchat_runtime_reply)

    result = webchat_ai_orchestration_service.process_webchat_ai_reply_job(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=message.id,
    )

    assert result["status"] == "done"
    assert result["fallback"] is True
    assert result["fallback_reason"] == "all_providers_failed"
    assert turn.status == "completed"
    agent_message = db_session.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id,
        WebchatMessage.direction == "agent",
    ).one()
    assert agent_message.body
    outbound = db_session.query(TicketOutboundMessage).filter(
        TicketOutboundMessage.ticket_id == ticket.id
    ).one()
    assert outbound.status == MessageStatus.pending
    assert outbound.body == agent_message.body'''
handoff = replace_function(
    handoff,
    "test_whatsapp_runtime_failure_records_null_reply_without_customer_visible_fallback",
    fallback_test,
)

success_test = '''def test_whatsapp_ai_reply_queues_native_outbound(db_session, monkeypatch):
    ticket, conversation, message, _account = make_whatsapp_webchat(db_session)
    message.body = "Can you help me check this later?"
    message.body_text = message.body
    turn, _job = attach_open_ai_turn(db_session, conversation, ticket, message)
    monkeypatch.setattr(webchat_ai_orchestration_service.settings, "webchat_ai_auto_reply_mode", "runtime")

    async def fake_generate_webchat_runtime_reply(**_kwargs):
        return WebchatRuntimeReplyResult(
            ok=True,
            ai_generated=True,
            reply_source="private_ai_runtime",
            reply="Hi, I can help with the information available here.",
            intent="general_support",
            handoff_required=False,
            handoff_reason=None,
            recommended_agent_action=None,
            elapsed_ms=42,
            runtime_trace={"agent_runtime": True},
            tool_calls=[],
        )

    monkeypatch.setattr(webchat_ai_service, "generate_webchat_runtime_reply", fake_generate_webchat_runtime_reply)

    result = webchat_ai_orchestration_service.process_webchat_ai_reply_job(
        db_session,
        conversation_id=conversation.id,
        ticket_id=ticket.id,
        visitor_message_id=message.id,
    )

    assert result["status"] == "done"
    assert result["fallback"] is False
    assert turn.status == "completed"
    agent_message = db_session.query(WebchatMessage).filter(
        WebchatMessage.conversation_id == conversation.id,
        WebchatMessage.direction == "agent",
    ).one()
    assert agent_message.delivery_status == "queued"
    outbound = db_session.query(TicketOutboundMessage).filter(
        TicketOutboundMessage.ticket_id == ticket.id
    ).one()
    assert outbound.channel == SourceChannel.whatsapp
    assert outbound.status == MessageStatus.pending
    assert outbound.provider_status == "whatsapp_ai_reply_queued"
    assert outbound.body == "Hi, I can help with the information available here."
    assert outbound.max_retries == message_dispatch.settings.outbox_max_retries
    assert outbound.provider_message_id == f"nexusdesk-outbound-{outbound.id}"'''
handoff = replace_function(handoff, "test_whatsapp_ai_reply_queues_native_outbound", success_test)
if re.search(r"^def test_whatsapp_ai_runtime_failure_records_null_reply_without_outbound\(", handoff, flags=re.MULTILINE):
    handoff = remove_function(handoff, "test_whatsapp_ai_runtime_failure_records_null_reply_without_outbound")
write(handoff_test_path, handoff)

osr_legacy_test = ROOT / "backend/tests/test_webchat_osr_audit_integration.py"
if osr_legacy_test.exists():
    osr_legacy_test.unlink()

residue_path = "scripts/ci/check_agent_runtime_residue.py"
residue = read(residue_path)
for retired in (
    '    ROOT / "backend/tests/test_ticketless_voice_ticket_binding.py",\n',
    '    ROOT / "backend/tests/test_webchat_osr_audit_integration.py",\n',
):
    if retired not in residue:
        residue = residue.replace(
            '    ROOT / "backend/tests/test_runtime_context_guard.py",\n',
            '    ROOT / "backend/tests/test_runtime_context_guard.py",\n' + retired,
            1,
        )
for marker in (
    '    "build_webchat_runtime_context",\n',
    '    "ensure_voice_ticket_for_public_conversation",\n',
    '    "ProviderTrafficPath.SHADOW_ONLY",\n',
    '    "provider_shadow_only",\n',
    '    "shadow_bucket_selected",\n',
):
    if marker not in residue:
        residue = residue.replace(
            '    "def build_runtime_context_guard(",\n',
            '    "def build_runtime_context_guard(",\n' + marker,
            1,
        )
write(residue_path, residue)

architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
architecture = architecture.replace("def build_webchat_runtime_context", "def build_agent_context")
architecture = architecture.replace("build_webchat_runtime_context", "build_agent_context")
extra_tests = '''


def test_provider_accepts_only_the_agent_turn_model_contract() -> None:
    source = Path(
        "backend/app/services/provider_runtime/output_contracts.py"
    ).read_text(encoding="utf-8")
    assert "nexus.agent_turn.v1" in source
    assert "nexus.ai_reply.v3" not in source
    assert "WEBCHAT_RUNTIME_OUTPUT_CONTRACT" not in source


def test_voice_control_plane_is_session_first_and_never_auto_creates_ticket() -> None:
    api = Path("backend/app/api/webchat_voice.py").read_text(encoding="utf-8")
    service = Path("backend/app/services/webchat_voice_service.py").read_text(encoding="utf-8")
    conversation = Path(
        "backend/app/services/conversation_first_service.py"
    ).read_text(encoding="utf-8")
    assert '"/admin/voice/{voice_session_id}/accept"' in api
    assert '"/admin/tickets/{ticket_id}/voice/{voice_session_id}/accept"' not in api
    assert "ensure_voice_ticket_for_public_conversation" not in api
    assert "ensure_voice_ticket_for_public_conversation" not in conversation
    assert "_visible_voice_session_context" in service


def test_provider_runtime_has_no_non_authoritative_shadow_execution() -> None:
    traffic = Path(
        "backend/app/services/provider_runtime/traffic_selection.py"
    ).read_text(encoding="utf-8")
    router = Path(
        "backend/app/services/provider_runtime/router.py"
    ).read_text(encoding="utf-8")
    assert "SHADOW_ONLY" not in traffic
    assert '"shadow"' not in traffic.split("_VALID_MODES", 1)[1].split("}", 1)[0]
    assert "provider_shadow_only" not in router
    assert "shadow_candidate_executed" not in router
'''
if "test_provider_accepts_only_the_agent_turn_model_contract" not in architecture:
    architecture = architecture.rstrip() + extra_tests
write(architecture_path, architecture)

voice_api_path = "backend/app/api/webchat_voice.py"
conversation_path = "backend/app/services/conversation_first_service.py"
router_path = "backend/app/services/provider_runtime/router.py"
output_contract_path = "backend/app/services/provider_runtime/output_contracts.py"
assert "ensure_voice_ticket_for_public_conversation" not in read(voice_api_path)
assert "ensure_voice_ticket_for_public_conversation" not in read(conversation_path)
assert "ProviderTrafficPath.SHADOW_ONLY" not in read(router_path)
assert "nexus.ai_reply.v3" not in read(output_contract_path)
assert "build_webchat_runtime_context" not in "\n".join(
    path.read_text(encoding="utf-8")
    for path in (ROOT / "backend" / "app").rglob("*.py")
)
