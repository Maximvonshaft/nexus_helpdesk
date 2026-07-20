from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def patch_tool_executor_core() -> None:
    path = Path("backend/app/services/nexus_osr/tool_execution_service_core.py")
    text = path.read_text(encoding="utf-8")

    old_import = "from ...webchat_models import WebchatConversation\n"
    new_import = (
        "from ...webchat_models import "
        "WebchatConversation, WebchatHandoffRequest\n"
    )
    if new_import not in text:
        require(old_import in text, "Tool Executor Core WebChat import state is unexpected")
        text = text.replace(old_import, new_import, 1)

    handler_anchor = text.index("def _production_handlers(")
    support_start = text.index("    def support_availability(", handler_anchor)
    support_end = text.index("\n    def ticket_create(", support_start)
    support = text[support_start:support_end]
    if "request_row=request_row" not in support:
        call_start = support.index("        summary = availability_summary(\n")
        call_end = support.index("        return ActionExecutionResult(\n", call_start)
        availability_call = (
            "        request_row = (\n"
            "            db.get(\n"
            "                WebchatHandoffRequest,\n"
            "                current.current_handoff_request_id,\n"
            "            )\n"
            "            if current.current_handoff_request_id is not None\n"
            "            else None\n"
            "        )\n"
            "        if (\n"
            "            request_row is not None\n"
            "            and request_row.conversation_id != current.id\n"
            "        ):\n"
            "            request_row = None\n"
            "        summary = availability_summary(\n"
            "            db,\n"
            "            tenant_key=control.tenant_key,\n"
            "            country_code=control.country_code,\n"
            "            channel_key=control.channel_key,\n"
            "            request_row=request_row,\n"
            "        )\n"
        )
        support = support[:call_start] + availability_call + support[call_end:]
        text = text[:support_start] + support + text[support_end:]

    summary_start = text.index("def _availability_customer_summary(")
    summary_end = text.index("\n\ndef _decision_for_policy_gate(", summary_start)
    summary_impl = dedent(
        '''
        def _availability_customer_summary(summary: dict[str, Any]) -> str:
            online = int(summary.get("online_agents") or 0)
            available = int(summary.get("available_capacity") or 0)
            queued = int(summary.get("queue_count") or 0)
            raw_position = summary.get("queue_position")
            position = int(raw_position) if isinstance(raw_position, int) else None
            if online <= 0:
                return "No human support agent is currently online."
            if available > 0:
                return (
                    "Human support is available with "
                    f"{available} open conversation slot(s)."
                )
            if position is not None and position > 0:
                ahead = max(0, position - 1)
                if ahead == 0:
                    return (
                        "Human support is currently at capacity. "
                        "This customer is next in the eligible queue."
                    )
                return (
                    "Human support is currently at capacity with "
                    f"{ahead} conversation(s) ahead of this customer."
                )
            return (
                "Human support is currently at capacity with "
                f"{queued} conversation(s) waiting."
            )
        '''
    ).strip()
    text = text[:summary_start] + summary_impl + text[summary_end:]
    path.write_text(text, encoding="utf-8")


