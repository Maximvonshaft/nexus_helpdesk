from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from sqlalchemy.orm import Session, object_session

from ..models import Ticket, User
from ..utils.time import ensure_utc, utc_now
from ..webchat_models import (
    WebchatConversation,
    WebchatHandoffDecision,
    WebchatHandoffRequest,
)
from .permissions import (
    CAP_OUTBOUND_SEND,
    CAP_WEBCHAT_HANDOFF_ACCEPT,
    resolve_capabilities,
)
from .scenario_assignment_service import (
    TicketScenarioAssignmentError,
    get_assigned_scenario,
    scenario_routing_priority,
)

ROUTING_POLICY_SCHEMA = "nexus.handoff-routing-policy.v1"
DECLINE_TTL_SECONDS = 15 * 60
RELEASE_DECLINE_TTL_SECONDS = 5 * 60
RETRY_DELAY_SECONDS = 30
ROUTING_OUTCOMES = frozenset(
    {
        "waiting",
        "offered",
        "accepted",
        "all_declined",
        "capacity_exhausted",
        "skill_unavailable",
        "scheduled_retry",
        "escalated",
        "fallback_selected",
    }
)


class HandoffRoutingPolicyError(RuntimeError):
    pass


@dataclass(frozen=True)
class HandoffRoutingPolicy:
    ticket_id: int | None
    scenario_key: str | None
    scenario_assignment_revision: int | None
    owner_queue_key: str
    priority: int
    risk_level: str
    required_capabilities: frozenset[str]
    policy_sha256: str
    policy_json: str

    def agent_is_eligible(self, capabilities: Iterable[str]) -> bool:
        normalized = {str(value).strip().lower() for value in capabilities}
        return self.required_capabilities.issubset(normalized)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _ticketless_policy(
    *,
    conversation: WebchatConversation,
) -> HandoffRoutingPolicy:
    payload = {
        "schema": ROUTING_POLICY_SCHEMA,
        "ticket_id": None,
        "scenario_key": None,
        "scenario_assignment_revision": None,
        "owner_queue_key": "human_support",
        "priority": 50,
        "risk_level": "medium",
        "required_capabilities": sorted(
            {CAP_WEBCHAT_HANDOFF_ACCEPT, CAP_OUTBOUND_SEND}
        ),
        "tenant_key": conversation.tenant_key,
        "channel_key": conversation.channel_key,
        "ticketless": True,
    }
    encoded = _canonical_json(payload)
    return HandoffRoutingPolicy(
        ticket_id=None,
        scenario_key=None,
        scenario_assignment_revision=None,
        owner_queue_key="human_support",
        priority=50,
        risk_level="medium",
        required_capabilities=frozenset(payload["required_capabilities"]),
        policy_sha256=_sha256(encoded),
        policy_json=encoded,
    )


def build_handoff_routing_policy(
    db: Session,
    *,
    conversation: WebchatConversation,
    ticket: Ticket | None = None,
) -> HandoffRoutingPolicy:
    resolved_ticket = ticket
    if resolved_ticket is None and conversation.ticket_id is not None:
        resolved_ticket = db.get(Ticket, conversation.ticket_id)
    if resolved_ticket is None:
        if conversation.ticket_id is not None:
            raise HandoffRoutingPolicyError("handoff_ticket_missing")
        return _ticketless_policy(conversation=conversation)

    try:
        assigned = get_assigned_scenario(
            db,
            ticket=resolved_ticket,
            required=True,
        )
    except TicketScenarioAssignmentError as exc:
        raise HandoffRoutingPolicyError(str(exc)) from exc
    assert assigned is not None
    priority = scenario_routing_priority(resolved_ticket, assigned.policy)
    payload = {
        "schema": ROUTING_POLICY_SCHEMA,
        "ticket_id": resolved_ticket.id,
        "scenario_key": assigned.policy.scenario_key,
        "scenario_assignment_revision": assigned.policy.assignment_revision,
        "catalog_version": assigned.policy.catalog_version,
        "catalog_sha256": assigned.policy.catalog_sha256,
        "definition_sha256": assigned.policy.definition_sha256,
        "owner_queue_key": assigned.policy.owner_queue_key,
        "priority": priority,
        "risk_level": assigned.policy.risk_level,
        "required_capabilities": sorted(
            assigned.policy.required_capabilities
        ),
        "tenant_id": resolved_ticket.tenant_id,
        "ticketless": False,
    }
    encoded = _canonical_json(payload)
    return HandoffRoutingPolicy(
        ticket_id=resolved_ticket.id,
        scenario_key=assigned.policy.scenario_key,
        scenario_assignment_revision=assigned.policy.assignment_revision,
        owner_queue_key=assigned.policy.owner_queue_key,
        priority=priority,
        risk_level=assigned.policy.risk_level,
        required_capabilities=assigned.policy.required_capabilities,
        policy_sha256=_sha256(encoded),
        policy_json=encoded,
    )


