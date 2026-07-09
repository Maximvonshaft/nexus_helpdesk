from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from sqlalchemy.orm import Session

from ...enums import EventType
from ...models import Ticket, TicketEvent
from ...models_osr import WhatsAppRoutingRuleRecord
from .case_context import CaseContext, redact_case_text
from .persistence import resolve_whatsapp_routing_rule, save_case_context


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_PHONE_RE = re.compile(r"(?<!\w)\+?\d[\d\s().-]{7,}\d(?!\w)")
_TRACKING_RE = re.compile(r"\b(?=[A-Z0-9-]{8,35}\b)(?=[A-Z0-9-]*\d)[A-Z0-9][A-Z0-9-]*[A-Z0-9]\b", re.IGNORECASE)
_ADDRESS_RE = re.compile(
    r"\b(?:address|addr|street|st\.|road|rd\.|avenue|ave\.|postcode|postal code|zip|地址)\b[:：]?\s+[^\n;|]{3,120}",
    re.IGNORECASE,
)

_DEFAULT_TEMPLATE = """Nexus OSR operations route
Ticket: {ticket_no}
Issue: {issue_type}
Country: {country_code}
Tracking: {safe_tracking_reference}
Customer claim: {customer_claim_summary}
Missing info: {missing_info}
Case status: {case_status}"""

_IDEMPOTENT_DISPATCH_STATUSES = {"pending", "dispatched", "retryable", "fallback_used"}


