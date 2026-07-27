from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import func
from sqlalchemy.orm import Query, Session

from ..models import Ticket, TicketComment, TicketInternalNote
from ..models_agent_routing import ConversationControl
from ..models_case_governance import CaseOutcomeRecord
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .tenant_authority import tenant_key_for_id

ROOT = Path(__file__).resolve().parents[3]
TARGETS_PATH = ROOT / "config/operations/outcome-metric-targets.v1.json"
REOPEN_WINDOW = timedelta(hours=72)
REPEAT_CONTACT_WINDOW = timedelta(days=7)


class OutcomeMetricConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TicketMetricRow:
    id: int
    customer_id: int | None
    created_at: datetime
    closed_at: datetime | None
    tracking_number: str | None
    scenario_key: str | None

    @property
    def repeat_contact_identity(self) -> tuple[int, str, str] | None:
        tracking = str(self.tracking_number or "").strip().upper()
        scenario = str(self.scenario_key or "").strip().lower()
        if self.customer_id is None or not tracking or not scenario:
            return None
        return self.customer_id, tracking, scenario


@dataclass(frozen=True)
class WindowFacts:
    values: dict[str, tuple[float | None, int, int]]
    source_ticket_ids: tuple[int, ...]
    diagnostics: dict[str, Any]


def _targets() -> dict[str, Any]:
    try:
        payload = json.loads(TARGETS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OutcomeMetricConfigurationError(
            "outcome_metric_targets_unavailable"
        ) from exc
    if payload.get("schema") != "nexus.outcome-metric-targets.v2":
        raise OutcomeMetricConfigurationError("outcome_metric_targets_invalid")
    if not isinstance(payload.get("metrics"), dict):
        raise OutcomeMetricConfigurationError("outcome_metric_targets_empty")
    return payload


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


def _mean(total: int, denominator: int) -> float | None:
    return total / denominator if denominator > 0 else None


def _p90(values: Iterable[int]) -> float | None:
    ordered = sorted(max(0, int(value)) for value in values)
    if not ordered:
        return None
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(0.9 * len(ordered)) - 1),
    )
    return float(ordered[index])


def _status(
    value: float | None,
    *,
    direction: str,
    target: float,
    warning: float,
) -> str:
    if value is None:
        return "unavailable"
    if direction == "min":
        if value >= target:
            return "success"
        if value >= warning:
            return "warning"
        return "danger"
    if direction == "max":
        if value <= target:
            return "success"
        if value <= warning:
            return "warning"
        return "danger"
    raise OutcomeMetricConfigurationError("outcome_metric_direction_invalid")


def _trend(
    current: float | None,
    previous: float | None,
    *,
    direction: str,
) -> tuple[float | None, str]:
    if current is None or previous is None:
        return None, "unavailable"
    delta = current - previous
    tolerance = max(0.0001, abs(previous) * 0.01)
    if abs(delta) <= tolerance:
        return delta, "stable"
    improving = delta > 0 if direction == "min" else delta < 0
    return delta, "improving" if improving else "declining"


def _ticket_rows(
    visible_query: Query,
    *,
    start: datetime,
    end: datetime,
) -> list[TicketMetricRow]:
    rows = (
        visible_query.filter(Ticket.created_at >= start, Ticket.created_at < end)
        .with_entities(
            Ticket.id,
            Ticket.customer_id,
            Ticket.created_at,
            Ticket.closed_at,
            Ticket.tracking_number,
            Ticket.case_type,
            Ticket.sub_category,
            Ticket.category,
            Ticket.ai_classification,
        )
        .all()
    )
    result: list[TicketMetricRow] = []
    for (
        ticket_id,
        customer_id,
        created_at,
        closed_at,
        tracking_number,
        case_type,
        sub_category,
        category,
        ai_classification,
    ) in rows:
        scenario_key = next(
            (
                str(value).strip().lower()
                for value in (
                    case_type,
                    sub_category,
                    category,
                    ai_classification,
                )
                if str(value or "").strip()
            ),
            None,
        )
        result.append(
            TicketMetricRow(
                id=int(ticket_id),
                customer_id=(
                    int(customer_id) if customer_id is not None else None
                ),
                created_at=ensure_utc(created_at) or start,
                closed_at=(
                    ensure_utc(closed_at) if closed_at is not None else None
                ),
                tracking_number=(
                    str(tracking_number).strip()
                    if str(tracking_number or "").strip()
                    else None
                ),
                scenario_key=scenario_key,
            )
        )
    return result


def _payload_bool(row: CaseOutcomeRecord, key: str) -> bool | None:
    payload = row.payload_json if isinstance(row.payload_json, dict) else {}
    value = payload.get(key)
    return value if isinstance(value, bool) else None


