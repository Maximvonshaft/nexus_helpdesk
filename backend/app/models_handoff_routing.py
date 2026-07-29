from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    inspect,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .utils.time import utc_now

UTCDateTime = DateTime(timezone=True)
ROUTING_PLAN_SCHEMA = "nexus.handoff-routing-plan.v1"
IMMUTABLE_PLAN_FIELDS = (
    "request_id",
    "ticket_id",
    "scenario_assignment_id",
    "scenario_key",
    "catalog_sha256",
    "scenario_snapshot_sha256",
    "owner_queue_key",
    "required_capabilities_json",
    "risk_level",
    "escalation_policy_key",
    "max_generations",
    "plan_schema",
    "plan_digest",
)


class HandoffRoutingPlan(Base):
    """One immutable Scenario-derived routing contract per HandoffRequest.

    Mutable progress fields track bounded generations and the terminal business
    outcome. Candidate selection never rereads the current Scenario catalog.
    """

    __tablename__ = "handoff_routing_plans"
    __table_args__ = (
        UniqueConstraint("request_id", name="uq_handoff_routing_plan_request"),
        CheckConstraint(
            "status IN ('active', 'retry_scheduled', 'assigned', 'exhausted', 'closed')",
            name="ck_handoff_routing_plan_status",
        ),
        CheckConstraint(
            "current_generation BETWEEN 1 AND 10",
            name="ck_handoff_routing_plan_generation",
        ),
        CheckConstraint(
            "max_generations BETWEEN 1 AND 10",
            name="ck_handoff_routing_plan_max_generations",
        ),
        CheckConstraint(
            "current_generation <= max_generations",
            name="ck_handoff_routing_plan_generation_bound",
        ),
        Index(
            "ix_handoff_routing_plan_retry",
            "status",
            "next_retry_at",
        ),
        Index(
            "ix_handoff_routing_plan_queue_status",
            "owner_queue_key",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_handoff_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    scenario_assignment_id: Mapped[int] = mapped_column(
        ForeignKey("case_scenario_assignments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    scenario_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    catalog_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    scenario_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    owner_queue_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    required_capabilities_json: Mapped[str] = mapped_column(Text, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    escalation_policy_key: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    plan_schema: Mapped[str] = mapped_column(
        String(80), nullable=False, default=ROUTING_PLAN_SCHEMA
    )
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", index=True
    )
    current_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_generations: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    assigned_agent_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    outcome_code: Mapped[Optional[str]] = mapped_column(
        String(160), nullable=True, index=True
    )
    exhausted_at: Mapped[Optional[datetime]] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )

    @hybrid_property
    def handoff_request_id(self) -> int:
        """Unambiguous query name backed by the single canonical request_id column."""

        return int(self.request_id)

    @handoff_request_id.inplace.expression
    @classmethod
    def _handoff_request_id_expression(cls):
        return cls.request_id


class HandoffRoutingCandidateAttempt(Base):
    """Generation-scoped candidate outcome shared by Text and Voice routing."""

    __tablename__ = "handoff_routing_candidate_attempts"
    __table_args__ = (
        UniqueConstraint(
            "plan_id",
            "generation",
            "agent_id",
            "channel_kind",
            name="uq_handoff_routing_attempt_candidate_generation",
        ),
        CheckConstraint(
            "channel_kind IN ('text', 'voice', 'manual')",
            name="ck_handoff_routing_attempt_channel",
        ),
        CheckConstraint(
            "outcome IN ('offered', 'accepted', 'declined', 'expired', 'cancelled', 'unavailable')",
            name="ck_handoff_routing_attempt_outcome",
        ),
        Index(
            "ix_handoff_routing_attempt_generation",
            "plan_id",
            "generation",
            "outcome",
        ),
        Index(
            "ix_handoff_routing_attempt_external_ref",
            "external_ref",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("handoff_routing_plans.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    request_id: Mapped[int] = mapped_column(
        ForeignKey("webchat_handoff_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    agent_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel_kind: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    reason_code: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        nullable=False,
        default=utc_now,
        onupdate=utc_now,
        index=True,
    )


@event.listens_for(HandoffRoutingPlan, "before_update")
def _enforce_routing_plan_immutability(mapper, connection, target) -> None:  # noqa: ANN001
    del mapper, connection
    state = inspect(target)
    changed = [
        field
        for field in IMMUTABLE_PLAN_FIELDS
        if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "handoff_routing_plan_immutable:" + ",".join(sorted(changed))
        )