class OSROperationsDispatchStatus(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    FAILED = "failed"
    RETRYABLE = "retryable"
    CANCELLED = "cancelled"
    FALLBACK_USED = "fallback_used"


@dataclass(frozen=True)
class OSROperationsDispatchQueueItem:
    """Internal operations-dispatch contract persisted through TicketEvent.

    This is not a native WhatsApp sidecar contract. Provider group identifiers
    remain sensitive implementation details; audit surfaces use group keys and
    provider id hashes only.
    """

    dispatch_key: str
    status: OSROperationsDispatchStatus
    destination_group_key: str | None
    provider_group_id_hash: str | None
    fallback_group_key: str | None = None
    fallback_provider_group_id_hash: str | None = None
    attempted_group_key: str | None = None
    fallback_used: bool = False
    retryable: bool = False
    external_message_id: str | None = None
    error_code: str | None = None

    def as_event_payload(self) -> dict[str, Any]:
        return {
            "dispatch_key": self.dispatch_key,
            "dispatch_status": str(self.status),
            "destination_group_key": self.destination_group_key,
            "provider_group_id_hash": self.provider_group_id_hash,
            "fallback_group_key": self.fallback_group_key,
            "fallback_provider_group_id_hash": self.fallback_provider_group_id_hash,
            "attempted_group_key": self.attempted_group_key,
            "fallback_used": self.fallback_used,
            "retryable": self.retryable,
            "external_message_id": self.external_message_id,
            "error_code": self.error_code,
        }


@dataclass(frozen=True)
class WhatsAppDispatchResult:
    ok: bool
    status: str
    external_message_id: str | None = None
    error_code: str | None = None
    retryable: bool = False


class WhatsAppGroupDispatcher(Protocol):
    """Narrow group-dispatch protocol for future safe adapters.

    Production code may inject an implementation later. Without one this service
    records a pending operations-dispatch event and does not call any WhatsApp
    sidecar API or customer-visible outbound path.
    """

    def send_group_message(self, *, provider_group_id: str, message: str, metadata: dict[str, Any]) -> WhatsAppDispatchResult | dict[str, Any] | bool:
        ...


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
    dispatcher: WhatsAppGroupDispatcher | None = None,
) -> WhatsAppRoutingResult:
    """Route an OSR case to an operations WhatsApp group.

    This service is rule-driven and audit-first. If a safe operations-group
    dispatcher is not injected, it writes a pending dispatch TicketEvent and
    marks the CaseContext routed to the business `destination_group_key`, but it
    does not call any WhatsApp sidecar API.
    """

    country_code = _normalize_country(case_context.country_code or getattr(ticket, "country_code", None))
    issue_type = _normalize_issue(case_context.issue_type or getattr(ticket, "case_type", None))
    channel = _normalize_channel(routing_channel)
    dispatch_key = _dispatch_key(ticket=ticket, case_context=case_context, country_code=country_code, issue_type=issue_type, routing_channel=channel)

    existing = _find_existing_dispatch(db, ticket=ticket, dispatch_key=dispatch_key)
    if existing is not None:
        event, payload = existing
        status = str(payload.get("dispatch_status") or "pending")
        return WhatsAppRoutingResult(
            routed=True,
            status=status,
            case_context=case_context,
            dispatch_key=dispatch_key,
            destination_group_key=payload.get("destination_group_key"),
            attempted_group_key=payload.get("attempted_group_key"),
            fallback_group_key=payload.get("fallback_group_key"),
            fallback_used=bool(payload.get("fallback_used")),
            dispatch_status=status,
            event_id=event.id,
        )

    resolution = _resolve_routing_rule(db, country_code=country_code, issue_type=issue_type, channel=channel)
    if resolution.rule is None:
        status = "routing_disabled" if resolution.disabled_rule is not None else "routing_not_configured"
        queue_status = OSROperationsDispatchStatus.CANCELLED if resolution.disabled_rule is not None else OSROperationsDispatchStatus.FAILED
        payload = _base_event_payload(
            ticket=ticket,
            case_context=case_context,
            country_code=country_code,
            issue_type=issue_type,
            routing_channel=channel,
            event=status,
            dispatch_key=dispatch_key,
        )
        payload.update(OSROperationsDispatchQueueItem(
            dispatch_key=dispatch_key,
            status=queue_status,
            destination_group_key=None,
            provider_group_id_hash=None,
            retryable=False,
            error_code=status,
        ).as_event_payload())
        payload["routing_scope"] = resolution.scope
        if resolution.disabled_rule is not None:
            payload["routing_rule_id"] = resolution.disabled_rule.id
        event = _write_routing_event(db, ticket=ticket, note=f"Nexus OSR WhatsApp routing {status}", payload=payload)
        return WhatsAppRoutingResult(routed=False, status=status, case_context=case_context, dispatch_key=dispatch_key, dispatch_status=str(queue_status), event_id=event.id)

    rule = resolution.rule
    message = build_safe_group_message(ticket=ticket, case_context=case_context, rule=rule)
    provider_group_id = _provider_group_id(rule, fallback=False)
    fallback_provider_group_id = _provider_group_id(rule, fallback=True)
    destination_group_key = _destination_group_key(rule, fallback=False)
    fallback_group_key = _destination_group_key(rule, fallback=True) if fallback_provider_group_id else None
    metadata = _dispatch_metadata(
        ticket=ticket,
        case_context=case_context,
        rule=rule,
        routing_channel=channel,
        dispatch_key=dispatch_key,
        destination_group_key=destination_group_key,
    )

    if dispatcher is None:
        next_context = case_context.mark_routed(destination_group_key)
        save_case_context(db, next_context, tenant_id=tenant_id)
        queue = OSROperationsDispatchQueueItem(
            dispatch_key=dispatch_key,
            status=OSROperationsDispatchStatus.PENDING,
            destination_group_key=destination_group_key,
            provider_group_id_hash=_hash_provider_group_id(provider_group_id),
            fallback_group_key=fallback_group_key,
            fallback_provider_group_id_hash=_hash_provider_group_id(fallback_provider_group_id),
            attempted_group_key=destination_group_key,
        )
        payload = _base_event_payload(
            ticket=ticket,
            case_context=next_context,
            country_code=country_code,
            issue_type=issue_type,
            routing_channel=channel,
            event="operations_dispatch_pending",
            dispatch_key=dispatch_key,
        )
        payload.update(queue.as_event_payload())
        payload.update({
            "routing_rule_id": rule.id,
            "routing_scope": resolution.scope,
            "dispatch_mode": "pending_no_operations_dispatcher",
            "message_sha256": _sha256(message),
            "message_preview": _message_preview(message),
        })
        event = _write_routing_event(db, ticket=ticket, note="Nexus OSR operations dispatch pending", payload=payload)
        return WhatsAppRoutingResult(
            routed=True,
            status=str(OSROperationsDispatchStatus.PENDING),
            case_context=next_context,
            dispatch_key=dispatch_key,
            destination_group_key=destination_group_key,
            attempted_group_key=destination_group_key,
            fallback_group_key=fallback_group_key,
            dispatch_status=str(OSROperationsDispatchStatus.PENDING),
            event_id=event.id,
            message_text=message,
        )

    primary = _send(dispatcher, provider_group_id=provider_group_id, message=message, metadata=metadata)
    target_group_key = destination_group_key
    target_provider_group_id = provider_group_id
    fallback_used = False
    dispatch = primary
    if not primary.ok and fallback_provider_group_id:
        fallback_used = True
        target_group_key = fallback_group_key or destination_group_key
        target_provider_group_id = fallback_provider_group_id
        fallback_metadata = {**metadata, "fallback_for_group_key": destination_group_key, "destination_group_key": target_group_key}
        dispatch = _send(dispatcher, provider_group_id=fallback_provider_group_id, message=message, metadata=fallback_metadata)

    routed = dispatch.ok
    queue_status = _queue_status_from_dispatch(dispatch, fallback_used=fallback_used)
    next_context = case_context.mark_routed(target_group_key) if routed else case_context
    if routed:
        save_case_context(db, next_context, tenant_id=tenant_id)

    queue = OSROperationsDispatchQueueItem(
        dispatch_key=dispatch_key,
        status=queue_status,
        destination_group_key=destination_group_key,
        provider_group_id_hash=_hash_provider_group_id(provider_group_id),
        fallback_group_key=fallback_group_key,
        fallback_provider_group_id_hash=_hash_provider_group_id(fallback_provider_group_id),
        attempted_group_key=target_group_key,
        fallback_used=fallback_used,
        retryable=dispatch.retryable,
        external_message_id=dispatch.external_message_id,
        error_code=dispatch.error_code,
    )
    payload = _base_event_payload(
        ticket=ticket,
        case_context=next_context,
        country_code=country_code,
        issue_type=issue_type,
        routing_channel=channel,
        event=f"operations_dispatch_{queue_status}",
        dispatch_key=dispatch_key,
    )
    payload.update(queue.as_event_payload())
    payload.update({
        "routing_rule_id": rule.id,
        "routing_scope": resolution.scope,
        "dispatch_result_status": dispatch.status,
        "message_sha256": _sha256(message),
        "message_preview": _message_preview(message),
        "attempted_provider_group_id_hash": _hash_provider_group_id(target_provider_group_id),
    })
    event = _write_routing_event(db, ticket=ticket, note=f"Nexus OSR operations dispatch {queue_status}", payload=payload)
    return WhatsAppRoutingResult(
        routed=routed,
        status=str(queue_status),
        case_context=next_context,
        dispatch_key=dispatch_key,
        destination_group_key=destination_group_key,
        attempted_group_key=target_group_key,
        fallback_group_key=fallback_group_key,
        fallback_used=fallback_used,
        dispatch_status=str(queue_status),
        event_id=event.id,
        message_text=message,
    )


