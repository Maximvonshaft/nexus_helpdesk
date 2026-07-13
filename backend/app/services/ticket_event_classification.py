from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..enums import EventType
from .ticket_event_sanitizer import TICKET_EVENT_CONTRACT
from .ticket_event_writer import TicketEventClass


class TicketEventClassificationError(ValueError):
    """Raised when an event cannot be assigned one trustworthy evidence class."""


_UNAMBIGUOUS_EVENT_CLASSES: dict[EventType, TicketEventClass] = {
    EventType.ticket_created: TicketEventClass.INTERNAL_AUDIT,
    EventType.status_changed: TicketEventClass.INTERNAL_AUDIT,
    EventType.assigned: TicketEventClass.INTERNAL_AUDIT,
    EventType.escalated: TicketEventClass.INTERNAL_AUDIT,
    EventType.reopened: TicketEventClass.INTERNAL_AUDIT,
    EventType.attachment_added: TicketEventClass.INTERNAL_AUDIT,
    EventType.outbound_draft_saved: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.outbound_queued: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.outbound_sent: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.outbound_failed: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.outbound_retry_scheduled: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.outbound_dead: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.ai_intake_added: TicketEventClass.PROVIDER,
    EventType.sla_breached: TicketEventClass.INTERNAL_AUDIT,
    EventType.integration_request_received: TicketEventClass.PROVIDER,
    EventType.external_channel_synced: TicketEventClass.PROVIDER,
    EventType.external_channel_reply_sent: TicketEventClass.CUSTOMER_VISIBLE,
    EventType.external_channel_attachment_synced: TicketEventClass.PROVIDER,
    EventType.external_channel_attachment_persisted: TicketEventClass.PROVIDER,
    EventType.conversation_state_changed: TicketEventClass.INTERNAL_AUDIT,
}