def _ancestor_execution_attempt_id(
    record: CaseOutcomeRecord,
    *,
    record_by_id: dict[int, CaseOutcomeRecord],
) -> int | None:
    observed: set[int] = set()
    current: CaseOutcomeRecord | None = record
    while current is not None and current.id not in observed:
        observed.add(current.id)
        if current.record_type == "execution_attempt":
            return current.id
        parent_id = current.parent_record_id
        current = record_by_id.get(parent_id) if parent_id is not None else None
    return None


def _repeat_contacts(
    tickets: list[TicketMetricRow],
) -> tuple[set[int], set[int]]:
    by_identity: dict[tuple[int, str, str], list[TicketMetricRow]] = defaultdict(list)
    eligible: set[int] = set()
    repeated: set[int] = set()
    for row in tickets:
        identity = row.repeat_contact_identity
        if identity is None:
            continue
        eligible.add(row.id)
        by_identity[identity].append(row)
    for rows in by_identity.values():
        ordered = sorted(rows, key=lambda item: (item.created_at, item.id))
        for index, row in enumerate(ordered[1:], start=1):
            prior = ordered[index - 1]
            if row.created_at - prior.created_at <= REPEAT_CONTACT_WINDOW:
                repeated.add(row.id)
    return repeated, eligible


def _window_facts(
    db: Session,
    *,
    visible_query: Query,
    tenant_id: int,
    start: datetime,
    end: datetime,
) -> WindowFacts:
    tickets = _ticket_rows(visible_query, start=start, end=end)
    ticket_ids = [row.id for row in tickets]
    ticket_id_set = set(ticket_ids)
    terminal_ticket_ids = {
        row.id
        for row in tickets
        if row.closed_at is not None and row.closed_at < end
    }

    outcome_rows: list[CaseOutcomeRecord] = []
    if ticket_ids:
        outcome_rows = (
            db.query(CaseOutcomeRecord)
            .filter(
                CaseOutcomeRecord.ticket_id.in_(ticket_ids),
                CaseOutcomeRecord.occurred_at < end,
            )
            .order_by(
                CaseOutcomeRecord.ticket_id.asc(),
                CaseOutcomeRecord.sequence.asc(),
                CaseOutcomeRecord.id.asc(),
            )
            .all()
        )
    record_by_id = {row.id: row for row in outcome_rows}
    outcomes_by_ticket: dict[int, list[CaseOutcomeRecord]] = defaultdict(list)
    for row in outcome_rows:
        outcomes_by_ticket[row.ticket_id].append(row)

    safe_closed: set[int] = set()
    reopen_eligible: set[int] = set()
    reopened_72h: set[int] = set()
    notified: set[int] = set()
    repair_required: set[int] = set()
    first_contact_eligible: set[int] = set()
    first_contact_resolved: set[int] = set()
    execution_attempts: list[CaseOutcomeRecord] = []
    operationally_completed_attempts: set[int] = set()
    provider_receipts: list[CaseOutcomeRecord] = []
    provider_failures = 0

    for ticket_id, records in outcomes_by_ticket.items():
        closure_rows = [
            row
            for row in records
            if row.record_type == "closure_assessment"
            and row.state in {"closed", "reopened"}
        ]
        closed_rows = [row for row in closure_rows if row.state == "closed"]
        reopen_rows = [row for row in closure_rows if row.state == "reopened"]
        if (
            ticket_id in terminal_ticket_ids
            and closure_rows
            and closure_rows[-1].state == "closed"
        ):
            safe_closed.add(ticket_id)

        for closed in closed_rows:
            closed_at = ensure_utc(closed.occurred_at)
            if closed_at is None or closed_at + REOPEN_WINDOW > end:
                continue
            reopen_eligible.add(ticket_id)
            if any(
                (reopened_at := ensure_utc(reopened.occurred_at)) is not None
                and closed_at <= reopened_at <= closed_at + REOPEN_WINDOW
                for reopened in reopen_rows
            ):
                reopened_72h.add(ticket_id)

        assessments = [
            row
            for row in records
            if row.record_type == "closure_assessment"
        ]
        if any(
            _payload_bool(row, "first_contact_eligible") is True
            for row in assessments
        ):
            first_contact_eligible.add(ticket_id)
            if (
                ticket_id in safe_closed
                and any(
                    _payload_bool(row, "first_contact_resolved") is True
                    for row in assessments
                )
                and ticket_id not in reopened_72h
            ):
                first_contact_resolved.add(ticket_id)

        if any(
            row.record_type == "customer_notification"
            and row.state in {"delivered", "confirmed", "waived"}
            for row in records
        ):
            notified.add(ticket_id)

        if any(
            row.state in {"failed", "repair_required"}
            for row in records
            if row.record_type
            in {"execution_attempt", "provider_receipt", "operational_outcome"}
        ):
            repair_required.add(ticket_id)

        attempts = [
            row
            for row in records
            if row.record_type == "execution_attempt"
            and row.state in {"succeeded", "failed"}
        ]
        execution_attempts.extend(attempts)
        for row in records:
            if (
                row.record_type == "operational_outcome"
                and row.state in {"succeeded", "delivered", "confirmed"}
            ):
                attempt_id = _ancestor_execution_attempt_id(
                    row,
                    record_by_id=record_by_id,
                )
                if attempt_id is not None:
                    operationally_completed_attempts.add(attempt_id)

        receipts = [
            row
            for row in records
            if row.record_type == "provider_receipt"
            and row.state in {"succeeded", "failed", "delivered", "confirmed"}
        ]
        provider_receipts.extend(receipts)
        provider_failures += sum(row.state == "failed" for row in receipts)

    comments_by_ticket: dict[int, int] = defaultdict(int)
    notes_by_ticket: dict[int, int] = defaultdict(int)
    if ticket_ids:
        for ticket_id, count in (
            db.query(TicketComment.ticket_id, func.count(TicketComment.id))
            .filter(
                TicketComment.ticket_id.in_(ticket_ids),
                TicketComment.author_id.is_not(None),
            )
            .group_by(TicketComment.ticket_id)
            .all()
        ):
            comments_by_ticket[int(ticket_id)] = int(count or 0)
        for ticket_id, count in (
            db.query(
                TicketInternalNote.ticket_id,
                func.count(TicketInternalNote.id),
            )
            .filter(
                TicketInternalNote.ticket_id.in_(ticket_ids),
                TicketInternalNote.author_id.is_not(None),
            )
            .group_by(TicketInternalNote.ticket_id)
            .all()
        ):
            notes_by_ticket[int(ticket_id)] = int(count or 0)

    handoffs: list[WebchatHandoffRequest] = []
    if ticket_ids:
        handoffs = (
            db.query(WebchatHandoffRequest)
            .filter(
                WebchatHandoffRequest.ticket_id.in_(ticket_ids),
                WebchatHandoffRequest.requested_at >= start,
                WebchatHandoffRequest.requested_at < end,
            )
            .all()
        )
    accepted_handoffs = [row for row in handoffs if row.accepted_at is not None]
    handoff_waits = [
        int((accepted_at - requested_at).total_seconds())
        for row in accepted_handoffs
        if (accepted_at := ensure_utc(row.accepted_at)) is not None
        and (requested_at := ensure_utc(row.requested_at)) is not None
        and accepted_at >= requested_at
    ]
    handoff_touches: dict[int, int] = defaultdict(int)
    for row in accepted_handoffs:
        if row.ticket_id in ticket_id_set:
            handoff_touches[int(row.ticket_id)] += 1

    touches_by_ticket = {
        ticket_id: comments_by_ticket[ticket_id]
        + notes_by_ticket[ticket_id]
        + handoff_touches[ticket_id]
        for ticket_id in ticket_ids
    }
    total_touches = sum(
        touches_by_ticket[ticket_id] for ticket_id in terminal_ticket_ids
    )

    repeat_contacts, repeat_contact_eligible = _repeat_contacts(tickets)

    tenant_key = tenant_key_for_id(db, tenant_id)
    controls = (
        db.query(ConversationControl, WebchatConversation)
        .join(
            WebchatConversation,
            WebchatConversation.id == ConversationControl.conversation_id,
        )
        .filter(
            ConversationControl.tenant_key == tenant_key,
            ConversationControl.closed_at >= start,
            ConversationControl.closed_at < end,
        )
        .all()
    )
    closed_conversation_ids = [control.conversation_id for control, _ in controls]
    handoff_conversations: set[int] = set()
    if closed_conversation_ids:
        handoff_conversations = {
            int(conversation_id)
            for (conversation_id,) in db.query(
                WebchatHandoffRequest.conversation_id
            )
            .filter(
                WebchatHandoffRequest.conversation_id.in_(
                    closed_conversation_ids
                )
            )
            .distinct()
            .all()
        }
    ai_contained = sum(
        1
        for control, conversation in controls
        if control.outcome == "ai_resolved"
        and conversation.ticket_id is None
        and control.conversation_id not in handoff_conversations
    )

    values: dict[str, tuple[float | None, int, int]] = {
        "safe_effective_closure_rate": (
            _ratio(len(safe_closed), len(terminal_ticket_ids)),
            len(safe_closed),
            len(terminal_ticket_ids),
        ),
        "first_contact_resolution_rate": (
            _ratio(
                len(first_contact_resolved),
                len(first_contact_eligible),
            ),
            len(first_contact_resolved),
            len(first_contact_eligible),
        ),
        "reopen_72h_rate": (
            _ratio(len(reopened_72h), len(reopen_eligible)),
            len(reopened_72h),
            len(reopen_eligible),
        ),
        "repeat_contact_7d_rate": (
            _ratio(
                len(repeat_contacts),
                len(repeat_contact_eligible),
            ),
            len(repeat_contacts),
            len(repeat_contact_eligible),
        ),
        "customer_notification_compliance": (
            _ratio(len(notified & safe_closed), len(safe_closed)),
            len(notified & safe_closed),
            len(safe_closed),
        ),
        "action_operational_completion_rate": (
            _ratio(
                len(operationally_completed_attempts),
                len(execution_attempts),
            ),
            len(operationally_completed_attempts),
            len(execution_attempts),
        ),
        "repair_required_rate": (
            _ratio(len(repair_required), len(ticket_ids)),
            len(repair_required),
            len(ticket_ids),
        ),
        "handoff_acceptance_rate": (
            _ratio(len(accepted_handoffs), len(handoffs)),
            len(accepted_handoffs),
            len(handoffs),
        ),
        "handoff_wait_p90_seconds": (
            _p90(handoff_waits),
            len(handoff_waits),
            len(handoff_waits),
        ),
        "average_human_touches": (
            _mean(total_touches, len(terminal_ticket_ids)),
            total_touches,
            len(terminal_ticket_ids),
        ),
        "provider_failure_rate": (
            _ratio(provider_failures, len(provider_receipts)),
            provider_failures,
            len(provider_receipts),
        ),
    }
    diagnostics = {
        "ai_live_containment": {
            "value": _ratio(ai_contained, len(controls)),
            "numerator": ai_contained,
            "denominator": len(controls),
            "quality_claim": False,
            "reason": (
                "Containment is operational routing data, not proof of customer "
                "resolution quality. It is excluded from scored outcome metrics."
            ),
        },
        "first_contact_resolution_contract": (
            "Only explicit closure-assessment fields first_contact_eligible and "
            "first_contact_resolved enter the metric."
        ),
        "repeat_contact_identity": (
            "customer_id + tracking_number + scenario_key within 7 days"
        ),
    }
    return WindowFacts(
        values=values,
        source_ticket_ids=tuple(ticket_ids),
        diagnostics=diagnostics,
    )


