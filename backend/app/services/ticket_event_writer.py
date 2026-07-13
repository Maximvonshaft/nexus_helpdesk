from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from ..enums import EventType, NoteVisibility
from ..models import TicketComment, TicketEvent
from .ticket_event_payload import (
    merge_ticket_event_operational_references,
    prepare_ticket_event_payload,
)
from .ticket_event_sanitizer import (
    TICKET_EVENT_CONTRACT,
    TicketEventPayloadPolicy,
    sanitize_ticket_event_field_name,
    sanitize_ticket_event_text,
    serialize_ticket_event_payload,
)


class TicketEventWriteError(ValueError):
    """Raised when a caller attempts an invalid durable audit write."""


class TicketEventClass(str, Enum):
    CUSTOMER_VISIBLE = "customer_visible"
    TRACKING = "tracking"
    TOOL = "tool"
    PROVIDER = "provider"
    DISPATCH = "dispatch"
    INTERNAL_AUDIT = "internal_audit"


TicketEventPolicy = TicketEventPayloadPolicy

_COMMON_IDENTIFIERS = frozenset(
    {
        "actor_id",
        "case_context_id",
        "conversation_id",
        "conversation_public_id",
        "event_id",
        "ticket_id",
    }
)
_COMMON_LABELS = frozenset(
    {
        "action",
        "allowed",
        "category",
        "channel",
        "code",
        "country_code",
        "bridge_elapsed_ms",
        "candidate_count",
        "created",
        "current_status",
        "customer_visible_reply",
        "error_code",
        "failure_code",
        "event",
        "fact_evidence_present",
        "failure_reason_code",
        "handoff_required",
        "next_action",
        "ok",
        "operator_projection",
        "origin",
        "outcome",
        "phase",
        "pii_redacted",
        "policy_key",
        "reason_code",
        "reply_source",
        "risk_level",
        "schema",
        "source",
        "status",
        "ticket_status",
        "tool_status",
        "trigger_type",
        "conversation_state",
        "checked_at",
        "visibility",
    }
)


def _policy(
    event_class: TicketEventClass,
    *,
    identifiers: frozenset[str],
    labels: frozenset[str],
    structured: frozenset[str] = frozenset(),
) -> TicketEventPolicy:
    return TicketEventPolicy(
        event_class=event_class.value,
        safe_identifier_keys=_COMMON_IDENTIFIERS | identifiers,
        safe_label_keys=_COMMON_LABELS | labels,
        safe_structured_keys=structured,
    )


_POLICIES: dict[TicketEventClass, TicketEventPolicy] = {
    TicketEventClass.CUSTOMER_VISIBLE: _policy(
        TicketEventClass.CUSTOMER_VISIBLE,
        identifiers=frozenset(
            {
                "comment_id",
                "inbound_message_id",
                "message_id",
                "outbound_message_id",
                "reply_to_message_id",
                "webchat_card_action_id",
                "webchat_message_id",
                "whatsapp_inbound_message_id",
            }
        ),
        labels=frozenset(
            {
                "external_send",
                "message_type",
                "provider_status",
                "reply_channel",
            }
        ),
    ),
    TicketEventClass.TRACKING: _policy(
        TicketEventClass.TRACKING,
        identifiers=frozenset({"audit_id", "tracking_number_hash"}),
        labels=frozenset(
            {
                "authority",
                "authority_level",
                "evidence_type",
                "safe_status",
                "source_type",
                "verified",
            }
        ),
    ),
    TicketEventClass.TOOL: _policy(
        TicketEventClass.TOOL,
        identifiers=frozenset(
            {
                "background_job_id",
                "dedupe_key",
                "external_id",
                "job_id",
                "operator_task_id",
                "tool_call_log_id",
                "work_order_id",
            }
        ),
        labels=frozenset(
            {
                "executed",
                "requires_confirmation",
                "tool_name",
            }
        ),
    ),
    TicketEventClass.PROVIDER: _policy(
        TicketEventClass.PROVIDER,
        identifiers=frozenset(
            {
                "ai_turn_id",
                "audit_id",
                "inbound_message_id",
                "message_id",
                "outbound_message_id",
                "public_conversation_id",
                "visitor_message_id",
            }
        ),
        labels=frozenset(
            {
                "provider",
                "provider_status",
                "route_status",
            }
        ),
    ),
    TicketEventClass.DISPATCH: _policy(
        TicketEventClass.DISPATCH,
        identifiers=frozenset(
            {
                "dispatch_key",
                "dispatch_outbox_id",
                "group_hash",
                "group_key",
                "outbox_id",
                "routing_rule_id",
                "rule_id",
            }
        ),
        labels=frozenset(
            {
                "dispatch_status",
                "route_status",
            }
        ),
        structured=frozenset({"dispatch", "routing"}),
    ),
    TicketEventClass.INTERNAL_AUDIT: _policy(
        TicketEventClass.INTERNAL_AUDIT,
        identifiers=frozenset(
            {
                "ai_turn_id",
                "audit_id",
                "channel_account_id",
                "client_message_id",
                "comment_id",
                "customer_id",
                "handoff_request_id",
                "note_id",
                "operator_task_id",
                "public_conversation_id",
                "routing_rule_id",
                "user_id",
                "visitor_message_id",
                "voice_session_id",
                "webchat_card_action_id",
                "whatsapp_inbound_message_id",
            }
        ),
        labels=frozenset(
            {
                "priority",
                "queue_key",
                "resolution_category",
                "state",
            }
        ),
        structured=frozenset({"case_context_state", "changed_fields"}),
    ),
}