def build_safe_group_message(*, ticket: Ticket, case_context: CaseContext, rule: WhatsAppRoutingRuleRecord) -> str:
    fields = _safe_template_fields(ticket=ticket, case_context=case_context)
    template = (rule.message_template or _DEFAULT_TEMPLATE).strip() or _DEFAULT_TEMPLATE
    return _redact_for_group(_render_template(template, fields), limit=1200)


def _resolve_routing_rule(db: Session, *, country_code: str, issue_type: str, channel: str) -> _RuleResolution:
    scopes = _routing_scopes(country_code=country_code, issue_type=issue_type)
    for index, (candidate_country, candidate_issue, scope) in enumerate(scopes):
        if index == 0:
            rule = resolve_whatsapp_routing_rule(db, country_code=candidate_country, issue_type=candidate_issue, channel=channel)
        else:
            rule = _find_enabled_rule(db, country_code=candidate_country, issue_type=candidate_issue, channel=channel)
        if rule is not None:
            return _RuleResolution(rule=rule, scope=scope)
        disabled = _find_disabled_rule(db, country_code=candidate_country, issue_type=candidate_issue, channel=channel)
        if disabled is not None:
            return _RuleResolution(rule=None, disabled_rule=disabled, scope=scope)
    return _RuleResolution(rule=None, scope="no_match")


def _routing_scopes(*, country_code: str, issue_type: str) -> list[tuple[str, str, str]]:
    raw = [
        (country_code, issue_type, "exact_country_issue_channel"),
        (country_code, "general", "country_general_channel"),
        ("GLOBAL", issue_type, "global_issue_channel"),
        ("GLOBAL", "general", "global_general_channel"),
    ]
    seen: set[tuple[str, str]] = set()
    result: list[tuple[str, str, str]] = []
    for country, issue, scope in raw:
        key = (_normalize_country(country), _normalize_issue(issue))
        if key in seen:
            continue
        seen.add(key)
        result.append((key[0], key[1], scope))
    return result