def patch_voice_ticket_authority() -> None:
    service_path = Path("backend/app/services/conversation_first_service.py")
    service = service_path.read_text(encoding="utf-8")

    if "from ..enums import SourceChannel, TicketPriority\n" not in service:
        marker = "from ..models import Customer, Tenant, Ticket\n"
        require(marker in service, "conversation service model import missing")
        service = service.replace(
            marker,
            "from ..enums import SourceChannel, TicketPriority\n" + marker,
            1,
        )
    if "from ..voice_models import WebchatVoiceSession\n" not in service:
        marker = "from ..utils.time import utc_now\n"
        require(marker in service, "conversation service time import missing")
        service = service.replace(
            marker,
            marker + "from ..voice_models import WebchatVoiceSession\n",
            1,
        )
    if (
        "from .nexus_osr.auto_ticket_service import "
        "create_or_reuse_ticket_from_case_context\n"
    ) not in service:
        marker = (
            "from .tenant_authority import "
            "stamp_runtime_tenant, tenant_runtime_authority_mode\n"
        )
        require(marker in service, "conversation service tenant import missing")
        service = service.replace(
            marker,
            "from .nexus_osr.auto_ticket_service import "
            "create_or_reuse_ticket_from_case_context\n"
            "from .nexus_osr.case_context import CaseContext\n"
            + marker,
            1,
        )

    helper = dedent(
        '''

        def ensure_voice_ticket_for_public_conversation(
            db: Session,
            *,
            conversation_public_id: str,
            visitor_token: str | None,
        ) -> Ticket:
            """Lazily bind explicit voice initiation to the canonical Ticket control plane."""

            query = db.query(WebchatConversation).filter(
                WebchatConversation.public_id == conversation_public_id
            )
            if db.bind and db.bind.dialect.name.startswith("postgresql"):
                query = query.with_for_update()
            conversation = query.first()
            if conversation is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="webchat conversation not found",
                )
            _validate_token(conversation, visitor_token)
            if conversation.ticket_id is not None:
                ticket = db.get(Ticket, conversation.ticket_id)
                if ticket is None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="webchat voice ticket relationship is invalid",
                    )
            else:
                control = ensure_conversation_control(db, conversation=conversation)
                customer = (
                    db.get(Customer, control.customer_id)
                    if control.customer_id is not None
                    else None
                )
                context = CaseContext(
                    conversation_id=conversation.id,
                    ticket_id=None,
                    channel=conversation.channel_key,
                    country_code=control.country_code,
                    issue_type="voice_support",
                ).with_inbound_message(
                    "Customer initiated a live voice support session.",
                    channel=conversation.channel_key,
                    country_code=control.country_code,
                )
                result = create_or_reuse_ticket_from_case_context(
                    db,
                    case_context=context,
                    customer=customer,
                    conversation=conversation,
                    source_channel=SourceChannel.web_chat,
                    title="Live voice support",
                    description="Customer initiated a live voice support session.",
                    priority=TicketPriority.medium,
                    issue_type="voice_support",
                )
                ticket = result.ticket
            db.query(WebchatVoiceSession).filter(
                WebchatVoiceSession.conversation_id == conversation.id,
                WebchatVoiceSession.ticket_id.is_(None),
            ).update(
                {WebchatVoiceSession.ticket_id: ticket.id},
                synchronize_session=False,
            )
            conversation.ticket_id = ticket.id
            conversation.updated_at = utc_now()
            db.flush()
            return ticket
        '''
    )
    if "def ensure_voice_ticket_for_public_conversation(" not in service:
        service = service.rstrip() + helper + "\n"
    service_path.write_text(service, encoding="utf-8")

    api_path = Path("backend/app/api/webchat_voice.py")
    api = api_path.read_text(encoding="utf-8")
    import_line = (
        "from ..services.conversation_first_service import "
        "ensure_voice_ticket_for_public_conversation\n"
    )
    if import_line not in api:
        marker = "from ..webchat_voice_config import load_webchat_voice_runtime_config\n"
        require(marker in api, "voice API config import missing")
        api = api.replace(marker, marker + import_line, 1)
    if "        ensure_voice_ticket_for_public_conversation(\n" not in api:
        anchor = "    with managed_session(db):\n        return create_public_voice_session(\n"
        require(api.count(anchor) == 1, "public voice creation anchor mismatch")
        replacement = (
            "    with managed_session(db):\n"
            "        ensure_voice_ticket_for_public_conversation(\n"
            "            db,\n"
            "            conversation_public_id=conversation_id,\n"
            "            visitor_token=visitor_token,\n"
            "        )\n"
            "        return create_public_voice_session(\n"
        )
        api = api.replace(anchor, replacement, 1)
    api_path.write_text(api, encoding="utf-8")


