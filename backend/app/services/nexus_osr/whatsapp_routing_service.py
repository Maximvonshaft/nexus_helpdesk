from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Protocol

from sqlalchemy.orm import Session

from ...models import Ticket, TicketEvent
from ...models_osr import WhatsAppRoutingRuleRecord
from .case_context import CaseContext
from .operations_dispatch_outbox import (
    build_operations_dispatch_key,
    digest_identifier,
    enqueue_operations_dispatch,
)
from .persistence import save_case_context

_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_TRACKING_RE = re.compile(r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b", re.I)
_SECRET_RE = re.compile(r"(?:\bbearer\s+\S+|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|\b(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S+)", re.I)
_GROUP_ID_RE = re.compile(r"\b\d{10,24}@g\.us\b", re.I)
_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([A-Za-z0-9_.:-]+)\s*\}\}")
_ALLOWED_TEMPLATE_KEYS = {
    "country_code",
    "issue_type",
    "channel",
    "safe_tracking_reference",
    "ticket_no",
    "priority",
    "status",
}


class WhatsAppRoutingStatus(StrEnum):
    ROUTED = "routed"
    NO_RULE = "no_rule"
    DISABLED_RULE = "disabled_rule"


class WhatsAppGroupDispatcher(Protocol):
    """Legacy type retained only to make direct-send prohibition explicit."""

    def send(self, *, group_id: str, message: str) -> Any:
        ...


@dataclass(frozen=True)
class WhatsAppRoutingResult:
    status: WhatsAppRoutingStatus
    routed: bool
    reason: str
    rule_id: int | None
    group_key: str | None
    group_hash: str | None
    fallback_used: bool
    case_context: CaseContext
    outbox_id: int | None = None
    dispatch_key: str | None = None
    dispatch_status: str | None = None

    def as_safe_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "routed": self.routed,
            "reason": self.reason,
            "rule_id": self.rule_id,
            "group_key": self.group_key,
            "group_hash": self.group_hash,
            "fallback_used": self.fallback_used,
            "outbox_id": self.outbox_id,
            "dispatch_key": self.dispatch_key,
            "dispatch_status": self.dispatch_status,
        }


