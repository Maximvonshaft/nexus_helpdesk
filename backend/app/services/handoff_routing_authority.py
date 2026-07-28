from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..models import User
from ..models_agent_routing import ConversationControl, OperatorAgentState
from ..models_case_scenario import CaseScenarioAssignment
from ..models_handoff_routing import (
    HandoffRoutingCandidateAttempt,
    HandoffRoutingPlan,
    ROUTING_PLAN_SCHEMA,
)
from ..operator_models import OperatorQueueScopeGrant
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import WebchatHandoffRequest
from .case_scenario_service import current_case_scenario_assignment
from .permissions import resolve_capabilities

HEARTBEAT_TTL_SECONDS = 90
DEFAULT_MAX_GENERATIONS = 3
MAX_BACKOFF_SECONDS = 300


def _conflict(code: str, **details: Any) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": code, **details},
    )


def _normalized_capabilities(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _conflict("handoff_routing_capabilities_invalid")
    values = tuple(
        sorted(
            {
                str(item).strip().lower()
                for item in raw
                if str(item).strip()
            }
        )
    )
    return values


def _plan_contract(
    assignment: CaseScenarioAssignment,
) -> tuple[dict[str, Any], dict[str, Any], tuple[str, ...]]:
    try:
        snapshot = json.loads(assignment.scenario_snapshot_json)
        scenario = snapshot["scenario"]
    except (TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        raise _conflict(
            "handoff_routing_scenario_snapshot_invalid",
            assignment_id=assignment.id,
        ) from exc
    if snapshot.get("schema") != "nexus.case-scenario-assignment.v1":
        raise _conflict(
            "handoff_routing_scenario_snapshot_schema_invalid",
            assignment_id=assignment.id,
        )
    if scenario.get("scenario_key") != assignment.scenario_key:
        raise _conflict(
            "handoff_routing_scenario_identity_mismatch",
            assignment_id=assignment.id,
        )
    queue_key = str(scenario.get("owner_queue_key") or "").strip().lower()
    risk_level = str(scenario.get("risk_level") or "").strip().lower()
    if not queue_key or risk_level not in {"low", "medium", "high", "critical"}:
        raise _conflict(
            "handoff_routing_scenario_contract_invalid",
            assignment_id=assignment.id,
        )
    required = _normalized_capabilities(scenario.get("required_capabilities"))
    return snapshot, scenario, required


def _plan_digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _max_generations(risk_level: str) -> int:
    return 3 if risk_level in {"high", "critical"} else DEFAULT_MAX_GENERATIONS


def routing_plan_for_request(
    db: Session,
    *,
    request_id: int,
    lock: bool = False,
) -> HandoffRoutingPlan | None:
    query = db.query(HandoffRoutingPlan).filter(
        HandoffRoutingPlan.request_id == int(request_id)
    )
    if lock and db.bind and db.bind.dialect.name.startswith("postgresql"):
        query = query.with_for_update()
    return query.one_or_none()


def ensure_handoff_routing_plan(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
) -> HandoffRoutingPlan | None:
    """Create or verify the immutable Scenario-derived plan.

    Ticketless conversations continue to use the existing exact Scope Grant
    contract and intentionally have no synthetic Scenario plan.
    """

    if request_row.ticket_id is None:
        return None
    assignment = current_case_scenario_assignment(
        db,
        ticket_id=int(request_row.ticket_id),
    )
    if assignment is None:
        raise _conflict(
            "handoff_scenario_assignment_required",
            request_id=request_row.id,
            ticket_id=request_row.ticket_id,
        )
    snapshot, scenario, required = _plan_contract(assignment)
    scenario_snapshot_sha256 = hashlib.sha256(
        assignment.scenario_snapshot_json.encode("utf-8")
    ).hexdigest()
    immutable = {
        "schema": ROUTING_PLAN_SCHEMA,
        "request_id": int(request_row.id),
        "ticket_id": int(request_row.ticket_id),
        "scenario_assignment_id": int(assignment.id),
        "scenario_key": assignment.scenario_key,
        "catalog_sha256": assignment.catalog_sha256,
        "scenario_snapshot_sha256": scenario_snapshot_sha256,
        "owner_queue_key": str(scenario["owner_queue_key"]).strip().lower(),
        "required_capabilities": list(required),
        "risk_level": str(scenario["risk_level"]).strip().lower(),
        "escalation_policy_key": (
            str(scenario.get("escalation_policy_key") or "").strip() or None
        ),
        "max_generations": _max_generations(
            str(scenario["risk_level"]).strip().lower()
        ),
    }
    digest = _plan_digest(immutable)
    existing = routing_plan_for_request(
        db,
        request_id=request_row.id,
        lock=True,
    )
    if existing is not None:
        observed = {
            "schema": existing.plan_schema,
            "request_id": existing.request_id,
            "ticket_id": existing.ticket_id,
            "scenario_assignment_id": existing.scenario_assignment_id,
            "scenario_key": existing.scenario_key,
            "catalog_sha256": existing.catalog_sha256,
            "scenario_snapshot_sha256": existing.scenario_snapshot_sha256,
            "owner_queue_key": existing.owner_queue_key,
            "required_capabilities": list(
                _normalized_capabilities(
                    json.loads(existing.required_capabilities_json)
                )
            ),
            "risk_level": existing.risk_level,
            "escalation_policy_key": existing.escalation_policy_key,
            "max_generations": existing.max_generations,
        }
        if existing.plan_digest != digest or observed != immutable:
            raise _conflict(
                "handoff_routing_plan_conflict",
                request_id=request_row.id,
                plan_id=existing.id,
            )
        return existing

    plan = HandoffRoutingPlan(
        request_id=request_row.id,
        ticket_id=request_row.ticket_id,
        scenario_assignment_id=assignment.id,
        scenario_key=assignment.scenario_key,
        catalog_sha256=assignment.catalog_sha256,
        scenario_snapshot_sha256=scenario_snapshot_sha256,
        owner_queue_key=immutable["owner_queue_key"],
        required_capabilities_json=json.dumps(
            list(required),
            sort_keys=True,
            separators=(",", ":"),
        ),
        risk_level=immutable["risk_level"],
        escalation_policy_key=immutable["escalation_policy_key"],
        plan_schema=ROUTING_PLAN_SCHEMA,
        plan_digest=digest,
        status="active",
        current_generation=1,
        max_generations=immutable["max_generations"],
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    db.add(plan)
    db.flush()
    return plan


def _heartbeat_fresh(state: OperatorAgentState) -> bool:
    heartbeat = ensure_utc(state.last_heartbeat_at)
    now = ensure_utc(utc_now())
    return bool(
        heartbeat is not None
        and now is not None
        and heartbeat >= now - timedelta(seconds=HEARTBEAT_TTL_SECONDS)
    )


def _attempted_agent_ids(
    db: Session,
    *,
    plan: HandoffRoutingPlan,
    channel_kind: str,
) -> set[int]:
    rows = (
        db.query(HandoffRoutingCandidateAttempt.agent_id)
        .filter(
            HandoffRoutingCandidateAttempt.plan_id == plan.id,
            HandoffRoutingCandidateAttempt.generation
            == plan.current_generation,
            HandoffRoutingCandidateAttempt.channel_kind == channel_kind,
        )
        .all()
    )
    return {int(row[0]) for row in rows}


def activate_due_generation(
    db: Session,
    *,
    plan: HandoffRoutingPlan,
) -> bool:
    if plan.status != "retry_scheduled":
        return plan.status == "active"
    due = ensure_utc(plan.next_retry_at)
    now = ensure_utc(utc_now())
    if due is None or now is None or due > now:
        return False
    if plan.current_generation >= plan.max_generations:
        plan.status = "exhausted"
        plan.outcome_code = "candidate_exhausted"
        plan.exhausted_at = utc_now()
        plan.next_retry_at = None
        plan.updated_at = utc_now()
        db.flush()
        return False
    plan.current_generation += 1
    plan.status = "active"
    plan.outcome_code = None
    plan.next_retry_at = None
    plan.updated_at = utc_now()
    db.flush()
    return True


def required_capabilities(plan: HandoffRoutingPlan) -> set[str]:
    try:
        return set(_normalized_capabilities(json.loads(plan.required_capabilities_json)))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise _conflict(
            "handoff_routing_plan_capabilities_invalid",
            plan_id=plan.id,
        ) from exc


def candidate_is_authorized(
    db: Session,
    *,
    plan: HandoffRoutingPlan | None,
    control: ConversationControl,
    user: User,
    channel_kind: str,
    exclude_attempted: bool = True,
) -> bool:
    if not user.is_active or not control.country_code:
        return False
    query = db.query(OperatorQueueScopeGrant.id).filter(
        OperatorQueueScopeGrant.user_id == user.id,
        OperatorQueueScopeGrant.tenant_key == control.tenant_key,
        OperatorQueueScopeGrant.country_code == control.country_code,
        OperatorQueueScopeGrant.channel_key == control.channel_key,
        OperatorQueueScopeGrant.enabled.is_(True),
    )
    if plan is not None:
        if not activate_due_generation(db, plan=plan):
            return False
        query = query.filter(
            OperatorQueueScopeGrant.queue_key == plan.owner_queue_key
        )
        if not required_capabilities(plan).issubset(
            resolve_capabilities(user, db)
        ):
            return False
        if exclude_attempted and user.id in _attempted_agent_ids(
            db,
            plan=plan,
            channel_kind=channel_kind,
        ):
            return False
    return query.first() is not None


def eligible_agents(
    db: Session,
    *,
    plan: HandoffRoutingPlan | None,
    control: ConversationControl,
    channel_kind: str,
    require_voice: bool = False,
) -> list[tuple[User, OperatorAgentState]]:
    rows = (
        db.query(User, OperatorAgentState)
        .join(
            OperatorAgentState,
            OperatorAgentState.user_id == User.id,
        )
        .filter(
            User.is_active.is_(True),
            OperatorAgentState.status == "online",
        )
        .order_by(
            OperatorAgentState.updated_at.asc(),
            User.id.asc(),
        )
        .all()
    )
    result: list[tuple[User, OperatorAgentState]] = []
    for user, state in rows:
        if require_voice and not state.voice_enabled:
            continue
        if not _heartbeat_fresh(state):
            continue
        if candidate_is_authorized(
            db,
            plan=plan,
            control=control,
            user=user,
            channel_kind=channel_kind,
        ):
            result.append((user, state))
    return result


def record_candidate_attempt(
    db: Session,
    *,
    plan: HandoffRoutingPlan | None,
    request_id: int,
    agent_id: int,
    channel_kind: str,
    outcome: str,
    reason_code: str | None = None,
    external_ref: str | None = None,
) -> HandoffRoutingCandidateAttempt | None:
    if plan is None:
        return None
    row = (
        db.query(HandoffRoutingCandidateAttempt)
        .filter(
            HandoffRoutingCandidateAttempt.plan_id == plan.id,
            HandoffRoutingCandidateAttempt.generation
            == plan.current_generation,
            HandoffRoutingCandidateAttempt.agent_id == int(agent_id),
            HandoffRoutingCandidateAttempt.channel_kind == channel_kind,
        )
        .one_or_none()
    )
    if row is None:
        row = HandoffRoutingCandidateAttempt(
            plan_id=plan.id,
            request_id=int(request_id),
            generation=plan.current_generation,
            agent_id=int(agent_id),
            channel_kind=channel_kind,
            outcome=outcome,
            reason_code=(reason_code or "")[:160] or None,
            external_ref=(external_ref or "")[:160] or None,
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        db.add(row)
    else:
        row.outcome = outcome
        row.reason_code = (reason_code or "")[:160] or None
        row.external_ref = (external_ref or row.external_ref or "")[:160] or None
        row.updated_at = utc_now()
    db.flush()
    return row


def update_attempt_by_external_ref(
    db: Session,
    *,
    external_ref: str,
    outcome: str,
    reason_code: str | None = None,
) -> HandoffRoutingCandidateAttempt | None:
    row = (
        db.query(HandoffRoutingCandidateAttempt)
        .filter(HandoffRoutingCandidateAttempt.external_ref == external_ref)
        .order_by(HandoffRoutingCandidateAttempt.id.desc())
        .first()
    )
    if row is None:
        return None
    row.outcome = outcome
    row.reason_code = (reason_code or "")[:160] or None
    row.updated_at = utc_now()
    db.flush()
    return row


def schedule_retry_or_exhaust(
    db: Session,
    *,
    plan: HandoffRoutingPlan | None,
    reason_code: str,
) -> str:
    if plan is None:
        return "legacy_unrouted"
    if plan.status in {"assigned", "closed", "exhausted"}:
        return plan.status
    if plan.current_generation >= plan.max_generations:
        plan.status = "exhausted"
        plan.outcome_code = "candidate_exhausted"
        plan.exhausted_at = utc_now()
        plan.next_retry_at = None
    else:
        backoff = min(
            30 * (2 ** max(0, plan.current_generation - 1)),
            MAX_BACKOFF_SECONDS,
        )
        plan.status = "retry_scheduled"
        plan.outcome_code = (reason_code or "no_eligible_candidate")[:160]
        plan.next_retry_at = utc_now() + timedelta(seconds=backoff)
    plan.updated_at = utc_now()
    db.flush()
    return plan.status


def mark_plan_assigned(
    db: Session,
    *,
    plan: HandoffRoutingPlan | None,
    agent_id: int,
) -> None:
    if plan is None:
        return
    plan.status = "assigned"
    plan.assigned_agent_id = int(agent_id)
    plan.outcome_code = "assigned"
    plan.next_retry_at = None
    plan.updated_at = utc_now()
    db.flush()


def close_routing_plan(
    db: Session,
    *,
    request_id: int,
    outcome_code: str,
) -> None:
    plan = routing_plan_for_request(db, request_id=request_id, lock=True)
    if plan is None:
        return
    plan.status = "closed"
    plan.outcome_code = (outcome_code or "closed")[:160]
    plan.next_retry_at = None
    plan.updated_at = utc_now()
    db.flush()


def routing_projection(
    db: Session,
    *,
    request_id: int,
) -> dict[str, Any] | None:
    plan = routing_plan_for_request(db, request_id=request_id)
    if plan is None:
        return None
    return {
        "plan_id": plan.id,
        "schema": plan.plan_schema,
        "plan_digest": plan.plan_digest,
        "scenario_assignment_id": plan.scenario_assignment_id,
        "scenario_key": plan.scenario_key,
        "owner_queue_key": plan.owner_queue_key,
        "required_capabilities": sorted(required_capabilities(plan)),
        "risk_level": plan.risk_level,
        "escalation_policy_key": plan.escalation_policy_key,
        "status": plan.status,
        "current_generation": plan.current_generation,
        "max_generations": plan.max_generations,
        "next_retry_at": (
            plan.next_retry_at.isoformat() if plan.next_retry_at else None
        ),
        "assigned_agent_id": plan.assigned_agent_id,
        "outcome_code": plan.outcome_code,
        "exhausted_at": (
            plan.exhausted_at.isoformat() if plan.exhausted_at else None
        ),
    }


__all__ = [
    "activate_due_generation",
    "candidate_is_authorized",
    "close_routing_plan",
    "eligible_agents",
    "ensure_handoff_routing_plan",
    "mark_plan_assigned",
    "record_candidate_attempt",
    "required_capabilities",
    "routing_plan_for_request",
    "routing_projection",
    "schedule_retry_or_exhaust",
    "update_attempt_by_external_ref",
]