def patch_tests() -> None:
    routing_path = Path("backend/tests/test_conversation_first_agent_routing.py")
    routing = routing_path.read_text(encoding="utf-8")
    old = '    assert result["ok"] is True\n'
    if old in routing:
        routing = routing.replace(
            old,
            '    assert result["direction"] == "agent"\n'
            '    assert result["body_text"] == '
            '"I am handling this conversation now."\n'
            '    assert result["delivery_status"] == "sent"\n',
            1,
        )
    routing_path.write_text(routing, encoding="utf-8")

    voice_path = Path("backend/tests/test_webchat_voice_api.py")
    voice = voice_path.read_text(encoding="utf-8")
    old_name = "def test_public_create_voice_session_binds_conversation_without_ticket():"
    if old_name in voice:
        start = voice.index(old_name)
        end = voice.index(
            "\n\ndef test_public_create_voice_session_rejects_invalid_token():",
            start,
        )
        replacement = dedent(
            '''
            def test_public_create_voice_session_lazily_binds_canonical_ticket():
                client = TestClient(app)
                conversation_id, visitor_token, ticket_id = _create_webchat_conversation(
                    client,
                    create_ticket=False,
                )
                assert ticket_id is None

                created = client.post(
                    f"/api/webchat/conversations/{conversation_id}/voice/sessions",
                    headers={"X-Webchat-Visitor-Token": visitor_token},
                    json={"locale": "de-CH", "recording_consent": False},
                )

                assert created.status_code == 200, created.text
                payload = created.json()
                assert payload["ok"] is True
                assert payload["voice_session_id"].startswith("wv_")
                assert payload["provider"] == "mock"
                assert payload["status"] == "ringing"
                assert payload["voice_page_url"].endswith(payload["voice_session_id"])
                assert payload["participant_token"].startswith("mock_voice_token_")
                assert "ticket_id" not in payload
                assert payload["recording_status"] == "disabled"
                assert payload["transcript_status"] == "disabled"
                assert payload["summary_status"] == "pending"

                db = SessionLocal()
                try:
                    row = (
                        db.query(WebchatVoiceSession)
                        .filter(
                            WebchatVoiceSession.public_id
                            == payload["voice_session_id"]
                        )
                        .one()
                    )
                    conversation = (
                        db.query(WebchatConversation)
                        .filter(WebchatConversation.public_id == conversation_id)
                        .one()
                    )
                    assert row.ticket_id is not None
                    assert conversation.ticket_id == row.ticket_id
                    assert row.provider == "mock"
                    events = (
                        db.query(WebchatEvent)
                        .filter(
                            WebchatEvent.conversation_id == row.conversation_id,
                            WebchatEvent.ticket_id == row.ticket_id,
                        )
                        .all()
                    )
                    event_types = {event.event_type for event in events}
                    assert "voice.session.created" in event_types
                    assert "voice.session.ringing" in event_types
                finally:
                    db.close()
            '''
        ).strip()
        voice = voice[:start] + replacement + voice[end:]
    voice_path.write_text(voice, encoding="utf-8")


def assert_authorities() -> None:
    public_tool = Path(
        "backend/app/services/nexus_osr/tool_execution_service.py"
    ).read_text(encoding="utf-8")
    require(
        "def execute_controlled_tool_calls(" not in public_tool,
        "public Tool module still defines an executor",
    )
    require(
        "def _production_handlers(" not in public_tool,
        "public Tool module still defines handlers",
    )
    availability = Path(
        "backend/app/services/agent_availability_service.py"
    ).read_text(encoding="utf-8")
    require("ContextVar" not in availability, "availability ContextVar remains")
    require(
        "bind_availability_conversation" not in availability,
        "availability context binder remains",
    )


def main() -> None:
    assert_authorities()
    patch_tool_executor_core()
    patch_voice_ticket_authority()
    patch_tests()
    assert_authorities()


if __name__ == "__main__":
    main()