def _find_enabled_rule(db: Session, *, country_code: str, issue_type: str, channel: str) -> WhatsAppRoutingRuleRecord | None:
    return (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.enabled.is_(True))
        .filter(WhatsAppRoutingRuleRecord.country_code == country_code)
        .filter(WhatsAppRoutingRuleRecord.issue_type == issue_type)
        .filter(WhatsAppRoutingRuleRecord.channel == channel)
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .first()
    )


def _find_disabled_rule(db: Session, *, country_code: str, issue_type: str, channel: str) -> WhatsAppRoutingRuleRecord | None:
    return (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.enabled.is_(False))
        .filter(WhatsAppRoutingRuleRecord.country_code == country_code)
        .filter(WhatsAppRoutingRuleRecord.issue_type == issue_type)
        .filter(WhatsAppRoutingRuleRecord.channel == channel)
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .first()
    )


def _find_existing_dispatch(db: Session, *, ticket: Ticket, dispatch_key: str) -> tuple[TicketEvent, dict[str, Any]] | None:
    rows = (
        db.query(TicketEvent)
        .filter(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.id.desc())
        .all()
    )
    for row in rows:
        try:
            payload = json.loads(row.payload_json or "{}")
        except Exception:
            continue
        if payload.get("dispatch_key") != dispatch_key:
            continue
        status = str(payload.get("dispatch_status") or "")
        if status in _IDEMPOTENT_DISPATCH_STATUSES:
            return row, payload
    return None


def _safe_template_fields(*, ticket: Ticket, case_context: CaseContext) -> dict[str, str]:
    issue_type = _normalize_issue(case_context.issue_type or getattr(ticket, "case_type", None))
    country_code = _normalize_country(case_context.country_code or getattr(ticket, "country_code", None))
    missing = ", ".join(str(item) for item in (case_context.missing_info or []) if item) or "none"
    return {
        "ticket_no": _redact_for_group(getattr(ticket, "ticket_no", None) or f"ticket:{getattr(ticket, 'id', '')}", limit=120),
        "issue_type": _redact_for_group(issue_type, limit=120),
        "country_code": _redact_for_group(country_code, limit=40),
        "safe_tracking_reference": _redact_for_group(case_context.safe_tracking_reference or "not provided", limit=120),
        "customer_claim_summary": _redact_for_group(case_context.customer_claim_summary or "not provided", limit=300),
        "missing_info": _redact_for_group(missing, limit=200),
        "case_status": _redact_for_group(str(case_context.status), limit=80),
    }