def persist_handoff_routing_policy(
    request_row: WebchatHandoffRequest,
    policy: HandoffRoutingPolicy,
) -> None:
    request_row.routing_owner = policy.owner_queue_key[:120]
    request_row.routing_policy_sha256 = policy.policy_sha256
    request_row.routing_policy_json = policy.policy_json


def request_policy(
    request_row: WebchatHandoffRequest,
) -> HandoffRoutingPolicy:
    try:
        payload = json.loads(request_row.routing_policy_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HandoffRoutingPolicyError("handoff_routing_policy_invalid") from exc
    if payload.get("schema") != ROUTING_POLICY_SCHEMA:
        raise HandoffRoutingPolicyError("handoff_routing_policy_missing")
    encoded = _canonical_json(payload)
    digest = _sha256(encoded)
    if digest != request_row.routing_policy_sha256:
        raise HandoffRoutingPolicyError("handoff_routing_policy_digest_mismatch")
    try:
        return HandoffRoutingPolicy(
            ticket_id=(
                int(payload["ticket_id"])
                if payload.get("ticket_id") is not None
                else None
            ),
            scenario_key=(
                str(payload["scenario_key"])
                if payload.get("scenario_key")
                else None
            ),
            scenario_assignment_revision=(
                int(payload["scenario_assignment_revision"])
                if payload.get("scenario_assignment_revision") is not None
                else None
            ),
            owner_queue_key=str(payload["owner_queue_key"]),
            priority=int(payload["priority"]),
            risk_level=str(payload["risk_level"]),
            required_capabilities=frozenset(
                str(value).strip().lower()
                for value in payload.get("required_capabilities", [])
            ),
            policy_sha256=digest,
            policy_json=encoded,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HandoffRoutingPolicyError("handoff_routing_policy_invalid") from exc


def user_is_routing_eligible(
    db: Session,
    *,
    user: User,
    request_row: WebchatHandoffRequest,
) -> bool:
    policy = request_policy(request_row)
    return policy.agent_is_eligible(resolve_capabilities(user, db))


def require_user_routing_eligible(
    db: Session,
    *,
    user: User,
    request_row: WebchatHandoffRequest,
) -> HandoffRoutingPolicy:
    policy = request_policy(request_row)
    if not policy.agent_is_eligible(resolve_capabilities(user, db)):
        raise HandoffRoutingPolicyError("agent_scenario_capability_missing")
    return policy


def active_decline_exists(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    user_id: int,
    now: datetime | None = None,
) -> bool:
    observed = ensure_utc(now or utc_now()) or utc_now()
    return bool(
        db.query(WebchatHandoffDecision.id)
        .filter(
            WebchatHandoffDecision.request_id == request_row.id,
            WebchatHandoffDecision.actor_id == user_id,
            WebchatHandoffDecision.routing_generation
            == request_row.routing_generation,
            WebchatHandoffDecision.decision == "declined",
            WebchatHandoffDecision.expires_at.isnot(None),
            WebchatHandoffDecision.expires_at > observed,
        )
        .first()
    )


def record_routing_decline(
    db: Session,
    *,
    request_row: WebchatHandoffRequest,
    user_id: int,
    reason_code: str,
    note: str | None,
    ttl_seconds: int = DECLINE_TTL_SECONDS,
) -> WebchatHandoffDecision:
    now = utc_now()
    existing = (
        db.query(WebchatHandoffDecision)
        .filter(
            WebchatHandoffDecision.request_id == request_row.id,
            WebchatHandoffDecision.actor_id == user_id,
            WebchatHandoffDecision.routing_generation
            == request_row.routing_generation,
            WebchatHandoffDecision.decision == "declined",
            WebchatHandoffDecision.expires_at.isnot(None),
            WebchatHandoffDecision.expires_at > now,
        )
        .order_by(WebchatHandoffDecision.id.desc())
        .first()
    )
    if existing is not None:
        return existing
    row = WebchatHandoffDecision(
        request_id=request_row.id,
        actor_id=user_id,
        routing_generation=request_row.routing_generation,
        decision="declined",
        reason_code=str(reason_code or "agent_declined")[:160],
        note=str(note)[:2000] if note else None,
        expires_at=now + timedelta(seconds=max(60, int(ttl_seconds))),
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


def _record_release_generation_decline(
    request_row: WebchatHandoffRequest,
) -> None:
    db = object_session(request_row)
    if db is None:
        raise HandoffRoutingPolicyError("handoff_release_session_missing")
    conversation = db.get(WebchatConversation, request_row.conversation_id)
    if conversation is None:
        raise HandoffRoutingPolicyError("handoff_release_conversation_missing")
    released_agent_id = conversation.active_agent_id
    if released_agent_id is None:
        raise HandoffRoutingPolicyError("handoff_release_owner_missing")
    record_routing_decline(
        db,
        request_row=request_row,
        user_id=released_agent_id,
        reason_code="agent_released",
        note=None,
        ttl_seconds=RELEASE_DECLINE_TTL_SECONDS,
    )


def start_next_routing_generation(
    request_row: WebchatHandoffRequest,
    *,
    reason_code: str,
) -> None:
    normalized_reason = str(reason_code or "retry")[:160]
    request_row.routing_generation = max(
        1,
        int(request_row.routing_generation or 1) + 1,
    )
    request_row.routing_outcome = "waiting"
    request_row.routing_reason_code = normalized_reason
    request_row.routing_retry_at = None
    request_row.routing_exhausted_at = None
    request_row.routing_fallback_action = None
    request_row.updated_at = utc_now()
    if normalized_reason == "handoff_released":
        _record_release_generation_decline(request_row)


def mark_routing_outcome(
    request_row: WebchatHandoffRequest,
    *,
    outcome: str,
    reason_code: str | None = None,
    owner: str | None = None,
    retry_at: datetime | None = None,
    fallback_action: str | None = None,
) -> None:
    normalized = str(outcome or "").strip().lower()
    if normalized not in ROUTING_OUTCOMES:
        raise HandoffRoutingPolicyError("handoff_routing_outcome_invalid")
    now = utc_now()
    request_row.routing_outcome = normalized
    request_row.routing_reason_code = (
        str(reason_code).strip()[:160] if reason_code else None
    )
    if owner:
        request_row.routing_owner = str(owner).strip()[:120]
    request_row.routing_retry_at = ensure_utc(retry_at)
    request_row.routing_exhausted_at = (
        now
        if normalized
        in {"all_declined", "capacity_exhausted", "skill_unavailable"}
        else None
    )
    request_row.routing_fallback_action = (
        str(fallback_action).strip()[:80] if fallback_action else None
    )
    request_row.updated_at = now


def classify_candidate_exhaustion(
    *,
    scoped_agents: int,
    skilled_agents: int,
    available_agents: int,
    declined_agents: int,
) -> tuple[str, str, str, datetime | None]:
    if scoped_agents <= 0 or skilled_agents <= 0:
        return (
            "skill_unavailable",
            "no_agent_with_required_scope_and_capability",
            "supervisor",
            None,
        )
    if available_agents <= 0 and declined_agents >= skilled_agents:
        return (
            "all_declined",
            "all_eligible_agents_declined_current_generation",
            "queue_supervisor",
            utc_now() + timedelta(seconds=RETRY_DELAY_SECONDS),
        )
    if available_agents <= 0:
        return (
            "capacity_exhausted",
            "eligible_agents_have_no_available_capacity",
            "queue_supervisor",
            utc_now() + timedelta(seconds=RETRY_DELAY_SECONDS),
        )
    return ("waiting", "eligible_candidate_available", "human_support", None)


def routing_projection(
    request_row: WebchatHandoffRequest,
) -> dict[str, Any]:
    policy_projection: dict[str, Any] = {
        "priority": None,
        "risk_level": None,
        "scenario_key": None,
        "scenario_assignment_revision": None,
        "required_capabilities": [],
    }
    try:
        policy = request_policy(request_row)
    except HandoffRoutingPolicyError:
        policy = None
    if policy is not None:
        policy_projection = {
            "priority": policy.priority,
            "risk_level": policy.risk_level,
            "scenario_key": policy.scenario_key,
            "scenario_assignment_revision": (
                policy.scenario_assignment_revision
            ),
            "required_capabilities": sorted(
                policy.required_capabilities
            ),
        }
    return {
        "generation": request_row.routing_generation,
        "outcome": request_row.routing_outcome,
        "reason_code": request_row.routing_reason_code,
        "owner": request_row.routing_owner,
        "retry_at": (
            request_row.routing_retry_at.isoformat()
            if request_row.routing_retry_at
            else None
        ),
        "exhausted_at": (
            request_row.routing_exhausted_at.isoformat()
            if request_row.routing_exhausted_at
            else None
        ),
        "policy_sha256": request_row.routing_policy_sha256,
        "fallback_action": request_row.routing_fallback_action,
        **policy_projection,
    }


__all__ = [
    "DECLINE_TTL_SECONDS",
    "RELEASE_DECLINE_TTL_SECONDS",
    "HandoffRoutingPolicy",
    "HandoffRoutingPolicyError",
    "active_decline_exists",
    "build_handoff_routing_policy",
    "classify_candidate_exhaustion",
    "mark_routing_outcome",
    "persist_handoff_routing_policy",
    "record_routing_decline",
    "request_policy",
    "require_user_routing_eligible",
    "routing_projection",
    "start_next_routing_generation",
    "user_is_routing_eligible",
]
