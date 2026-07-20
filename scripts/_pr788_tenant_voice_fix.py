from __future__ import annotations

from pathlib import Path
from textwrap import dedent


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


service_old = '''    _validate_token(conversation, visitor_token)
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
'''
service_new = '''    _validate_token(conversation, visitor_token)
    tenant = _relational_tenant(db)
    control = ensure_conversation_control(db, conversation=conversation)
    _assert_resume_scope(
        db,
        conversation=conversation,
        control=control,
        tenant=tenant,
    )
    customer = (
        db.get(Customer, control.customer_id)
        if control.customer_id is not None
        else None
    )
    if conversation.ticket_id is not None:
        ticket = db.get(Ticket, conversation.ticket_id)
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="webchat voice ticket relationship is invalid",
            )
    else:
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
    if tenant is not None:
        if ticket.tenant_id is None:
            stamp_runtime_tenant(ticket, tenant.id)
        elif ticket.tenant_id != tenant.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="webchat_tenant_relationship_conflict",
            )
'''
replace_once(
    "backend/app/services/conversation_first_service.py",
    service_old,
    service_new,
    label="tenant-safe voice ticket authority",
)

path = Path("backend/tests/test_ticketless_voice_ticket_binding.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "    models_osr,\n    operator_models,\n",
    "    models_osr,\n    models_webchat_binding,\n    operator_models,\n",
    1,
)
text = text.replace(
    "from app.models import Customer, Ticket  # noqa: E402\n",
    "from app.models import Customer, Tenant, Ticket  # noqa: E402\n"
    "from app.models_webchat_binding import WebchatPublicOriginBinding  # noqa: E402\n",
    1,
)
text = text.replace(
    "from app.services.webchat_service import _hash_token  # noqa: E402\n",
    "from app.services.tenant_authority import stamp_runtime_tenant  # noqa: E402\n"
    "from app.services.webchat_service import _hash_token  # noqa: E402\n"
    "from app.services.webchat_tenant_binding import (  # noqa: E402\n"
    "    resolve_public_webchat_scope,\n"
    ")\n",
    1,
)
text = text.replace(
    "from sqlalchemy.orm import sessionmaker\n",
    "from sqlalchemy.orm import sessionmaker\n"
    "from starlette.requests import Request\n",
    1,
)

addition = dedent(
    '''

    def _tenant_request() -> Request:
        return Request(
            {
                "type": "http",
                "http_version": "1.1",
                "method": "POST",
                "scheme": "https",
                "path": "/api/webchat/conversations/ticketless-voice-conversation/voice/sessions",
                "raw_path": b"/api/webchat/conversations/ticketless-voice-conversation/voice/sessions",
                "query_string": b"",
                "headers": [(b"origin", b"https://tenant-a.example")],
                "client": ("203.0.113.10", 50000),
                "server": ("testserver", 443),
            }
        )


    def test_voice_ticket_inherits_verified_relational_tenant(db_session):
        tenant = Tenant(
            tenant_key="tenant-a",
            display_name="Tenant A",
            is_active=True,
        )
        binding = WebchatPublicOriginBinding(
            normalized_origin="https://tenant-a.example",
            tenant_key="tenant-a",
            country_code="ME",
            channel_key="webchat",
            display_name="Tenant A widget",
            is_active=True,
        )
        db_session.add_all([tenant, binding])
        db_session.flush()
        resolve_public_webchat_scope(
            db_session,
            request=_tenant_request(),
            requested_tenant_key="default",
            requested_channel_key="default",
            app_env="production",
        )

        token = "tenant-bound-voice-token"
        customer = Customer(
            name="Tenant Voice Visitor",
            external_ref="tenant-voice-visitor",
        )
        stamp_runtime_tenant(customer, tenant.id)
        db_session.add(customer)
        db_session.flush()
        conversation = WebchatConversation(
            public_id="ticketless-voice-conversation",
            visitor_token_hash=_hash_token(token),
            tenant_key="tenant-a",
            channel_key="webchat",
            ticket_id=None,
            visitor_name=customer.name,
            origin="https://tenant-a.example",
            status="open",
        )
        db_session.add(conversation)
        db_session.flush()
        db_session.add(
            ConversationControl(
                conversation_id=conversation.id,
                customer_id=customer.id,
                tenant_key="tenant-a",
                country_code="ME",
                channel_key="webchat",
            )
        )
        db_session.flush()

        first = ensure_voice_ticket_for_public_conversation(
            db_session,
            conversation_public_id=conversation.public_id,
            visitor_token=token,
        )
        second = ensure_voice_ticket_for_public_conversation(
            db_session,
            conversation_public_id=conversation.public_id,
            visitor_token=token,
        )

        assert first.id == second.id
        assert first.tenant_id == tenant.id
        assert first.tenant_assignment_source == "runtime_principal"
        assert first.tenant_assignment_version == "nexus.tenant.runtime_authority.v1"
        assert first.country_code == "ME"
    '''
).rstrip() + "\n"
if "def test_voice_ticket_inherits_verified_relational_tenant(" in text:
    raise SystemExit("tenant-bound voice regression already exists")
path.write_text(text.rstrip() + addition, encoding="utf-8")
