from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..models import (
    Tenant,
    Ticket,
    TicketComment,
    TicketInternalNote,
    User,
)
from ..models_case_governance import CaseOutcomeRecord
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import (
    WebchatAITurn,
    WebchatConversation,
    WebchatHandoffRequest,
)
from .scope_permissions import has_global_case_visibility
from .tenant_authority import resolve_actor_tenant_id

ROOT = Path(__file__).resolve().parents[3]
METRIC_AUTHORITY = ROOT / "config" / "operations" / "outcome-metrics.v1.json"
TERMINAL_ACTION_STATES = {"succeeded", "failed", "confirmed", "repair_required", "waived"}
SUCCESS_ACTION_STATES = {"succeeded", "confirmed", "waived"}
TERMINAL_NOTIFICATION_STATES = {"delivered", "confirmed", "failed", "repair_required", "waived"}
SUCCESS_NOTIFICATION_STATES = {"delivered", "confirmed", "waived"}
TERMINAL_PROVIDER_STATES = {"succeeded", "failed", "confirmed", "repair_required"}
FAILURE_PROVIDER_STATES = {"failed", "repair_required"}


class OutcomeMetricAuthorityError(RuntimeError):
    pass


def _definitions() -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    try:
        payload = json.loads(METRIC_AUTHORITY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeMetricAuthorityError("outcome_metric_authority_unavailable") from exc
    if payload.get("schema") != "nexus.outcome-metrics.v1":
        raise OutcomeMetricAuthorityError("outcome_metric_authority_schema_invalid")
    rows = payload.get("metrics")
    if not isinstance(rows, list) or not rows:
        raise OutcomeMetricAuthorityError("outcome_metric_definitions_missing")
    definitions: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("key") or "").strip():
            raise OutcomeMetricAuthorityError("outcome_metric_definition_invalid")
        key = str(row["key"]).strip()
        if key in definitions:
            raise OutcomeMetricAuthorityError("outcome_metric_definition_duplicate")
        definitions[key] = row
    return payload, definitions


def _visible_ticket_query(db: Session, current_user: User):
    actor_tenant_id = resolve_actor_tenant_id(db, current_user)
    query = db.query(Ticket)
    if actor_tenant_id is None:
        query = query.filter(Ticket.tenant_id.is_(None))
    else:
        query = query.filter(Ticket.tenant_id == actor_tenant_id)
    if not has_global_case_visibility(current_user, db):
        query = query.filter(
            or_(
                Ticket.team_id == current_user.team_id,
                Ticket.assignee_id == current_user.id,
            )
        )
    return query, actor_tenant_id


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator * 100.0 / denominator, 2)


def _metric(
    definition: dict[str, Any],
    *,
    numerator: float | int | None,
    denominator: float | int | None,
    value: float | int | None,
    unit: str,
    available: bool = True,
    unavailable_reason: str | None = None,
    drilldown_ticket_ids: list[int] | None = None,
) -> dict[str, Any]:
    return {
        "key": definition["key"],
        "label": definition["label"],
        "owner": definition["owner"],
        "available": available,
        "unavailable_reason": unavailable_reason,
        "numerator": numerator,
        "denominator": denominator,
        "value": value,
        "unit": unit,
        "definition": {
            "numerator": definition["numerator"],
            "denominator": definition["denominator"],
            "lineage": definition["lineage"],
            "drilldown": definition["drilldown"],
        },
        "drilldown_ticket_ids": sorted(set(drilldown_ticket_ids or []))[:100],
    }


def _closure_timelines(
    rows: list[CaseOutcomeRecord],
) -> tuple[dict[int, datetime], dict[int, list[datetime]]]:
    closed: dict[int, datetime] = {}
    reopened: dict[int, list[datetime]] = {}
    for row in rows:
        occurred = ensure_utc(row.occurred_at)
        if occurred is None or row.record_type != "closure_assessment":
            continue
        if row.state == "closed":
            closed[row.ticket_id] = occurred
        elif row.state == "reopened":
            reopened.setdefault(row.ticket_id, []).append(occurred)
    return closed, reopened


