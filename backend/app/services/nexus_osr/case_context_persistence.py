from __future__ import annotations

from datetime import datetime

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session, object_session

from ...models_osr import CaseContextRecord
from ...utils.time import ensure_utc, utc_now
from ...webchat_models import WebchatConversation
from .case_context import CaseContext, CaseContextStatus, ContactMethod

_TERMINAL_STATUSES = {
    CaseContextStatus.CLOSED.value,
    CaseContextStatus.ARCHIVED.value,
}


def load_case_context(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    tenant_id: str | None = None,
    include_inactive: bool = False,
    now: datetime | None = None,
) -> CaseContext | None:
    """Load one tenant-scoped context without silently crossing identities.

    A single-key selector first resolves its exact single-key identity. For
    backwards-compatible internal reads it may then resolve one unique active
    two-key row. Multiple fallback candidates fail closed instead of choosing a
    random case. Saving always remains exact-combination only. A selector with
    no tenant or persisted context evidence returns ``None`` so first-use runtime
    flows can create the context normally.
    """

    conversation = _safe_identity(conversation_id)
    ticket = _safe_identity(ticket_id)
    if conversation is None and ticket is None:
        raise ValueError("case_context_identity_required")

    tenant = _resolve_tenant(
        db,
        tenant_id=tenant_id,
        conversation_id=conversation,
        ticket_id=ticket,
    )
    if tenant is None:
        return None
    current = ensure_utc(now) or utc_now()

    exact = _identity_query(
        db,
        tenant_id=tenant,
        conversation_id=conversation,
        ticket_id=ticket,
    )
    row = _first_context_row(
        _apply_read_state(exact, include_inactive=include_inactive, now=current)
    )
    if row is not None:
        return record_to_case_context(row)
    if conversation is not None and ticket is not None:
        return None

    fallback = db.query(CaseContextRecord).filter(CaseContextRecord.tenant_id == tenant)
    if conversation is not None:
        fallback = fallback.filter(
            CaseContextRecord.conversation_id == conversation,
            CaseContextRecord.ticket_id.is_not(None),
        )
    else:
        fallback = fallback.filter(
            CaseContextRecord.ticket_id == ticket,
            CaseContextRecord.conversation_id.is_not(None),
        )
    candidates = (
        _apply_read_state(fallback, include_inactive=include_inactive, now=current)
        .order_by(CaseContextRecord.id.desc())
        .limit(2)
        .all()
    )
    if len(candidates) > 1:
        raise ValueError("case_context_identity_ambiguous")
    return record_to_case_context(candidates[0]) if candidates else None


def save_case_context(
    db: Session,
    context: CaseContext,
    *,
    tenant_id: str = "default",
    expires_at: datetime | None = None,
) -> CaseContextRecord:
    """Persist short-lived case state without reviving inactive history."""

    tenant = _normalize_tenant(tenant_id)
    conversation = _safe_identity(context.conversation_id)
    ticket = _safe_identity(context.ticket_id)
    current = utc_now()
    expiry = ensure_utc(expires_at)
    closed_at = ensure_utc(_parse_iso(context.closed_at))
    status = _status_value(context.status)
    incoming_active = (
        (conversation is not None or ticket is not None)
        and status not in _TERMINAL_STATUSES
        and closed_at is None
        and (expiry is None or expiry > current)
    )

    if conversation is not None or ticket is not None:
        _deactivate_logically_inactive_rows(
            db,
            tenant_id=tenant,
            conversation_id=conversation,
            ticket_id=ticket,
            now=current,
        )
        row = _active_exact_row(
            db,
            tenant_id=tenant,
            conversation_id=conversation,
            ticket_id=ticket,
            now=current,
        )
    else:
        row = None

    if row is None:
        row = CaseContextRecord(
            tenant_id=tenant,
            conversation_id=conversation,
            ticket_id=ticket,
        )

    _apply_values(
        row,
        context=context,
        tenant_id=tenant,
        conversation_id=conversation,
        ticket_id=ticket,
        status=status,
        is_active=incoming_active,
        expires_at=expiry,
        closed_at=closed_at,
    )

    if row.id is not None or not incoming_active:
        if row.id is None:
            db.add(row)
        db.flush()
        return row

    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
        return row
    except IntegrityError as exc:
        if object_session(row) is db:
            db.expunge(row)
        db.expire_all()
        winner = _active_exact_row(
            db,
            tenant_id=tenant,
            conversation_id=conversation,
            ticket_id=ticket,
            now=current,
        )
        if winner is None:
            raise exc
        _apply_values(
            winner,
            context=context,
            tenant_id=tenant,
            conversation_id=conversation,
            ticket_id=ticket,
            status=status,
            is_active=True,
            expires_at=expiry,
            closed_at=closed_at,
        )
        db.flush()
        return winner


