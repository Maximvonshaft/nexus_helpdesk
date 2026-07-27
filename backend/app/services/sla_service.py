from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.orm import Session, object_session

from ..enums import EventType, TicketPriority, TicketStatus
from ..models import SLAPolicy, Ticket
from ..models_case_governance import (
    SLAPolicyRevision,
    TicketSLAAssignment,
    TicketSLAPauseInterval,
)
from ..models_sla_runtime import TicketSLATarget
from ..utils.time import ensure_utc, utc_now
from .audit_service import log_event

UTC = ZoneInfo("UTC")
DEFAULT_RISK_WINDOW_MINUTES = 30
DEFAULT_POLICY_EFFECTIVE_FROM = datetime(1970, 1, 1, tzinfo=UTC)
DEFAULTS: dict[TicketPriority, tuple[int, int]] = {
    TicketPriority.low: (240, 2880),
    TicketPriority.medium: (120, 1440),
    TicketPriority.high: (60, 720),
    TicketPriority.urgent: (15, 240),
}


class SLAConfigurationError(RuntimeError):
    """Raised for explicit SLA administration or policy-integrity failures."""


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _runtime_session(ticket: Ticket, db: Session | None) -> Session | None:
    return db or object_session(ticket)


def _default_snapshot(
    policy: SLAPolicy,
    revision_id: int | None = None,
) -> dict[str, Any]:
    pause_reasons: list[str] = []
    if policy.pause_on_waiting_customer:
        pause_reasons.append("waiting_customer")
    if policy.pause_on_waiting_internal:
        pause_reasons.append("waiting_internal")
    return {
        "schema": "nexus.sla-assignment.v1",
        "policy_id": policy.id,
        "policy_revision_id": revision_id,
        "policy_version": 1,
        "priority": _enum_value(policy.priority),
        "timezone_name": "UTC",
        "weekly_schedule": {},
        "holidays": [],
        "first_response_minutes": int(policy.first_response_minutes),
        "resolution_minutes": int(policy.resolution_minutes),
        "action_minutes": None,
        "notification_minutes": None,
        "risk_window_minutes": DEFAULT_RISK_WINDOW_MINUTES,
        "pause_reasons": pause_reasons,
        "scope": {"global_template": True},
    }


def _revision_snapshot(
    revision: SLAPolicyRevision,
    policy: SLAPolicy,
) -> dict[str, Any]:
    return {
        "schema": "nexus.sla-assignment.v1",
        "policy_id": policy.id,
        "policy_revision_id": revision.id,
        "policy_version": revision.version,
        "priority": _enum_value(policy.priority),
        "timezone_name": revision.timezone_name,
        "weekly_schedule": revision.weekly_schedule_json or {},
        "holidays": revision.holidays_json or [],
        "first_response_minutes": revision.first_response_minutes,
        "resolution_minutes": revision.resolution_minutes,
        "action_minutes": revision.action_minutes,
        "notification_minutes": revision.notification_minutes,
        "risk_window_minutes": revision.risk_window_minutes,
        "pause_reasons": revision.pause_reasons_json or [],
        "scope": {
            "tenant_id": revision.tenant_id,
            "global_template": bool(revision.is_global_template),
            "market_id": revision.market_id,
            "channel_key": revision.channel_key,
            "scenario_key": revision.scenario_key,
        },
    }