def route_ticket_to_whatsapp_group(
    db: Session,
    *,
    case_context: CaseContext,
    ticket: Ticket,
    channel: str = "whatsapp",
    tenant_key: str | None = None,
    template_context: Mapping[str, Any] | None = None,
    dispatcher: WhatsAppGroupDispatcher | None = None,
    message: str | None = None,
) -> WhatsAppRoutingResult:
    """Resolve one configured rule and enqueue a durable operations dispatch.

    Direct transport is forbidden. ``dispatcher`` and free-form ``message`` are
    retained only as explicit compatibility traps and fail closed when supplied.
    Template context may be validated separately with ``build_safe_group_message``
    but no message body is stored in the outbox or TicketEvent timeline.
    """

    if dispatcher is not None:
        raise RuntimeError("direct_whatsapp_dispatch_forbidden")
    if message not in (None, ""):
        raise RuntimeError("operations_dispatch_message_body_forbidden")
    if template_context:
        # Validate caller-supplied values without persisting or returning text.
        build_safe_group_message("", values=template_context)

    country = _scope(case_context.country_code or getattr(ticket, "country_code", None) or "GLOBAL", field="country_code", limit=16).upper()
    issue_type = _scope(case_context.issue_type or getattr(ticket, "case_type", None) or "general", field="issue_type", limit=120)
    channel_key = _scope(channel or "whatsapp", field="channel", limit=40).lower()
    tenant = _scope(tenant_key or "default", field="tenant_key", limit=80)

    rule = (
        db.query(WhatsAppRoutingRuleRecord)
        .filter(WhatsAppRoutingRuleRecord.country_code == country)
        .filter(WhatsAppRoutingRuleRecord.issue_type == issue_type)
        .filter(WhatsAppRoutingRuleRecord.channel == channel_key)
        .order_by(WhatsAppRoutingRuleRecord.priority.asc(), WhatsAppRoutingRuleRecord.id.asc())
        .first()
    )
    if rule is None:
        return _not_routed(
            status=WhatsAppRoutingStatus.NO_RULE,
            reason="whatsapp_routing_rule_not_found",
            case_context=case_context,
        )
    if not rule.enabled:
        return _not_routed(
            status=WhatsAppRoutingStatus.DISABLED_RULE,
            reason="whatsapp_routing_rule_disabled",
            case_context=case_context,
            rule_id=rule.id,
        )

    destination_id = str(rule.destination_group_id or "").strip()
    if not destination_id:
        return _not_routed(
            status=WhatsAppRoutingStatus.DISABLED_RULE,
            reason="whatsapp_routing_destination_missing",
            case_context=case_context,
            rule_id=rule.id,
        )

    group_hash = digest_identifier(destination_id)
    group_key = _group_key(group_hash)
    ticket_id = int(ticket.id) if getattr(ticket, "id", None) is not None else None
    case_reference = ":".join([
        str(case_context.conversation_id or "none"),
        str(case_context.tracking_number_hash or "none"),
        issue_type,
    ])
    dispatch_key = build_operations_dispatch_key(
        tenant_key=tenant,
        country_code=country,
        channel_key=channel_key,
        routing_rule_id=rule.id,
        ticket_id=ticket_id,
        case_reference=case_reference,
    )
    enqueue_result = enqueue_operations_dispatch(
        db,
        dispatch_key=dispatch_key,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel_key,
        routing_rule_id=rule.id,
        destination_group_key=group_key,
        destination_group_hash=group_hash,
        ticket_id=ticket_id,
    )

    updated_context = case_context.mark_routed(group_key)
    save_case_context(db, updated_context, tenant_id=tenant)
    if ticket_id is not None:
        db.add(TicketEvent(
            ticket_id=ticket_id,
            event_type="whatsapp_group_routed",
            actor_type="system",
            actor_user_id=None,
            details_json={
                "routing_status": WhatsAppRoutingStatus.ROUTED.value,
                "rule_id": rule.id,
                "group_key": group_key,
                "group_hash": group_hash,
                "fallback_used": False,
                "outbox_id": enqueue_result.record.id,
                "dispatch_key": dispatch_key,
                "dispatch_status": enqueue_result.record.status,
                "outbox_created": enqueue_result.created,
            },
        ))
        db.flush()

    return WhatsAppRoutingResult(
        status=WhatsAppRoutingStatus.ROUTED,
        routed=True,
        reason="whatsapp_operations_dispatch_enqueued",
        rule_id=rule.id,
        group_key=group_key,
        group_hash=group_hash,
        fallback_used=False,
        case_context=updated_context,
        outbox_id=enqueue_result.record.id,
        dispatch_key=dispatch_key,
        dispatch_status=enqueue_result.record.status,
    )


def build_safe_group_message(template: str, *, values: Mapping[str, Any] | None = None) -> str:
    """Render a bounded internal template without exposing arbitrary values.

    This helper does not send or enqueue text. It remains available for future
    governed provider-adapter work and existing safe preview tests.
    """

    source = str(template or "").strip()
    if not source:
        return ""
    data = dict(values or {})

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in _ALLOWED_TEMPLATE_KEYS:
            return "[unsupported_template_value]"
        return _sanitize_text(data.get(key), limit=80)

    rendered = _TEMPLATE_TOKEN_RE.sub(replace, source)
    return _sanitize_text(rendered, limit=800)


def _not_routed(
    *,
    status: WhatsAppRoutingStatus,
    reason: str,
    case_context: CaseContext,
    rule_id: int | None = None,
) -> WhatsAppRoutingResult:
    return WhatsAppRoutingResult(
        status=status,
        routed=False,
        reason=reason,
        rule_id=rule_id,
        group_key=None,
        group_hash=None,
        fallback_used=False,
        case_context=case_context,
    )


def _group_key(group_hash: str) -> str:
    return "provider-group:" + group_hash.removeprefix("sha256:")[:20]


def _scope(value: Any, *, field: str, limit: int) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit or not _SAFE_KEY_RE.fullmatch(text):
        raise ValueError(f"whatsapp_routing_invalid_{field}")
    return text


def _sanitize_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").strip().split())
    text = _SECRET_RE.sub("[redacted_secret]", text)
    text = _GROUP_ID_RE.sub("[redacted_provider_group]", text)
    text = _EMAIL_RE.sub("[redacted_email]", text)
    text = _PHONE_RE.sub("[redacted_phone]", text)
    text = _TRACKING_RE.sub("[redacted_tracking]", text)
    return text[:limit]