def build_business_outcome_metrics(
    db: Session,
    *,
    visible_query: Query,
    tenant_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    config = _targets()
    observed_at = ensure_utc(now or utc_now()) or utc_now()
    window_days = int(config.get("window_days") or 30)
    current_start = observed_at - timedelta(days=window_days)
    previous_start = current_start - timedelta(days=window_days)
    current = _window_facts(
        db,
        visible_query=visible_query,
        tenant_id=tenant_id,
        start=current_start,
        end=observed_at,
    )
    previous = _window_facts(
        db,
        visible_query=visible_query,
        tenant_id=tenant_id,
        start=previous_start,
        end=current_start,
    )
    items: list[dict[str, Any]] = []
    for key, contract in config["metrics"].items():
        value, numerator, denominator = current.values.get(
            key,
            (None, 0, 0),
        )
        previous_value, _, _ = previous.values.get(
            key,
            (None, 0, 0),
        )
        direction = str(contract["direction"])
        target = float(contract["target"])
        warning = float(contract["warning"])
        delta, trend = _trend(
            value,
            previous_value,
            direction=direction,
        )
        items.append(
            {
                "key": key,
                "label": contract["label"],
                "unit": contract["unit"],
                "value": value,
                "numerator": numerator,
                "denominator": denominator,
                "target": target,
                "warning": warning,
                "direction": direction,
                "status": _status(
                    value,
                    direction=direction,
                    target=target,
                    warning=warning,
                ),
                "previous_value": previous_value,
                "delta": delta,
                "trend": trend,
                "drilldown": contract["drilldown"],
                "definition": contract["definition"],
            }
        )
    return {
        "schema": "nexus.business-outcome-metrics.v2",
        "generated_at": observed_at.isoformat(),
        "window": {
            "days": window_days,
            "start": current_start.isoformat(),
            "end": observed_at.isoformat(),
            "previous_start": previous_start.isoformat(),
            "previous_end": current_start.isoformat(),
        },
        "items": items,
        "source_ticket_count": len(current.source_ticket_ids),
        "contains_customer_data": False,
        "diagnostics": current.diagnostics,
    }