def _render_template(template: str, fields: dict[str, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        return fields.get(key, "[unavailable]")

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, template)


def _redact_for_group(value: Any, *, limit: int) -> str:
    text = redact_case_text(value, limit=limit)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _TRACKING_RE.sub(lambda match: _safe_tracking_token(match.group(0)), text)
    text = _ADDRESS_RE.sub("[redacted_address]", text)
    return text[:limit]


def _safe_tracking_token(token: str) -> str:
    cleaned = re.sub(r"[^A-Z0-9]", "", str(token or "").upper())
    if len(cleaned) >= 6:
        return f"tracking ending {cleaned[-6:]}"
    return "tracking reference provided"


def _send(dispatcher: WhatsAppGroupDispatcher, *, provider_group_id: str, message: str, metadata: dict[str, Any]) -> WhatsAppDispatchResult:
    try:
        raw = dispatcher.send_group_message(provider_group_id=provider_group_id, message=message, metadata=metadata)
    except Exception as exc:
        return WhatsAppDispatchResult(ok=False, status="error", error_code=type(exc).__name__, retryable=True)
    if isinstance(raw, WhatsAppDispatchResult):
        return raw
    if isinstance(raw, dict):
        ok = bool(raw.get("ok") or raw.get("status") in {"sent", "success", "queued"})
        return WhatsAppDispatchResult(
            ok=ok,
            status=str(raw.get("status") or ("sent" if ok else "failed")),
            external_message_id=str(raw.get("external_message_id")) if raw.get("external_message_id") else None,
            error_code=str(raw.get("error_code")) if raw.get("error_code") else None,
            retryable=bool(raw.get("retryable")),
        )
    if isinstance(raw, bool):
        return WhatsAppDispatchResult(ok=raw, status="sent" if raw else "failed")
    return WhatsAppDispatchResult(ok=False, status="invalid_dispatch_result", error_code="invalid_dispatch_result")


def _queue_status_from_dispatch(dispatch: WhatsAppDispatchResult, *, fallback_used: bool) -> OSROperationsDispatchStatus:
    if dispatch.ok and fallback_used:
        return OSROperationsDispatchStatus.FALLBACK_USED
    if dispatch.ok:
        return OSROperationsDispatchStatus.DISPATCHED
    if dispatch.retryable:
        return OSROperationsDispatchStatus.RETRYABLE
    return OSROperationsDispatchStatus.FAILED


def _base_event_payload(
    *,
    ticket: Ticket,
    case_context: CaseContext,
    country_code: str,
    issue_type: str,
    routing_channel: str,
    event: str,
    dispatch_key: str,
) -> dict[str, Any]:
    return {
        "event": event,
        "source": "nexus_osr",
        "dispatch_key": dispatch_key,
        "ticket_id": getattr(ticket, "id", None),
        "ticket_no": getattr(ticket, "ticket_no", None),
        "country_code": country_code,
        "issue_type": issue_type,
        "routing_channel": routing_channel,
        "case_context": {
            "conversation_id": case_context.conversation_id,
            "ticket_id": case_context.ticket_id,
            "channel": case_context.channel,
            "country_code": country_code,
            "issue_type": issue_type,
            "status": str(case_context.status),
            "safe_tracking_reference": case_context.safe_tracking_reference,
            "tracking_number_hash": case_context.tracking_number_hash,
            "missing_info": list(case_context.missing_info or []),
            "routed_group_key": case_context.routed_group_key,
        },
    }


def _dispatch_metadata(
    *,
    ticket: Ticket,
    case_context: CaseContext,
    rule: WhatsAppRoutingRuleRecord,
    routing_channel: str,
    dispatch_key: str,
    destination_group_key: str,
) -> dict[str, Any]:
    return {
        "source": "nexus_osr",
        "dispatch_key": dispatch_key,
        "ticket_id": getattr(ticket, "id", None),
        "ticket_no": getattr(ticket, "ticket_no", None),
        "routing_rule_id": rule.id,
        "routing_channel": routing_channel,
        "destination_group_key": destination_group_key,
        "country_code": _normalize_country(case_context.country_code or getattr(ticket, "country_code", None)),
        "issue_type": _normalize_issue(case_context.issue_type or getattr(ticket, "case_type", None)),
        "safe_tracking_reference": case_context.safe_tracking_reference,
        "tracking_number_hash": case_context.tracking_number_hash,
    }


def _write_routing_event(db: Session, *, ticket: Ticket, note: str, payload: dict[str, Any]) -> TicketEvent:
    safe_payload = _safe_payload(payload)
    event = TicketEvent(
        ticket_id=ticket.id,
        actor_id=None,
        event_type=EventType.field_updated,
        note=note,
        payload_json=json.dumps(safe_payload, ensure_ascii=False, sort_keys=True, default=str),
    )
    db.add(event)
    db.flush()
    return event


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _safe_payload(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe_payload(item) for item in value]
    if isinstance(value, str):
        return _redact_for_group(value, limit=1200)
    return value


def _destination_group_key(rule: WhatsAppRoutingRuleRecord, *, fallback: bool) -> str:
    suffix = "fallback" if fallback else "destination"
    country = _normalize_country(rule.country_code).lower()
    issue = _normalize_issue(rule.issue_type)
    channel = _normalize_channel(rule.channel)
    return f"{channel}:{country}:{issue}:{suffix}"


def _provider_group_id(rule: WhatsAppRoutingRuleRecord, *, fallback: bool) -> str | None:
    raw = rule.fallback_group_id if fallback else rule.destination_group_id
    cleaned = str(raw or "").strip()
    return cleaned or None


def _hash_provider_group_id(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    if not cleaned:
        return None
    return _sha256(cleaned)


def _message_preview(message: str) -> str:
    return _redact_for_group(message, limit=500)


def _dispatch_key(*, ticket: Ticket, case_context: CaseContext, country_code: str, issue_type: str, routing_channel: str) -> str:
    parts = [
        str(getattr(ticket, "id", "") or ""),
        str(case_context.conversation_id or ""),
        str(case_context.ticket_id or getattr(ticket, "id", "") or ""),
        country_code,
        issue_type,
        routing_channel,
    ]
    return _sha256("|".join(parts))


def _sha256(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_country(value: Any) -> str:
    cleaned = str(value or "GLOBAL").strip().upper()
    return cleaned or "GLOBAL"


def _normalize_issue(value: Any) -> str:
    cleaned = str(value or "general").strip().lower()
    return cleaned or "general"


def _normalize_channel(value: Any) -> str:
    cleaned = str(value or "whatsapp").strip().lower()
    return cleaned or "whatsapp"