def seed_default_sla_policies(db: Session) -> None:
    """Create the one global default policy/revision set for controlled bootstrap."""

    for priority, (first_minutes, resolution_minutes) in DEFAULTS.items():
        policy = (
            db.query(SLAPolicy)
            .filter(SLAPolicy.priority == priority)
            .first()
        )
        if policy is None:
            policy = SLAPolicy(
                name=f"{priority.value.title()} SLA",
                priority=priority,
                first_response_minutes=first_minutes,
                resolution_minutes=resolution_minutes,
                pause_on_waiting_customer=True,
                pause_on_waiting_internal=False,
            )
            db.add(policy)
            db.flush()
        revision = (
            db.query(SLAPolicyRevision)
            .filter(
                SLAPolicyRevision.policy_id == policy.id,
                SLAPolicyRevision.version == 1,
            )
            .first()
        )
        if revision is None:
            snapshot = _default_snapshot(policy)
            db.add(
                SLAPolicyRevision(
                    policy_id=policy.id,
                    version=1,
                    tenant_id=None,
                    is_global_template=True,
                    market_id=None,
                    channel_key=None,
                    scenario_key=None,
                    status="approved",
                    timezone_name="UTC",
                    weekly_schedule_json={},
                    holidays_json=[],
                    first_response_minutes=snapshot["first_response_minutes"],
                    resolution_minutes=snapshot["resolution_minutes"],
                    action_minutes=None,
                    notification_minutes=None,
                    risk_window_minutes=DEFAULT_RISK_WINDOW_MINUTES,
                    pause_reasons_json=snapshot["pause_reasons"],
                    effective_from=DEFAULT_POLICY_EFFECTIVE_FROM,
                    effective_to=None,
                    approved_by=None,
                    created_at=utc_now(),
                )
            )
    db.commit()


def get_policy_for_priority(
    db: Session,
    priority: TicketPriority,
) -> Optional[SLAPolicy]:
    return (
        db.query(SLAPolicy)
        .filter(SLAPolicy.priority == priority)
        .first()
    )


def _scenario_key(ticket: Ticket) -> str:
    return str(
        ticket.case_type
        or ticket.sub_category
        or ticket.category
        or ticket.ai_classification
        or ""
    ).strip().lower()


def _active_revisions(
    db: Session,
    ticket: Ticket,
    *,
    at: datetime,
) -> list[tuple[SLAPolicyRevision, SLAPolicy]]:
    rows = (
        db.query(SLAPolicyRevision, SLAPolicy)
        .join(SLAPolicy, SLAPolicy.id == SLAPolicyRevision.policy_id)
        .filter(
            SLAPolicy.priority == ticket.priority,
            SLAPolicyRevision.status == "approved",
            SLAPolicyRevision.effective_from <= at,
            (
                SLAPolicyRevision.effective_to.is_(None)
                | (SLAPolicyRevision.effective_to > at)
            ),
        )
        .all()
    )
    channel = _enum_value(ticket.source_channel)
    scenario = _scenario_key(ticket)
    eligible: list[tuple[SLAPolicyRevision, SLAPolicy]] = []
    for revision, policy in rows:
        if revision.is_global_template:
            if revision.tenant_id is not None:
                continue
        elif ticket.tenant_id is None or revision.tenant_id != ticket.tenant_id:
            continue
        if revision.market_id is not None and revision.market_id != ticket.market_id:
            continue
        if revision.channel_key is not None and revision.channel_key != channel:
            continue
        if revision.scenario_key is not None and revision.scenario_key != scenario:
            continue
        eligible.append((revision, policy))
    return eligible


def select_policy_revision(
    db: Session,
    ticket: Ticket,
    *,
    at: datetime | None = None,
) -> tuple[SLAPolicyRevision, SLAPolicy] | None:
    observed_at = ensure_utc(at or utc_now()) or utc_now()
    rows = _active_revisions(db, ticket, at=observed_at)

    def rank(item: tuple[SLAPolicyRevision, SLAPolicy]):
        revision, _ = item
        score = 0
        if revision.tenant_id is not None:
            score += 16
        if revision.market_id is not None:
            score += 8
        if revision.channel_key is not None:
            score += 4
        if revision.scenario_key is not None:
            score += 2
        return (
            score,
            ensure_utc(revision.effective_from)
            or DEFAULT_POLICY_EFFECTIVE_FROM,
            revision.version,
            revision.id,
        )

    return max(rows, key=rank) if rows else None


def _assignment(
    db: Session,
    ticket_id: int,
) -> TicketSLAAssignment | None:
    return (
        db.query(TicketSLAAssignment)
        .filter(TicketSLAAssignment.ticket_id == ticket_id)
        .first()
    )