def close_case_context(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    tenant_id: str = "default",
    now: datetime | None = None,
) -> CaseContextRecord | None:
    current = ensure_utc(now) or utc_now()
    row = _active_exact_row(
        db,
        tenant_id=_normalize_tenant(tenant_id),
        conversation_id=_safe_identity(conversation_id),
        ticket_id=_safe_identity(ticket_id),
        now=current,
    )
    if row is None:
        return None
    row.status = CaseContextStatus.CLOSED.value
    row.closed_at = current
    row.is_active = False
    row.updated_at = current
    db.flush()
    return row


def expire_case_context(
    db: Session,
    *,
    conversation_id: int | None = None,
    ticket_id: int | None = None,
    tenant_id: str = "default",
    now: datetime | None = None,
) -> CaseContextRecord | None:
    current = ensure_utc(now) or utc_now()
    row = _active_exact_row(
        db,
        tenant_id=_normalize_tenant(tenant_id),
        conversation_id=_safe_identity(conversation_id),
        ticket_id=_safe_identity(ticket_id),
        now=current,
    )
    if row is None:
        return None
    row.expires_at = current
    row.is_active = False
    row.updated_at = current
    db.flush()
    return row


def _resolve_tenant(
    db: Session,
    *,
    tenant_id: str | None,
    conversation_id: int | None,
    ticket_id: int | None,
) -> str | None:
    if tenant_id is not None:
        return _normalize_tenant(tenant_id)
    if conversation_id is not None:
        resolved = (
            db.query(WebchatConversation.tenant_key)
            .filter(WebchatConversation.id == conversation_id)
            .scalar()
        )
        if resolved:
            return _normalize_tenant(resolved)
        tenant_rows = (
            db.query(CaseContextRecord.tenant_id)
            .filter(CaseContextRecord.conversation_id == conversation_id)
            .distinct()
            .limit(2)
            .all()
        )
    elif ticket_id is not None:
        tenant_rows = (
            db.query(CaseContextRecord.tenant_id)
            .filter(CaseContextRecord.ticket_id == ticket_id)
            .distinct()
            .limit(2)
            .all()
        )
    else:
        raise ValueError("case_context_tenant_required")
    if not tenant_rows:
        return None
    if len(tenant_rows) > 1:
        raise ValueError("case_context_tenant_ambiguous")
    return _normalize_tenant(tenant_rows[0][0])


def _identity_query(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None,
    ticket_id: int | None,
) -> Query:
    if conversation_id is None and ticket_id is None:
        raise ValueError("case_context_identity_required")
    return (
        db.query(CaseContextRecord)
        .filter(CaseContextRecord.tenant_id == tenant_id)
        .filter(
            CaseContextRecord.conversation_id.is_(None)
            if conversation_id is None
            else CaseContextRecord.conversation_id == conversation_id
        )
        .filter(
            CaseContextRecord.ticket_id.is_(None)
            if ticket_id is None
            else CaseContextRecord.ticket_id == ticket_id
        )
    )


def _apply_read_state(query: Query, *, include_inactive: bool, now: datetime) -> Query:
    if include_inactive:
        return query
    return (
        query.filter(CaseContextRecord.is_active.is_(True))
        .filter(CaseContextRecord.closed_at.is_(None))
        .filter(~CaseContextRecord.status.in_(_TERMINAL_STATUSES))
        .filter(or_(CaseContextRecord.expires_at.is_(None), CaseContextRecord.expires_at > now))
    )


def _first_context_row(query: Query) -> CaseContextRecord | None:
    return query.order_by(CaseContextRecord.id.desc()).first()


