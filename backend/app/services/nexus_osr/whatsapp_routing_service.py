from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ...enums import EventType
from ...models import Ticket, TicketEvent
from ...models_osr import WhatsAppRoutingRuleRecord
from .case_context import CaseContext
from .operations_dispatch_outbox import (
    OperationsDispatchStatus,
    audit_reference_payload,
    build_dispatch_key,
    enqueue_operations_dispatch,
    sha256_digest,
)
from .persistence import save_case_context


# Compatibility alias for existing internal callers. Dispatch state now belongs
# to the dedicated outbox, not to TicketEvent payloads.
OSROperationsDispatchStatus = OperationsDispatchStatus

_ACTIVE_OR_DELIVERED_STATUSES = {
    OperationsDispatchStatus.PENDING.value,
    OperationsDispatchStatus.PROCESSING.value,
    OperationsDispatchStatus.RETRYABLE.value,
    OperationsDispatchStatus.DISPATCHED.value,
}


@dataclass(frozen=True)
class WhatsAppRoutingResult:
    routed: bool
    status: str
    case_context: CaseContext
    dispatch_key: str | None = None
    destination_group_key: str | None = None
    attempted_group_key: str | None = None
    fallback_group_key: str | None = None
    fallback_used: bool = False
    dispatch_status: str | None = None
    event_id: int | None = None
    outbox_id: int | None = None
    enqueue_created: bool = False
    message_text: str | None = None


@dataclass(frozen=True)
class _RuleResolution:
    rule: WhatsAppRoutingRuleRecord | None
    scope: str
    disabled_rule: WhatsAppRoutingRuleRecord | None = None


def route_ticket_to_whatsapp_group(
    db: Session,
    *,
    ticket: Ticket,
    case_context: CaseContext,
    routing_channel: str = "whatsapp",
    tenant_id: str = "default",
    dispatcher: Any | None = None,
) -> WhatsAppRoutingResult:
    """Resolve one governed operations route and durably enqueue it.

    `dispatcher` remains an accepted compatibility argument but is deliberately
    never invoked. Provider dispatch belongs to the separately governed outbox
    processor contract. This function does not build or send a message.
    """

    del dispatcher
    tenant_key = _normalize_tenant(tenant_id)
    country_code = _normalize_country(case_context.country_code or getattr(ticket, "country_code", None))
    issue_type = _normalize_issue(case_context.issue_type or getattr(ticket, "case_type", None))
    channel_key = _normalize_channel(routing_channel)

    resolution = _resolve_routing_rule(
        db,
        country_code=country_code,
        issue_type=issue_type,
        channel=channel_key,
    )
    if resolution.rule is None:
        status = "routing_disabled" if resolution.disabled_rule is not None else "routing_not_configured"
        audit_status = (
            OperationsDispatchStatus.CANCELLED.value
            if resolution.disabled_rule is not None
            else OperationsDispatchStatus.FAILED.value
        )
        payload: dict[str, Any] = {
            "event": status,
            "source": "nexus_osr",
            "routing_status": "failed_closed",
            "dispatch_status": audit_status,
            "tenant_key": tenant_key,
            "country_code": country_code,
            "channel_key": channel_key,
            "issue_type": issue_type,
            "routing_scope": resolution.scope,
        }
        if resolution.disabled_rule is not None:
            payload["routing_rule_id"] = resolution.disabled_rule.id
        event = _write_routing_event(
            db,
            ticket=ticket,
            note=f"Nexus OSR WhatsApp routing {status}",
            payload=payload,
        )
        return WhatsAppRoutingResult(
            routed=False,
            status=status,
            case_context=case_context,
            dispatch_status=audit_status,
            event_id=event.id,
        )

    rule = resolution.rule
    raw_destination = _provider_group_id(rule)
    if raw_destination is None:
        event = _write_routing_event(
            db,
            ticket=ticket,
            note="Nexus OSR WhatsApp routing destination invalid",
            payload={
                "event": "routing_destination_invalid",
                "source": "nexus_osr",
                "routing_status": "failed_closed",
                "dispatch_status": OperationsDispatchStatus.FAILED.value,
                "tenant_key": tenant_key,
                "country_code": country_code,
                "channel_key": channel_key,
                "issue_type": issue_type,
                "routing_rule_id": rule.id,
                "routing_scope": resolution.scope,
            },
        )
        return WhatsAppRoutingResult(
            routed=False,
            status="routing_destination_invalid",
            case_context=case_context,
            dispatch_status=OperationsDispatchStatus.FAILED.value,
            event_id=event.id,
        )

    destination_group_key = _destination_group_key(rule)
    destination_group_hash = sha256_digest(raw_destination)
    dispatch_key = build_dispatch_key(
        "operations-dispatch-v1",
        tenant_key,
        getattr(ticket, "id", None),
        case_context.conversation_id,
        case_context.ticket_id,
        country_code,
        issue_type,
        channel_key,
    )
    enqueue = enqueue_operations_dispatch(
        db,
        dispatch_key=dispatch_key,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
        routing_rule_id=rule.id,
        destination_group_key=destination_group_key,
        destination_group_hash=destination_group_hash,
        ticket_id=getattr(ticket, "id", None),
    )
    record = enqueue.record
    routed = record.status in _ACTIVE_OR_DELIVERED_STATUSES
    next_context = case_context
    if routed and case_context.routed_group_key != record.destination_group_key:
        next_context = case_context.mark_routed(record.destination_group_key)
        save_case_context(db, next_context, tenant_id=tenant_key)

    event_id: int | None = None
    if enqueue.created:
        payload = audit_reference_payload(record, event="operations_dispatch_enqueued")
        payload.update(
            {
                "routing_scope": resolution.scope,
                "issue_type": issue_type,
                "routing_status": "durably_enqueued",
            }
        )
        event = _write_routing_event(
            db,
            ticket=ticket,
            note="Nexus OSR operations dispatch enqueued",
            payload=payload,
        )
        event_id = event.id

    return WhatsAppRoutingResult(
        routed=routed,
        status=record.status,
        case_context=next_context,
        dispatch_key=record.dispatch_key,
        destination_group_key=record.destination_group_key,
        attempted_group_key=None,
        fallback_group_key=None,
        fallback_used=False,
        dispatch_status=record.status,
        event_id=event_id,
        outbox_id=record.id,
        enqueue_created=enqueue.created,
        message_text=None,
    )


