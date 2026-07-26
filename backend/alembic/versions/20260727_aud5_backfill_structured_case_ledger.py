"""Backfill legacy closure events into structured Case ledgers.

Revision ID: 20260727_aud5
Revises: 20260727_aud4
Create Date: 2026-07-27
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260727_aud5"
down_revision = "20260727_aud4"
branch_labels = None
depends_on = None


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _as_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    text = str(value or "").strip()
    if text:
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return (
                parsed.replace(tzinfo=timezone.utc)
                if parsed.tzinfo is None
                else parsed
            )
        except ValueError:
            pass
    return fallback


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    ticket_events = sa.Table("ticket_events", metadata, autoload_with=bind)
    evidence = sa.Table("case_evidence_records", metadata, autoload_with=bind)
    outcomes = sa.Table("case_outcome_records", metadata, autoload_with=bind)

    existing_evidence_events = {
        str(row[0])
        for row in bind.execute(
            sa.select(evidence.c.source_ref).where(
                evidence.c.source_kind == "legacy_ticket_event"
            )
        )
        if row[0]
    }
    existing_outcome_events = {
        str(row[0])
        for row in bind.execute(
            sa.select(outcomes.c.source_id).where(
                outcomes.c.source_kind == "legacy_ticket_event"
            )
        )
        if row[0]
    }
    next_sequence: dict[int, int] = defaultdict(int)
    for ticket_id, maximum in bind.execute(
        sa.select(
            outcomes.c.ticket_id,
            sa.func.max(outcomes.c.sequence),
        ).group_by(outcomes.c.ticket_id)
    ):
        next_sequence[int(ticket_id)] = int(maximum or 0)

    rows = bind.execute(
        sa.select(
            ticket_events.c.id,
            ticket_events.c.ticket_id,
            ticket_events.c.actor_id,
            ticket_events.c.field_name,
            ticket_events.c.new_value,
            ticket_events.c.payload_json,
            ticket_events.c.created_at,
        ).order_by(ticket_events.c.id.asc())
    )
    for row in rows:
        event_id = int(row.id)
        ticket_id = int(row.ticket_id)
        source_identity = str(event_id)
        payload = _payload(row.payload_json)
        created_at = _as_datetime(
            row.created_at,
            datetime.now(timezone.utc),
        )

        if (
            row.field_name == "closure_evidence"
            and payload.get("schema")
            == "nexus.ticket-closure-evidence.v1"
        ):
            kind = str(payload.get("kind") or "").strip().lower()
            key = str(payload.get("key") or "").strip().lower()
            state = str(payload.get("state") or "").strip().lower()
            original_source_kind = str(
                payload.get("source_kind") or "legacy_ticket_event"
            ).strip().lower()[:80]
            original_source_ref = str(
                payload.get("source_ref") or source_identity
            ).strip()[:200]
            source_revision = str(
                payload.get("source_revision")
                or f"ticket-event:{event_id}"
            ).strip()[:160]
            observed_at = _as_datetime(
                payload.get("observed_at"),
                created_at,
            )

            if (
                kind in {"fact", "customer_input"}
                and key
                and state
                in {"verified", "completed", "waived", "failed"}
            ):
                if source_identity in existing_evidence_events:
                    continue
                safe_metadata = {
                    "original_source_kind": original_source_kind,
                    "original_source_ref_hash": _sha256(
                        original_source_ref
                    )[:16],
                }
                identity = {
                    "schema": "nexus.case-evidence.v1",
                    "ticket_id": ticket_id,
                    "evidence_kind": kind,
                    "evidence_key": key,
                    "state": state,
                    "source_kind": "legacy_ticket_event",
                    "source_ref": source_identity,
                    "source_revision": source_revision,
                    "observed_at": observed_at.isoformat(),
                    "safe_metadata": safe_metadata,
                }
                bind.execute(
                    evidence.insert().values(
                        ticket_id=ticket_id,
                        evidence_kind=kind,
                        evidence_key=key,
                        state=state,
                        source_kind="legacy_ticket_event",
                        source_ref=source_identity,
                        source_revision=source_revision,
                        evidence_sha256=str(
                            payload.get("evidence_sha256")
                            or _sha256(identity)
                        )[:64],
                        safe_metadata_json=safe_metadata,
                        observed_at=observed_at,
                        recorded_by=row.actor_id,
                        created_at=created_at,
                    )
                )
                existing_evidence_events.add(source_identity)
                continue

            if (
                kind in {"action", "outcome", "notification"}
                and key
                and state
                in {"verified", "completed", "waived", "failed"}
            ):
                if source_identity in existing_outcome_events:
                    continue
                if kind == "action":
                    record_type = "execution_attempt"
                    mapped_state = (
                        "succeeded"
                        if state in {"verified", "completed"}
                        else state
                    )
                    safe_payload = {"action_class": key}
                elif kind == "outcome":
                    record_type = "operational_outcome"
                    mapped_state = (
                        "confirmed"
                        if state in {"verified", "completed"}
                        else state
                    )
                    safe_payload = {"outcome_level": key}
                else:
                    record_type = "customer_notification"
                    mapped_state = (
                        "delivered"
                        if state in {"verified", "completed"}
                        else state
                    )
                    safe_payload = (
                        {"waiver_reason": key}
                        if state == "waived"
                        else {"notification_state": key}
                    )
                next_sequence[ticket_id] += 1
                bind.execute(
                    outcomes.insert().values(
                        ticket_id=ticket_id,
                        sequence=next_sequence[ticket_id],
                        record_type=record_type,
                        state=mapped_state,
                        idempotency_key=(
                            f"legacy-ticket-event:{event_id}"
                        ),
                        parent_record_id=None,
                        source_kind="legacy_ticket_event",
                        source_id=source_identity,
                        payload_json={
                            **safe_payload,
                            "legacy_evidence_sha256": str(
                                payload.get("evidence_sha256") or ""
                            )[:64],
                        },
                        occurred_at=observed_at,
                        created_by=row.actor_id,
                        created_at=created_at,
                    )
                )
                existing_outcome_events.add(source_identity)
                continue

        legacy_actions = {
            "speedaf_waybill_lookup": "tracking_lookup",
            "speedaf_work_order": "create_delivery_work_order",
            "speedaf_address_update": "update_address_contact",
            "speedaf_cancel": "cancel_order",
        }
        if (
            row.field_name in legacy_actions
            and str(row.new_value or "").strip().lower()
            == "completed"
        ):
            if source_identity in existing_outcome_events:
                continue
            next_sequence[ticket_id] += 1
            bind.execute(
                outcomes.insert().values(
                    ticket_id=ticket_id,
                    sequence=next_sequence[ticket_id],
                    record_type="execution_attempt",
                    state="succeeded",
                    idempotency_key=(
                        f"legacy-ticket-event:{event_id}"
                    ),
                    parent_record_id=None,
                    source_kind="legacy_ticket_event",
                    source_id=source_identity,
                    payload_json={
                        "action_class": legacy_actions[row.field_name],
                        "job_id": (
                            payload.get("job_id")
                            if isinstance(payload.get("job_id"), int)
                            else None
                        ),
                    },
                    occurred_at=created_at,
                    created_by=row.actor_id,
                    created_at=created_at,
                )
            )
            existing_outcome_events.add(source_identity)


def downgrade() -> None:
    # The backfill is evidence-preserving and intentionally idempotent. Rows are
    # retained when stepping back to aud4; the following schema downgrade owns
    # any table removal explicitly.
    return None