def ensure_ticket_sla_assignment(
    db: Session,
    ticket: Ticket,
    *,
    assigned_by: int | None = None,
    at: datetime | None = None,
) -> TicketSLAAssignment:
    """Assign one immutable approved revision.

    This is an explicit governance operation and therefore fails closed when no
    approved revision exists. Runtime customer communication paths use the
    non-blocking evaluation functions below and never synthesize a policy.
    """

    existing = _assignment(db, ticket.id)
    if existing is not None:
        return existing
    selected = select_policy_revision(db, ticket, at=at)
    if selected is None:
        raise SLAConfigurationError("approved_sla_revision_missing")
    revision, policy = selected
    assignment = TicketSLAAssignment(
        ticket_id=ticket.id,
        policy_revision_id=revision.id,
        snapshot_json=_revision_snapshot(revision, policy),
        assigned_at=ensure_utc(at or utc_now()) or utc_now(),
        assigned_by=assigned_by,
    )
    db.add(assignment)
    db.flush()
    ticket.sla_policy_id = policy.id
    return assignment


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise SLAConfigurationError(f"sla_timezone_invalid:{name}") from exc


def _clock(value: str) -> time:
    try:
        hour_text, minute_text = value.split(":", 1)
        result = time(hour=int(hour_text), minute=int(minute_text))
    except (AttributeError, TypeError, ValueError) as exc:
        raise SLAConfigurationError("sla_schedule_time_invalid") from exc
    if result.second or result.microsecond:
        raise SLAConfigurationError("sla_schedule_time_invalid")
    return result


def _intervals_for_day(
    schedule: dict[str, Any],
    current_date: date,
) -> list[tuple[time, time]]:
    raw = schedule.get(current_date.strftime("%A").lower(), [])
    intervals: list[tuple[time, time]] = []
    for item in raw:
        if isinstance(item, dict):
            start_raw, end_raw = item.get("start"), item.get("end")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            start_raw, end_raw = item
        else:
            raise SLAConfigurationError("sla_schedule_interval_invalid")
        start, end = _clock(str(start_raw)), _clock(str(end_raw))
        if end <= start:
            raise SLAConfigurationError("sla_schedule_interval_order_invalid")
        intervals.append((start, end))
    intervals.sort(key=lambda value: value[0])
    for previous, current in zip(intervals, intervals[1:]):
        if current[0] < previous[1]:
            raise SLAConfigurationError("sla_schedule_interval_overlap")
    return intervals


def _holiday_dates(holidays: list[Any] | None) -> set[str]:
    return {str(value)[:10] for value in (holidays or [])}


def add_business_seconds(
    start: datetime,
    seconds: int,
    *,
    timezone_name: str,
    weekly_schedule: dict[str, Any] | None,
    holidays: list[Any] | None,
) -> datetime:
    base = ensure_utc(start)
    if base is None:
        raise SLAConfigurationError("sla_start_time_required")
    if seconds < 0:
        raise SLAConfigurationError("sla_seconds_negative")
    if seconds == 0:
        return base
    schedule = weekly_schedule or {}
    if not schedule:
        return base + timedelta(seconds=seconds)

    zone = _zone(timezone_name)
    local = base.astimezone(zone)
    holiday_dates = _holiday_dates(holidays)
    remaining = timedelta(seconds=seconds)
    for _ in range(3700):
        day = local.date()
        if day.isoformat() not in holiday_dates:
            for start_clock, end_clock in _intervals_for_day(schedule, day):
                interval_start = datetime.combine(day, start_clock, tzinfo=zone)
                interval_end = datetime.combine(day, end_clock, tzinfo=zone)
                cursor = max(local, interval_start)
                if cursor >= interval_end:
                    continue
                available = interval_end - cursor
                if remaining <= available:
                    return (cursor + remaining).astimezone(UTC)
                remaining -= available
        local = datetime.combine(
            day + timedelta(days=1),
            time.min,
            tzinfo=zone,
        )
    raise SLAConfigurationError("sla_schedule_horizon_exceeded")