def _active_exact_row(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None,
    ticket_id: int | None,
    now: datetime,
) -> CaseContextRecord | None:
    if conversation_id is None and ticket_id is None:
        return None
    return _first_context_row(
        _apply_read_state(
            _identity_query(
                db,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                ticket_id=ticket_id,
            ),
            include_inactive=False,
            now=now,
        )
    )


def _deactivate_logically_inactive_rows(
    db: Session,
    *,
    tenant_id: str,
    conversation_id: int | None,
    ticket_id: int | None,
    now: datetime,
) -> None:
    rows = (
        _identity_query(
            db,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            ticket_id=ticket_id,
        )
        .filter(CaseContextRecord.is_active.is_(True))
        .filter(
            or_(
                CaseContextRecord.closed_at.is_not(None),
                CaseContextRecord.status.in_(_TERMINAL_STATUSES),
                CaseContextRecord.expires_at <= now,
            )
        )
        .all()
    )
    for row in rows:
        row.is_active = False
        row.updated_at = now
    if rows:
        db.flush()


def _apply_values(
    row: CaseContextRecord,
    *,
    context: CaseContext,
    tenant_id: str,
    conversation_id: int | None,
    ticket_id: int | None,
    status: str,
    is_active: bool,
    expires_at: datetime | None,
    closed_at: datetime | None,
) -> None:
    row.tenant_id = tenant_id
    row.conversation_id = conversation_id
    row.ticket_id = ticket_id
    row.channel = context.channel
    row.country_code = context.country_code
    row.issue_type = context.issue_type
    row.status = status
    row.is_active = is_active
    row.safe_tracking_reference = context.safe_tracking_reference
    row.tracking_number_hash = context.tracking_number_hash
    row.contact_methods_json = [item.as_dict() for item in context.contact_methods]
    row.customer_claim_summary = context.customer_claim_summary
    row.last_mcp_fact_json = context.last_mcp_fact
    row.missing_info_json = list(context.missing_info)
    row.handoff_requested = context.handoff_requested
    row.ticket_created = context.ticket_created
    row.routed_group_key = context.routed_group_key
    row.ai_actions_taken_json = list(context.ai_actions_taken)
    row.agent_handover_summary = context.agent_handover_summary
    row.expires_at = expires_at
    row.closed_at = closed_at
    row.updated_at = utc_now()


def record_to_case_context(row: CaseContextRecord) -> CaseContext:
    contacts: list[ContactMethod] = []
    for item in row.contact_methods_json or []:
        if isinstance(item, dict):
            contacts.append(
                ContactMethod(
                    channel=str(item.get("channel") or "unknown"),
                    value_redacted=str(item.get("value_redacted") or ""),
                    source=str(item.get("source") or "unknown"),
                    is_default=bool(item.get("is_default")),
                )
            )
    try:
        status = CaseContextStatus(row.status)
    except ValueError:
        status = CaseContextStatus.ACTIVE
    return CaseContext(
        conversation_id=row.conversation_id,
        ticket_id=row.ticket_id,
        channel=row.channel,
        country_code=row.country_code,
        issue_type=row.issue_type,
        status=status,
        safe_tracking_reference=row.safe_tracking_reference,
        tracking_number_hash=row.tracking_number_hash,
        contact_methods=contacts,
        customer_claim_summary=row.customer_claim_summary,
        last_mcp_fact=row.last_mcp_fact_json,
        missing_info=list(row.missing_info_json or []),
        handoff_requested=row.handoff_requested,
        ticket_created=row.ticket_created,
        routed_group_key=row.routed_group_key,
        ai_actions_taken=list(row.ai_actions_taken_json or []),
        agent_handover_summary=row.agent_handover_summary,
        created_at=row.created_at.isoformat() if row.created_at else "",
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        closed_at=row.closed_at.isoformat() if row.closed_at else None,
    )


def _normalize_tenant(value: str | None) -> str:
    return (str(value or "default").strip() or "default")[:80]


def _safe_identity(value: int | str | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("case_context_identity_invalid")
    if isinstance(value, int):
        return value
    cleaned = str(value).strip()
    if not cleaned.isdigit():
        raise ValueError("case_context_identity_invalid")
    return int(cleaned)


def _status_value(value: CaseContextStatus | str) -> str:
    return value.value if isinstance(value, CaseContextStatus) else str(value)


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