def policy_for_event_class(event_class: TicketEventClass) -> TicketEventPolicy:
    if not isinstance(event_class, TicketEventClass):
        raise TicketEventWriteError("event_class must be a TicketEventClass")
    try:
        return _POLICIES[event_class]
    except KeyError as exc:
        raise TicketEventWriteError("event_class is not registered") from exc


def _require_ticket_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TicketEventWriteError("ticket_id must be a positive integer")
    return value


def _optional_actor_id(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TicketEventWriteError("actor_id must be a positive integer or None")
    return value


def _require_event_type(value: Any) -> EventType:
    if not isinstance(value, EventType):
        raise TicketEventWriteError("event_type must be an EventType")
    return value


def _comment_visibility_value(value: Any) -> str | None:
    if isinstance(value, NoteVisibility):
        return value.value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {
            NoteVisibility.external.value,
            NoteVisibility.internal.value,
        }:
            return normalized
    return None


def _comment_authority_for_pending_row(
    db: Session,
    *,
    ticket_id: int,
    actor_id: int | None,
    event_type: EventType,
    event_class: TicketEventClass,
    payload: Any,
) -> tuple[TicketEventClass, Any]:
    """Derive a new comment event from the server-owned relational row.

    Public WebChat and WhatsApp callers add one TicketComment and immediately
    append its TicketEvent in the same transaction. The comment visibility is
    authoritative; a caller-provided class cannot downgrade an external
    comment to internal audit. The helper flushes only to materialize relational
    identifiers and never commits or rolls back.
    """

    if event_type != EventType.comment_added:
        return event_class, payload

    pending_comments = [
        row
        for row in list(getattr(db, "new", ()))
        if isinstance(row, TicketComment) and row.ticket_id == ticket_id
    ]
    if not pending_comments:
        return event_class, payload
    if len(pending_comments) != 1:
        raise TicketEventWriteError("ticket_event_comment_authority_ambiguous")

    comment = pending_comments[0]
    if comment.author_id != actor_id:
        raise TicketEventWriteError("ticket_event_comment_actor_mismatch")

    visibility = _comment_visibility_value(comment.visibility)
    if visibility == NoteVisibility.external.value:
        resolved_class = TicketEventClass.CUSTOMER_VISIBLE
    elif visibility == NoteVisibility.internal.value:
        resolved_class = TicketEventClass.INTERNAL_AUDIT
    else:
        raise TicketEventWriteError("ticket_event_comment_visibility_invalid")

    db.flush()

    if not isinstance(payload, Mapping):
        return resolved_class, payload

    normalized_payload = dict(payload)
    normalized_payload["comment_id"] = comment.id
    normalized_payload["visibility"] = visibility

    message_id = normalized_payload.get("webchat_message_id")
    if isinstance(message_id, int) and not isinstance(message_id, bool):
        from ..webchat_models import WebchatMessage

        message = (
            db.query(WebchatMessage)
            .filter(WebchatMessage.id == message_id)
            .one_or_none()
        )
        if message is None or message.ticket_id != ticket_id:
            raise TicketEventWriteError("ticket_event_comment_message_mismatch")
        normalized_payload["conversation_id"] = message.conversation_id

    action_id = normalized_payload.get("webchat_card_action_id")
    if isinstance(action_id, int) and not isinstance(action_id, bool):
        from ..webchat_models import WebchatCardAction

        action = (
            db.query(WebchatCardAction)
            .filter(WebchatCardAction.id == action_id)
            .one_or_none()
        )
        if action is None or action.ticket_id != ticket_id:
            raise TicketEventWriteError("ticket_event_comment_action_mismatch")
        normalized_payload["conversation_id"] = action.conversation_id

    return resolved_class, normalized_payload


class TicketEventWriter:
    """Authoritative production creation boundary for durable TicketEvent rows.

    The caller retains transaction ownership. `add` deliberately flushes to make
    generated identifiers available but never commits or rolls back.
    """

    @classmethod
    def build(
        cls,
        *,
        ticket_id: int,
        event_type: EventType,
        event_class: TicketEventClass,
        actor_id: int | None = None,
        field_name: str | None = None,
        old_value: Any = None,
        new_value: Any = None,
        note: Any = None,
        payload: Any = None,
        created_at: datetime | None = None,
    ) -> TicketEvent:
        policy = policy_for_event_class(event_class)
        payload_source = {} if payload is None else payload
        prepared_payload, approved_references = prepare_ticket_event_payload(
            payload_source,
            event_class=event_class.value,
        )
        serialized_payload = serialize_ticket_event_payload(
            prepared_payload,
            policy=policy,
        )
        serialized_payload = merge_ticket_event_operational_references(
            serialized_payload,
            approved_references,
            max_bytes=policy.max_bytes,
        )
        kwargs: dict[str, Any] = {
            "ticket_id": _require_ticket_id(ticket_id),
            "actor_id": _optional_actor_id(actor_id),
            "event_type": _require_event_type(event_type),
            "field_name": sanitize_ticket_event_field_name(field_name),
            "old_value": sanitize_ticket_event_text(old_value, limit=500),
            "new_value": sanitize_ticket_event_text(new_value, limit=500),
            "note": sanitize_ticket_event_text(note, limit=1000),
            "payload_json": serialized_payload,
        }
        if created_at is not None:
            if not isinstance(created_at, datetime):
                raise TicketEventWriteError("created_at must be a datetime or None")
            kwargs["created_at"] = created_at
        return TicketEvent(**kwargs)

    @classmethod
    def add(
        cls,
        db: Session,
        **kwargs: Any,
    ) -> TicketEvent:
        if (
            db is None
            or not callable(getattr(db, "add", None))
            or not callable(getattr(db, "flush", None))
        ):
            raise TicketEventWriteError("db must provide add() and flush()")

        build_kwargs = dict(kwargs)
        ticket_id = _require_ticket_id(build_kwargs.get("ticket_id"))
        actor_id = _optional_actor_id(build_kwargs.get("actor_id"))
        event_type = _require_event_type(build_kwargs.get("event_type"))
        event_class = build_kwargs.get("event_class")
        policy_for_event_class(event_class)

        resolved_class, resolved_payload = _comment_authority_for_pending_row(
            db,
            ticket_id=ticket_id,
            actor_id=actor_id,
            event_type=event_type,
            event_class=event_class,
            payload=build_kwargs.get("payload"),
        )
        build_kwargs["ticket_id"] = ticket_id
        build_kwargs["actor_id"] = actor_id
        build_kwargs["event_type"] = event_type
        build_kwargs["event_class"] = resolved_class
        build_kwargs["payload"] = resolved_payload

        row = cls.build(**build_kwargs)
        db.add(row)
        db.flush()
        return row


__all__ = [
    "TICKET_EVENT_CONTRACT",
    "TicketEventClass",
    "TicketEventPolicy",
    "TicketEventWriteError",
    "TicketEventWriter",
    "policy_for_event_class",
]