def _count_human_touches(db: Session, ticket_ids: list[int]) -> int:
    if not ticket_ids:
        return 0
    comments = int(
        db.query(func.count(TicketComment.id))
        .filter(
            TicketComment.ticket_id.in_(ticket_ids),
            TicketComment.author_id.is_not(None),
        )
        .scalar()
        or 0
    )
    notes = int(
        db.query(func.count(TicketInternalNote.id))
        .filter(
            TicketInternalNote.ticket_id.in_(ticket_ids),
            TicketInternalNote.author_id.is_not(None),
        )
        .scalar()
        or 0
    )
    return comments + notes


def build_outcome_metrics(
    db: Session,
    current_user: User,
    *,
    window_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    authority, definitions = _definitions()
    observed_at = ensure_utc(now or utc_now()) or utc_now()
    resolved_window = int(window_days or authority.get("default_window_days") or 30)
    if resolved_window < 1 or resolved_window > 366:
        raise ValueError("outcome_metric_window_invalid")
    window_start = observed_at - timedelta(days=resolved_window)

    visible, actor_tenant_id = _visible_ticket_query(db, current_user)
    closed_tickets = (
        visible.filter(
            Ticket.closed_at.is_not(None),
            Ticket.closed_at >= window_start,
            Ticket.closed_at <= observed_at,
        )
        .order_by(Ticket.id.asc())
        .all()
    )
    closed_ids = [row.id for row in closed_tickets]
    closed_by_id = {row.id: row for row in closed_tickets}

    ledger_rows = (
        db.query(CaseOutcomeRecord)
        .filter(CaseOutcomeRecord.ticket_id.in_(closed_ids))
        .order_by(
            CaseOutcomeRecord.ticket_id.asc(),
            CaseOutcomeRecord.sequence.asc(),
        )
        .all()
        if closed_ids
        else []
    )
    closure_at, reopen_at = _closure_timelines(ledger_rows)
    safe_closed_ids = sorted(set(closure_at) & set(closed_ids))

    conversation_counts = {
        int(ticket_id): int(count or 0)
        for ticket_id, count in (
            db.query(
                WebchatConversation.ticket_id,
                func.count(WebchatConversation.id),
            )
            .filter(WebchatConversation.ticket_id.in_(closed_ids))
            .group_by(WebchatConversation.ticket_id)
            .all()
            if closed_ids
            else []
        )
        if ticket_id is not None
    }

    first_contact_ids = [
        ticket_id
        for ticket_id in safe_closed_ids
        if int(closed_by_id[ticket_id].reopen_count or 0) == 0
        and conversation_counts.get(ticket_id, 0) <= 1
    ]
    repeat_contact_ids = [
        ticket_id
        for ticket_id in closed_ids
        if int(closed_by_id[ticket_id].reopen_count or 0) > 0
        or conversation_counts.get(ticket_id, 0) > 1
    ]

    reopen_72h_ids: list[int] = []
    reopen_7d_ids: list[int] = []
    for ticket_id, closed_time in closure_at.items():
        for reopen_time in reopen_at.get(ticket_id, []):
            elapsed = reopen_time - closed_time
            if timedelta(0) <= elapsed <= timedelta(hours=72):
                reopen_72h_ids.append(ticket_id)
            if timedelta(0) <= elapsed <= timedelta(days=7):
                reopen_7d_ids.append(ticket_id)

    outcome_window_rows = (
        db.query(CaseOutcomeRecord)
        .join(Ticket, Ticket.id == CaseOutcomeRecord.ticket_id)
        .filter(
            CaseOutcomeRecord.occurred_at >= window_start,
            CaseOutcomeRecord.occurred_at <= observed_at,
        )
    )
    if actor_tenant_id is None:
        outcome_window_rows = outcome_window_rows.filter(Ticket.tenant_id.is_(None))
    else:
        outcome_window_rows = outcome_window_rows.filter(Ticket.tenant_id == actor_tenant_id)
    if not has_global_case_visibility(current_user, db):
        outcome_window_rows = outcome_window_rows.filter(
            or_(
                Ticket.team_id == current_user.team_id,
                Ticket.assignee_id == current_user.id,
            )
        )
    outcome_window = outcome_window_rows.all()

    action_rows = [
        row
        for row in outcome_window
        if row.record_type in {"execution_attempt", "operational_outcome"}
        and row.state in TERMINAL_ACTION_STATES
    ]
    action_success = [row for row in action_rows if row.state in SUCCESS_ACTION_STATES]
    action_fail_ids = [row.ticket_id for row in action_rows if row.state not in SUCCESS_ACTION_STATES]

    notification_rows = [
        row
        for row in outcome_window
        if row.record_type == "customer_notification"
        and row.state in TERMINAL_NOTIFICATION_STATES
    ]
    notification_success = [
        row for row in notification_rows if row.state in SUCCESS_NOTIFICATION_STATES
    ]
    notification_fail_ids = [
        row.ticket_id
        for row in notification_rows
        if row.state not in SUCCESS_NOTIFICATION_STATES
    ]

    provider_rows = [
        row
        for row in outcome_window
        if row.record_type == "provider_receipt"
        and row.state in TERMINAL_PROVIDER_STATES
    ]
    provider_failures = [row for row in provider_rows if row.state in FAILURE_PROVIDER_STATES]

    tenant_key = None
    if actor_tenant_id is not None:
        tenant_key = db.query(Tenant.tenant_key).filter(Tenant.id == actor_tenant_id).scalar()
    handoff_query = (
        db.query(WebchatHandoffRequest)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .filter(
            WebchatHandoffRequest.requested_at >= window_start,
            WebchatHandoffRequest.requested_at <= observed_at,
        )
    )
    if tenant_key:
        handoff_query = handoff_query.filter(WebchatConversation.tenant_key == tenant_key)
    handoffs = handoff_query.all()
    accepted_handoffs = [row for row in handoffs if row.accepted_at is not None]
    handoff_waits = [
        max(
            0,
            int(
                (
                    (ensure_utc(row.accepted_at) or observed_at)
                    - (ensure_utc(row.requested_at) or observed_at)
                ).total_seconds()
            ),
        )
        for row in accepted_handoffs
    ]
    handoff_p50 = int(statistics.median(handoff_waits)) if handoff_waits else None

    ai_query = (
        db.query(WebchatAITurn, WebchatConversation, Ticket)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatAITurn.conversation_id,
        )
        .outerjoin(Ticket, Ticket.id == WebchatAITurn.ticket_id)
        .filter(
            WebchatAITurn.completed_at >= window_start,
            WebchatAITurn.completed_at <= observed_at,
            WebchatAITurn.is_public_reply_allowed.is_(True),
            WebchatAITurn.status.in_(("completed", "failed", "timeout")),
        )
    )
    if tenant_key:
        ai_query = ai_query.filter(WebchatConversation.tenant_key == tenant_key)
    ai_rows = ai_query.all()
    ai_conversation_ids = [conversation.id for _, conversation, _ in ai_rows]
    accepted_handoff_conversations = {
        int(value)
        for (value,) in (
            db.query(WebchatHandoffRequest.conversation_id)
            .filter(
                WebchatHandoffRequest.conversation_id.in_(ai_conversation_ids),
                WebchatHandoffRequest.accepted_at.is_not(None),
            )
            .all()
            if ai_conversation_ids
            else []
        )
    }
    ai_contained = [
        turn
        for turn, conversation, ticket in ai_rows
        if turn.status == "completed"
        and turn.reply_message_id is not None
        and conversation.id not in accepted_handoff_conversations
        and (ticket is None or int(ticket.reopen_count or 0) == 0)
    ]

    human_touches = _count_human_touches(db, closed_ids)
    metrics = [
        _metric(
            definitions["safe_effective_closure_rate"],
            numerator=len(safe_closed_ids),
            denominator=len(closed_ids),
            value=_ratio(len(safe_closed_ids), len(closed_ids)),
            unit="percent",
            drilldown_ticket_ids=[value for value in closed_ids if value not in safe_closed_ids],
        ),
        _metric(
            definitions["first_contact_resolution_rate"],
            numerator=len(first_contact_ids),
            denominator=len(safe_closed_ids),
            value=_ratio(len(first_contact_ids), len(safe_closed_ids)),
            unit="percent",
            drilldown_ticket_ids=[value for value in safe_closed_ids if value not in first_contact_ids],
        ),
        _metric(
            definitions["reopen_72h_rate"],
            numerator=len(set(reopen_72h_ids)),
            denominator=len(safe_closed_ids),
            value=_ratio(len(set(reopen_72h_ids)), len(safe_closed_ids)),
            unit="percent",
            drilldown_ticket_ids=reopen_72h_ids,
        ),
        _metric(
            definitions["reopen_7d_rate"],
            numerator=len(set(reopen_7d_ids)),
            denominator=len(safe_closed_ids),
            value=_ratio(len(set(reopen_7d_ids)), len(safe_closed_ids)),
            unit="percent",
            drilldown_ticket_ids=reopen_7d_ids,
        ),
        _metric(
            definitions["repeat_contact_rate"],
            numerator=len(repeat_contact_ids),
            denominator=len(closed_ids),
            value=_ratio(len(repeat_contact_ids), len(closed_ids)),
            unit="percent",
            drilldown_ticket_ids=repeat_contact_ids,
        ),
        _metric(
            definitions["handoff_acceptance_rate"],
            numerator=len(accepted_handoffs),
            denominator=len(handoffs),
            value=_ratio(len(accepted_handoffs), len(handoffs)),
            unit="percent",
        ),
        _metric(
            definitions["handoff_wait_seconds_p50"],
            numerator=len(accepted_handoffs),
            denominator=len(accepted_handoffs),
            value=handoff_p50,
            unit="seconds",
        ),
        _metric(
            definitions["human_touches_per_resolved_case"],
            numerator=human_touches,
            denominator=len(closed_ids),
            value=(round(human_touches / len(closed_ids), 2) if closed_ids else None),
            unit="count_per_case",
        ),
        _metric(
            definitions["action_operational_completion_rate"],
            numerator=len(action_success),
            denominator=len(action_rows),
            value=_ratio(len(action_success), len(action_rows)),
            unit="percent",
            drilldown_ticket_ids=action_fail_ids,
        ),
        _metric(
            definitions["customer_notification_compliance"],
            numerator=len(notification_success),
            denominator=len(notification_rows),
            value=_ratio(len(notification_success), len(notification_rows)),
            unit="percent",
            drilldown_ticket_ids=notification_fail_ids,
        ),
        _metric(
            definitions["provider_failure_rate"],
            numerator=len(provider_failures),
            denominator=len(provider_rows),
            value=_ratio(len(provider_failures), len(provider_rows)),
            unit="percent",
            drilldown_ticket_ids=[row.ticket_id for row in provider_failures],
        ),
        _metric(
            definitions["ai_containment_with_quality"],
            numerator=len(ai_contained),
            denominator=len(ai_rows),
            value=_ratio(len(ai_contained), len(ai_rows)),
            unit="percent",
            drilldown_ticket_ids=[
                ticket.id
                for turn, conversation, ticket in ai_rows
                if ticket is not None and turn not in ai_contained
            ],
        ),
        _metric(
            definitions["cost_per_resolved_case"],
            numerator=None,
            denominator=len(safe_closed_ids),
            value=None,
            unit="currency_per_case",
            available=False,
            unavailable_reason="approved_cost_ledger_not_configured",
        ),
    ]
    return {
        "schema": "nexus.outcome-metrics-result.v1",
        "definition_version": authority["version"],
        "generated_at": observed_at.isoformat(),
        "window": {
            "days": resolved_window,
            "start": window_start.isoformat(),
            "end": observed_at.isoformat(),
        },
        "scope": {
            "tenant_id": actor_tenant_id,
            "tenant_key": tenant_key,
            "team_id": None if has_global_case_visibility(current_user, db) else current_user.team_id,
        },
        "metrics": metrics,
        "contains_customer_content": False,
    }
