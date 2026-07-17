from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.api import support_conversations as support
from app.api import webchat_admin, webchat_ws
from app.enums import ConversationState, SourceChannel, TicketStatus
from app.services import support_sensitive_access
from app.services.permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
    CAP_WEBCHAT_HANDOFF_RELEASE,
    CAP_WEBCHAT_HANDOFF_RESUME_AI,
)


def _conversation(**overrides):
    values = {
        "public_id": "wc-authority",
        "channel_key": "default",
        "origin": "webchat-demo",
        "visitor_name": "Sensitive Customer",
        "visitor_ref": None,
        "visitor_phone": "+41000001",
        "visitor_email": "private@example.test",
        "updated_at": None,
        "last_seen_at": None,
        "handoff_status": "requested",
        "current_handoff_request_id": 9,
        "active_agent_id": None,
        "active_ai_status": None,
        "active_ai_turn_id": None,
        "active_ai_for_message_id": None,
        "ai_suspended": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ticket(**overrides):
    values = {
        "id": 42,
        "ticket_no": "SUP-42",
        "title": "Support case",
        "source_channel": SourceChannel.web_chat,
        "status": TicketStatus.in_progress,
        "conversation_state": ConversationState.human_review_required,
        "required_action": "review",
        "tracking_number": "CH020000001234",
        "last_customer_message": "Where is my parcel?",
        "updated_at": None,
        "priority": "medium",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_channel_scope_is_applied_before_limit_without_python_post_filter():
    source = inspect.getsource(support.list_support_conversations)

    assert source.index("query = query.filter(_channel_predicate(channel))") < source.index(".limit(limit)")
    assert 'item["channel"] != channel' not in source
    assert "limit * 3" not in source


def test_session_key_channel_alias_fails_closed(monkeypatch):
    db = Mock()
    query = db.query.return_value.join.return_value.filter.return_value
    scoped = Mock()
    scoped.first.return_value = (_conversation(), _ticket())
    monkeypatch.setattr(support, "apply_support_ticket_scope", lambda query, user, session: scoped)

    with pytest.raises(HTTPException) as exc:
        support._load_conversation(
            db,
            "whatsapp:wc-authority",
            current_user=SimpleNamespace(id=7),
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "support_conversation_not_found"
    query.first.assert_not_called()


def test_action_flags_are_capability_derived():
    conversation = _conversation()
    ticket = _ticket()
    current_user = SimpleNamespace(id=7)

    without_capabilities = support._conversation_out(
        conversation=conversation,
        ticket=ticket,
        last_message=None,
        current_user=current_user,
        capabilities=set(),
    )
    assert without_capabilities["can_accept"] is False
    assert without_capabilities["can_force_takeover"] is False
    assert without_capabilities["can_release"] is False
    assert without_capabilities["can_resume_ai"] is False
    assert without_capabilities["can_reply"] is False

    with_capabilities = support._conversation_out(
        conversation=conversation,
        ticket=ticket,
        last_message=None,
        current_user=current_user,
        capabilities={
            CAP_OUTBOUND_SEND,
            CAP_WEBCHAT_HANDOFF_ACCEPT,
            CAP_WEBCHAT_HANDOFF_FORCE_TAKEOVER,
            CAP_WEBCHAT_HANDOFF_RELEASE,
            CAP_WEBCHAT_HANDOFF_RESUME_AI,
        },
    )
    assert with_capabilities["can_accept"] is True
    assert with_capabilities["can_force_takeover"] is True
    assert with_capabilities["can_resume_ai"] is True
    assert with_capabilities["can_release"] is False
    assert with_capabilities["can_reply"] is False

    replyable = support._conversation_out(
        conversation=_conversation(
            handoff_status="none",
            current_handoff_request_id=None,
        ),
        ticket=ticket,
        last_message=None,
        current_user=current_user,
        capabilities={CAP_OUTBOUND_SEND},
    )
    assert replyable["can_reply"] is True


def test_all_reply_transports_require_outbound_capability():
    support_source = inspect.getsource(support.reply_support_conversation)
    http_source = inspect.getsource(webchat_admin.reply_webchat)
    ws_source = inspect.getsource(webchat_ws._handle_command)

    assert "ensure_can_send_outbound(current_user, db)" in support_source
    assert "ensure_can_send_outbound(current_user, db)" in http_source
    assert "ensure_can_send_outbound(state.current_user, db)" in ws_source
    assert support_source.index("ensure_can_send_outbound") < support_source.index("admin_reply")
    assert http_source.index("ensure_can_send_outbound") < http_source.index("admin_reply")
    assert ws_source.index("ensure_can_send_outbound") < ws_source.index("admin_reply")


def test_thread_route_does_not_requery_or_reimplement_sensitive_state():
    source = inspect.getsource(webchat_admin.get_webchat_thread)

    assert "db.query" not in source
    assert "WebchatConversation" not in source
    assert "ai_snapshot" not in source
    assert "surface=" not in source
    assert "admin_get_thread" in source
    assert "build_support_memory_ledger" in source
    assert "audit_sensitive_support_read" in source


def test_sensitive_access_has_no_path_registry_or_single_value_dispatch():
    source = inspect.getsource(support_sensitive_access)

    assert "Request" not in source
    assert "url.path" not in source
    assert "Literal" not in source
    assert "_ALLOWED_SURFACES" not in source
    assert "classify_sensitive_support_request" not in source
    assert "enforce_sensitive_support_request" not in source