def add_business_minutes(
    start: datetime,
    minutes: int,
    *,
    timezone_name: str,
    weekly_schedule: dict[str, Any] | None,
    holidays: list[Any] | None,
) -> datetime:
    if minutes < 0:
        raise SLAConfigurationError("sla_minutes_negative")
    return add_business_seconds(
        start,
        minutes * 60,
        timezone_name=timezone_name,
        weekly_schedule=weekly_schedule,
        holidays=holidays,
    )


def business_seconds_between(
    start: datetime,
    end: datetime,
    *,
    timezone_name: str,
    weekly_schedule: dict[str, Any] | None,
    holidays: list[Any] | None,
) -> int:
    start_utc = ensure_utc(start)
    end_utc = ensure_utc(end)
    if start_utc is None or end_utc is None:
        raise SLAConfigurationError("sla_pause_interval_invalid")
    if end_utc <= start_utc:
        return 0
    schedule = weekly_schedule or {}
    if not schedule:
        return int((end_utc - start_utc).total_seconds())

    zone = _zone(timezone_name)
    holidays_set = _holiday_dates(holidays)
    current_day = start_utc.astimezone(zone).date()
    final_day = end_utc.astimezone(zone).date()
    total = 0.0
    for _ in range(3700):
        if current_day > final_day:
            break
        if current_day.isoformat() not in holidays_set:
            for start_clock, end_clock in _intervals_for_day(
                schedule,
                current_day,
            ):
                interval_start = datetime.combine(
                    current_day,
                    start_clock,
                    tzinfo=zone,
                ).astimezone(UTC)
                interval_end = datetime.combine(
                    current_day,
                    end_clock,
                    tzinfo=zone,
                ).astimezone(UTC)
                overlap_start = max(start_utc, interval_start)
                overlap_end = min(end_utc, interval_end)
                if overlap_end > overlap_start:
                    total += (overlap_end - overlap_start).total_seconds()
        current_day += timedelta(days=1)
    else:
        raise SLAConfigurationError("sla_schedule_horizon_exceeded")
    return int(total)


def _pause_intervals(
    db: Session,
    ticket_id: int,
) -> list[TicketSLAPauseInterval]:
    return (
        db.query(TicketSLAPauseInterval)
        .filter(TicketSLAPauseInterval.ticket_id == ticket_id)
        .order_by(TicketSLAPauseInterval.started_at.asc())
        .all()
    )


def _open_pause(
    db: Session,
    ticket_id: int,
) -> TicketSLAPauseInterval | None:
    return (
        db.query(TicketSLAPauseInterval)
        .filter(
            TicketSLAPauseInterval.ticket_id == ticket_id,
            TicketSLAPauseInterval.ended_at.is_(None),
        )
        .first()
    )


def total_pause_seconds(
    db: Session,
    ticket_id: int,
    *,
    now: datetime | None = None,
    snapshot: dict[str, Any] | None = None,
) -> int:
    observed_at = ensure_utc(now or utc_now()) or utc_now()
    if snapshot is None:
        assignment = _assignment(db, ticket_id)
        if assignment is None:
            return 0
        snapshot = assignment.snapshot_json or {}
    timezone_name = str(snapshot.get("timezone_name") or "UTC")
    weekly_schedule = snapshot.get("weekly_schedule") or {}
    holidays = snapshot.get("holidays") or []
    total = 0
    for interval in _pause_intervals(db, ticket_id):
        started = ensure_utc(interval.started_at)
        ended = ensure_utc(interval.ended_at) or observed_at
        if started is None or ended < started:
            continue
        total += business_seconds_between(
            started,
            ended,
            timezone_name=timezone_name,
            weekly_schedule=weekly_schedule,
            holidays=holidays,
        )
    return total


def _target(
    db: Session,
    ticket_id: int,
) -> TicketSLATarget | None:
    return (
        db.query(TicketSLATarget)
        .filter(TicketSLATarget.ticket_id == ticket_id)
        .first()
    )