def _resolve_routing_rule(
    db: Session,
    *,
    country_code: str,
    issue_type: str,
    channel: str,
) -> _RuleResolution:
    """Resolve only within the requested country.

    A disabled rule at an otherwise eligible scope is an explicit fail-closed
    decision. Non-GLOBAL countries never fall back to GLOBAL rules.
    """

    for candidate_country, candidate_issue, scope in _routing_scopes(
        country_code=country_code,
        issue_type=issue_type,
    ):
        enabled = _find_rule(
            db,
            country_code=candidate_country,
            issue_type=candidate_issue,
            channel=channel,
            enabled=True,
        )
        if enabled is not None:
            return _RuleResolution(rule=enabled, scope=scope)
        disabled = _find_rule(
            db,
            country_code=candidate_country,
            issue_type=candidate_issue,
            channel=channel,
            enabled=False,
        )
        if disabled is not None:
            return _RuleResolution(rule=None, disabled_rule=disabled, scope=scope)
    return _RuleResolution(rule=None, scope="no_match")


def _routing_scopes(*, country_code: str, issue_type: str) -> list[tuple[str, str, str]]:
    country = _normalize_country(country_code)
    issue = _normalize_issue(issue_type)
    raw = [
        (country, issue, "exact_country_issue_channel"),
        (country, "general", "country_general_channel"),
    ]
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str, str]] = []
    for candidate_country, candidate_issue, scope in raw:
        key = (candidate_country, candidate_issue)
        if key in seen:
            continue
        seen.add(key)
        result.append((candidate_country, candidate_issue, scope))
    return result


def _find_rule(
    db: Session,
    *,
    country_code: str,
    issue_type: str,
    channel: str,
    enabled: bool,
) -> WhatsAppRoutingRuleRecord | None:
    return (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.enabled.is_(enabled))
        .filter(WhatsAppRoutingRuleRecord.country_code == country_code)
        .filter(WhatsAppRoutingRuleRecord.issue_type == issue_type)
        .filter(WhatsAppRoutingRuleRecord.channel == channel)
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .first()
    )


def _write_routing_event(
    db: Session,
    *,
    ticket: Ticket,
    note: str,
    payload: dict[str, Any],
) -> TicketEvent:
    event = TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        note=note,
        payload_json=json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str),
    )
    db.add(event)
    db.flush()
    return event


def _destination_group_key(rule: WhatsAppRoutingRuleRecord) -> str:
    country = _normalize_country(rule.country_code).lower()
    issue = _normalize_issue(rule.issue_type)
    channel = _normalize_channel(rule.channel)
    return f"{channel}:{country}:{issue}:destination"


def _provider_group_id(rule: WhatsAppRoutingRuleRecord) -> str | None:
    cleaned = str(rule.destination_group_id or "").strip()
    return cleaned or None


def _normalize_tenant(value: Any) -> str:
    cleaned = str(value or "default").strip()
    return cleaned[:80] or "default"


def _normalize_country(value: Any) -> str:
    cleaned = str(value or "GLOBAL").strip().upper()
    return cleaned[:16] or "GLOBAL"


def _normalize_issue(value: Any) -> str:
    cleaned = str(value or "general").strip().lower()
    return cleaned[:120] or "general"


def _normalize_channel(value: Any) -> str:
    cleaned = str(value or "whatsapp").strip().lower()
    return cleaned[:40] or "whatsapp"