_TICKET_CORE_FIELD_NAMES = frozenset(
    {
        "title",
        "description",
        "category",
        "sub_category",
        "resolution_category",
        "case_type",
        "issue_summary",
        "customer_request",
        "source_chat_id",
        "required_action",
        "missing_fields",
        "last_customer_message",
        "customer_update",
        "resolution_summary",
        "last_human_update",
        "requested_time",
        "destination",
        "preferred_reply_channel",
        "preferred_reply_contact",
        "market_id",
        "country_code",
        "priority",
        "qa_agent_appeal",
        "qa_knowledge_gap",
        "tags",
    }
)
_TOOL_KEYS = frozenset(
    {
        "background_job_id",
        "dedupe_key",
        "job_id",
        "tool_call_log_id",
        "tool_name",
        "work_order_id",
    }
)
_DISPATCH_KEYS = frozenset(
    {
        "dispatch",
        "dispatch_key",
        "dispatch_outbox_id",
        "outbox_id",
        "routing",
        "routing_rule_id",
    }
)
_TRACKING_KEYS = frozenset(
    {
        "tracking_number_hash",
        "evidence_type",
        "authority_level",
        "safe_status",
    }
)
_PROVIDER_KEYS = frozenset(
    {
        "ai_turn_id",
        "public_conversation_id",
        "visitor_message_id",
    }
)
_INTERNAL_KEYS = frozenset(
    {
        "changed_fields",
        "fields",
        "handoff_request_id",
        "note_id",
        "operator_task_id",
    }
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _governed_event_class(payload: Mapping[str, Any]) -> TicketEventClass | None:
    if payload.get("event_contract") != TICKET_EVENT_CONTRACT:
        return None
    if payload.get("schema_version") != 1:
        raise TicketEventClassificationError("ticket_event_schema_version_unknown")
    try:
        return TicketEventClass(str(payload.get("event_class") or ""))
    except ValueError as exc:
        raise TicketEventClassificationError("ticket_event_class_unknown") from exc


def _field(value: Any) -> str:
    return str(value or "").strip().lower()


def _note(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())[:240]


def _field_updated_candidates(
    *,
    field_name: str,
    payload: Mapping[str, Any],
    note: str,
) -> set[TicketEventClass]:
    keys = {str(key).lower() for key in payload}
    candidates: set[TicketEventClass] = set()

    if (
        keys & _DISPATCH_KEYS
        or "operations_dispatch" in field_name
        or "dispatch_routing" in field_name
        or "operations dispatch" in note
        or "operations routing" in note
    ):
        candidates.add(TicketEventClass.DISPATCH)

    if (
        keys & _TOOL_KEYS
        or field_name.startswith("speedaf_")
        or field_name.startswith("tool.")
        or "tool execution" in note
    ):
        candidates.add(TicketEventClass.TOOL)

    if (
        keys & _TRACKING_KEYS
        or field_name.startswith("tracking")
        or field_name.startswith("waybill")
    ):
        candidates.add(TicketEventClass.TRACKING)

    if (
        keys & _PROVIDER_KEYS
        or field_name.startswith("provider.")
        or field_name.startswith("ai_runtime.")
    ):
        candidates.add(TicketEventClass.PROVIDER)

    if (
        keys & _INTERNAL_KEYS
        or field_name.startswith("case.")
        or field_name.startswith("ticket.")
        or note.startswith("case ")
        or note.startswith("lite case ")
        or note.startswith("nexus osr auto ticket")
        or note.startswith("nexus osr ticket reused")
    ):
        candidates.add(TicketEventClass.INTERNAL_AUDIT)

    if field_name in _TICKET_CORE_FIELD_NAMES or field_name == "webcall.voice.action":
        candidates.add(TicketEventClass.INTERNAL_AUDIT)
    if field_name.startswith("speedaf_"):
        candidates.add(TicketEventClass.TOOL)
    if (
        not field_name
        and str(payload.get("field") or "").strip().lower() == "conversation_state"
        and "new_value" in payload
    ):
        candidates.add(TicketEventClass.INTERNAL_AUDIT)

    return candidates


def _single_field_update_evidence(
    *,
    field_name: str,
    payload: Mapping[str, Any],
    note: str,
) -> TicketEventClass | None:
    candidates = _field_updated_candidates(
        field_name=field_name,
        payload=payload,
        note=note,
    )
    if len(candidates) > 1:
        raise TicketEventClassificationError("ticket_event_field_update_ambiguous")
    return next(iter(candidates)) if candidates else None


def _internal_note_evidence(
    *,
    field_name: str,
    payload: Mapping[str, Any],
) -> TicketEventClass | None:
    keys = {str(key).lower() for key in payload}
    candidates: set[TicketEventClass] = set()
    if keys & _TOOL_KEYS or field_name.startswith("tool."):
        candidates.add(TicketEventClass.TOOL)
    if keys & _INTERNAL_KEYS or field_name.startswith("note."):
        candidates.add(TicketEventClass.INTERNAL_AUDIT)
    if (
        keys & _PROVIDER_KEYS
        or field_name.startswith("provider.")
        or field_name.startswith("ai_runtime.")
    ):
        candidates.add(TicketEventClass.PROVIDER)
    if len(candidates) > 1:
        raise TicketEventClassificationError("ticket_event_internal_note_ambiguous")
    return next(iter(candidates)) if candidates else None


def _explicit_evidence_class(
    event_type: EventType,
    *,
    field_name: str,
    payload: Mapping[str, Any],
    note: str,
) -> TicketEventClass | None:
    if event_type is EventType.comment_added:
        if field_name == "email.inbound":
            return TicketEventClass.PROVIDER
        visibility = str(payload.get("visibility") or "").strip().lower()
        if visibility == "external":
            return TicketEventClass.CUSTOMER_VISIBLE
        if visibility == "internal":
            return TicketEventClass.INTERNAL_AUDIT
        return None

    if event_type is EventType.internal_note_added:
        return _internal_note_evidence(
            field_name=field_name,
            payload=payload,
        )

    if event_type is EventType.field_updated:
        return _single_field_update_evidence(
            field_name=field_name,
            payload=payload,
            note=note,
        )

    return None


def resolve_ticket_event_class(
    event_type: EventType,
    *,
    field_name: str | None = None,
    payload: Mapping[str, Any] | None = None,
    note: str | None = None,
    explicit: TicketEventClass | None = None,
) -> TicketEventClass:
    """Resolve one durable event class or fail closed on ambiguity.

    The same resolver is used by live generic writes and offline repair. A caller
    may supply an explicit class only as the trusted server-owned decision. For
    historical rows, governed metadata is reusable only when it does not conflict
    with either an unambiguous EventType or one high-confidence evidence signal.
    """

    if not isinstance(event_type, EventType):
        raise TicketEventClassificationError("ticket_event_type_invalid")
    if explicit is not None:
        if not isinstance(explicit, TicketEventClass):
            raise TicketEventClassificationError("ticket_event_explicit_class_invalid")
        return explicit

    source = _mapping(payload)
    normalized_field = _field(field_name)
    normalized_note = _note(note)
    governed = _governed_event_class(source)
    unambiguous = _UNAMBIGUOUS_EVENT_CLASSES.get(event_type)
    if governed is not None:
        if unambiguous is not None:
            if governed is not unambiguous:
                raise TicketEventClassificationError(
                    "ticket_event_class_conflicts_with_type"
                )
            return governed
        evidence = _explicit_evidence_class(
            event_type,
            field_name=normalized_field,
            payload=source,
            note=normalized_note,
        )
        if evidence is None:
            if event_type is EventType.comment_added:
                raise TicketEventClassificationError(
                    "ticket_event_comment_visibility_missing"
                )
            if event_type is EventType.field_updated:
                raise TicketEventClassificationError(
                    "ticket_event_field_update_unclassified"
                )
            if event_type is EventType.internal_note_added:
                raise TicketEventClassificationError(
                    "ticket_event_internal_note_evidence_missing"
                )
        elif governed is not evidence:
            raise TicketEventClassificationError(
                "ticket_event_class_conflicts_with_evidence"
            )
        return governed
    if unambiguous is not None:
        return unambiguous

    if event_type is EventType.comment_added:
        evidence = _explicit_evidence_class(
            event_type,
            field_name=normalized_field,
            payload=source,
            note=normalized_note,
        )
        if evidence is not None:
            return evidence
        raise TicketEventClassificationError("ticket_event_comment_visibility_missing")

    if event_type is EventType.internal_note_added:
        evidence = _internal_note_evidence(
            field_name=normalized_field,
            payload=source,
        )
        return evidence or TicketEventClass.INTERNAL_AUDIT

    if event_type is EventType.field_updated:
        evidence = _single_field_update_evidence(
            field_name=normalized_field,
            payload=source,
            note=normalized_note,
        )
        if evidence is not None:
            return evidence
        raise TicketEventClassificationError("ticket_event_field_update_unclassified")

    raise TicketEventClassificationError("ticket_event_type_unclassified")


__all__ = [
    "TicketEventClassificationError",
    "resolve_ticket_event_class",
]