def _assigned_policy_id(snapshot: dict[str, Any]) -> int:
    try:
        policy_id = int(snapshot["policy_id"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SLAConfigurationError(
            "sla_assignment_policy_identity_invalid"
        ) from exc
    if policy_id <= 0:
        raise SLAConfigurationError("sla_assignment_policy_identity_invalid")
    return policy_id


def apply_policy_to_ticket(
    ticket: Ticket,
    policy: SLAPolicy,
    now: Optional[datetime] = None,
    *,
    db: Session | None = None,
    assigned_by: int | None = None,
) -> None:
    """Apply an explicitly selected policy to the immutable assignment/target."""

    if policy is None:
        raise SLAConfigurationError("sla_policy_missing")
    observed_at = ensure_utc(now or utc_now()) or utc_now()
    base = ensure_utc(ticket.created_at) or observed_at
    runtime_db = _runtime_session(ticket, db)
    if runtime_db is None:
        ticket.sla_policy_id = policy.id
        ticket.first_response_due_at = base + timedelta(
            minutes=policy.first_response_minutes
        )
        ticket.resolution_due_at = base + timedelta(
            minutes=policy.resolution_minutes
        )
        return

    assignment = ensure_ticket_sla_assignment(
        runtime_db,
        ticket,
        assigned_by=assigned_by,
        at=observed_at,
    )
    snapshot = assignment.snapshot_json or _default_snapshot(policy)
    assigned_policy_id = _assigned_policy_id(snapshot)
    timezone_name = str(snapshot.get("timezone_name") or "UTC")
    weekly_schedule = snapshot.get("weekly_schedule") or {}
    holidays = snapshot.get("holidays") or []
    first_due = add_business_minutes(
        base,
        int(snapshot["first_response_minutes"]),
        timezone_name=timezone_name,
        weekly_schedule=weekly_schedule,
        holidays=holidays,
    )
    resolution_due = add_business_minutes(
        base,
        int(snapshot["resolution_minutes"]),
        timezone_name=timezone_name,
        weekly_schedule=weekly_schedule,
        holidays=holidays,
    )
    paused_seconds = total_pause_seconds(
        runtime_db,
        ticket.id,
        now=observed_at,
        snapshot=snapshot,
    )
    if paused_seconds:
        first_due = add_business_seconds(
            first_due,
            paused_seconds,
            timezone_name=timezone_name,
            weekly_schedule=weekly_schedule,
            holidays=holidays,
        )
        resolution_due = add_business_seconds(
            resolution_due,
            paused_seconds,
            timezone_name=timezone_name,
            weekly_schedule=weekly_schedule,
            holidays=holidays,
        )

    risk_window = timedelta(
        minutes=max(0, int(snapshot.get("risk_window_minutes") or 0))
    )
    target = _target(runtime_db, ticket.id)
    values = {
        "assignment_id": assignment.id,
        "first_response_due_at": first_due,
        "resolution_due_at": resolution_due,
        "first_response_risk_at": first_due - risk_window,
        "resolution_risk_at": resolution_due - risk_window,
        "paused_seconds": paused_seconds,
        "calculated_at": observed_at,
        "updated_at": observed_at,
        "source_revision": int(snapshot.get("policy_version") or 1),
    }
    if target is None:
        target = TicketSLATarget(ticket_id=ticket.id, **values)
        runtime_db.add(target)
    else:
        for key, value in values.items():
            setattr(target, key, value)

    ticket.sla_policy_id = assigned_policy_id
    ticket.first_response_due_at = first_due
    ticket.resolution_due_at = resolution_due
    ticket.total_paused_seconds = paused_seconds
    open_interval = _open_pause(runtime_db, ticket.id)
    ticket.sla_paused = open_interval is not None
    ticket.sla_paused_at = (
        open_interval.started_at if open_interval is not None else None
    )
    ticket.sla_pause_reason = (
        open_interval.reason_code if open_interval is not None else None
    )
    runtime_db.flush()


def pause_sla(
    ticket: Ticket,
    reason: str,
    db: Session | None = None,
    *,
    actor_id: int | None = None,
) -> TicketSLAPauseInterval:
    """Open an explicit, governed pause interval."""

    runtime_db = _runtime_session(ticket, db)
    if runtime_db is None:
        raise SLAConfigurationError("sla_unit_of_work_required")
    normalized = " ".join(str(reason or "").strip().split())[:120]
    if not normalized:
        raise SLAConfigurationError("sla_pause_reason_required")
    existing = _open_pause(runtime_db, ticket.id)
    if existing is not None:
        if existing.reason_code != normalized:
            raise SLAConfigurationError("sla_pause_already_open")
        return existing

    assignment = ensure_ticket_sla_assignment(
        runtime_db,
        ticket,
        assigned_by=actor_id,
    )
    allowed = set(
        (assignment.snapshot_json or {}).get("pause_reasons") or []
    )
    if normalized not in allowed:
        raise SLAConfigurationError("sla_pause_reason_not_allowed")

    interval = TicketSLAPauseInterval(
        ticket_id=ticket.id,
        reason_code=normalized,
        started_at=utc_now(),
        ended_at=None,
        started_by=actor_id,
        ended_by=None,
        created_at=utc_now(),
    )
    runtime_db.add(interval)
    runtime_db.flush()

    policy = ticket.sla_policy or get_policy_for_priority(
        runtime_db,
        ticket.priority,
    )
    if policy is None:
        raise SLAConfigurationError("sla_policy_missing")
    apply_policy_to_ticket(
        ticket,
        policy,
        db=runtime_db,
        assigned_by=actor_id,
    )
    return interval


def _clear_pause_projection(ticket: Ticket) -> None:
    ticket.sla_paused = False
    ticket.sla_paused_at = None
    ticket.sla_pause_reason = None


def resume_sla(
    ticket: Ticket,
    db: Session | None = None,
    *,
    actor_id: int | None = None,
) -> TicketSLAPauseInterval | None:
    """Close an existing pause without blocking service on missing SLA config."""

    runtime_db = _runtime_session(ticket, db)
    if runtime_db is None:
        raise SLAConfigurationError("sla_unit_of_work_required")
    interval = _open_pause(runtime_db, ticket.id)
    if interval is None:
        _clear_pause_projection(ticket)
        return None

    interval.ended_at = utc_now()
    interval.ended_by = actor_id
    runtime_db.flush()
    _clear_pause_projection(ticket)

    policy = ticket.sla_policy or get_policy_for_priority(
        runtime_db,
        ticket.priority,
    )
    if policy is not None and _assignment(runtime_db, ticket.id) is not None:
        apply_policy_to_ticket(
            ticket,
            policy,
            db=runtime_db,
            assigned_by=actor_id,
        )
    return interval


def update_pause_state_for_status(
    ticket: Ticket,
    new_status: TicketStatus,
    db: Session,
    *,
    actor_id: int | None = None,
) -> None:
    """Synchronize status-driven pauses when an SLA assignment exists.

    Missing SLA configuration is an operational-readiness condition, not a
    customer-communication blocker. The function therefore leaves the case
    unpaused and returns when no approved assignment can be established.
    """

    assignment = _assignment(db, ticket.id)
    if assignment is None:
        try:
            assignment = ensure_ticket_sla_assignment(
                db,
                ticket,
                assigned_by=actor_id,
            )
        except SLAConfigurationError as exc:
            if str(exc) in {
                "approved_sla_revision_missing",
                "sla_policy_missing",
            }:
                if _open_pause(db, ticket.id) is not None:
                    resume_sla(ticket, db, actor_id=actor_id)
                else:
                    _clear_pause_projection(ticket)
                return
            raise

    pause_reasons = set(
        (assignment.snapshot_json or {}).get("pause_reasons") or []
    )
    reason = None
    if (
        new_status == TicketStatus.waiting_customer
        and "waiting_customer" in pause_reasons
    ):
        reason = "waiting_customer"
    elif (
        new_status == TicketStatus.waiting_internal
        and "waiting_internal" in pause_reasons
    ):
        reason = "waiting_internal"

    if reason is not None:
        pause_sla(ticket, reason, db, actor_id=actor_id)
        return

    closed_interval = resume_sla(ticket, db, actor_id=actor_id)
    if closed_interval is None:
        policy = ticket.sla_policy or get_policy_for_priority(
            db,
            ticket.priority,
        )
        if policy is not None:
            apply_policy_to_ticket(
                ticket,
                policy,
                db=db,
                assigned_by=actor_id,
            )


def update_first_response(ticket: Ticket) -> None:
    if ticket.first_response_at is None:
        ticket.first_response_at = utc_now()


def _runtime_due_times(
    ticket: Ticket,
    db: Session,
    *,
    now: datetime,
) -> tuple[datetime | None, datetime | None]:
    target = _target(db, ticket.id)
    if target is not None and _open_pause(db, ticket.id) is None:
        return (
            ensure_utc(target.first_response_due_at),
            ensure_utc(target.resolution_due_at),
        )

    policy = ticket.sla_policy or get_policy_for_priority(
        db,
        ticket.priority,
    )
    if policy is None:
        return (
            ensure_utc(ticket.first_response_due_at),
            ensure_utc(ticket.resolution_due_at),
        )

    try:
        apply_policy_to_ticket(
            ticket,
            policy,
            now=now,
            db=db,
        )
    except SLAConfigurationError as exc:
        if str(exc) in {
            "approved_sla_revision_missing",
            "sla_assignment_missing",
            "sla_policy_missing",
        }:
            return (
                ensure_utc(ticket.first_response_due_at),
                ensure_utc(ticket.resolution_due_at),
            )
        raise

    target = _target(db, ticket.id)
    return (
        ensure_utc(
            target.first_response_due_at if target is not None else None
        ),
        ensure_utc(
            target.resolution_due_at if target is not None else None
        ),
    )


def compute_sla_snapshot(
    ticket: Ticket,
    db: Session | None = None,
) -> dict[str, bool]:
    """Evaluate breach state without blocking customer service on missing config."""

    now = utc_now()
    runtime_db = _runtime_session(ticket, db)
    if runtime_db is not None:
        first_due, resolution_due = _runtime_due_times(
            ticket,
            runtime_db,
            now=now,
        )
    else:
        first_due = ensure_utc(ticket.first_response_due_at)
        resolution_due = ensure_utc(ticket.resolution_due_at)

    first_at = ensure_utc(ticket.first_response_at)
    status = ticket.status
    first_breached = bool(
        ticket.first_response_breached
        or (
            first_at is None
            and first_due is not None
            and now > first_due
        )
    )
    resolution_breached = bool(
        ticket.resolution_breached
        or (
            status not in {TicketStatus.closed, TicketStatus.canceled}
            and resolution_due is not None
            and now > resolution_due
        )
    )
    overdue = bool(
        status not in {TicketStatus.closed, TicketStatus.canceled}
        and resolution_due is not None
        and now > resolution_due
    )
    return {
        "first_response_breached": first_breached,
        "resolution_breached": resolution_breached,
        "overdue": overdue,
    }


def evaluate_sla(ticket: Ticket, db: Session) -> None:
    previous_first = ticket.first_response_breached
    previous_resolution = ticket.resolution_breached
    snapshot = compute_sla_snapshot(ticket, db)
    ticket.first_response_breached = snapshot["first_response_breached"]
    ticket.resolution_breached = snapshot["resolution_breached"]

    if ticket.first_response_breached and not previous_first:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.sla_breached,
            note="First response SLA breached",
            payload={"scope": "first_response"},
        )
    if ticket.resolution_breached and not previous_resolution:
        log_event(
            db,
            ticket_id=ticket.id,
            actor_id=None,
            event_type=EventType.sla_breached,
            note="Resolution SLA breached",
            payload={"scope": "resolution"},
        )
