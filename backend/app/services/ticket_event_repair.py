from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..enums import EventType
from .ticket_event_classification import (
    TicketEventClassificationError,
    resolve_ticket_event_class,
)
from .ticket_event_writer import TicketEventWriter


class TicketEventRepairError(RuntimeError):
    """Base error for bounded historical TicketEvent repair."""


class TicketEventRepairScopeError(TicketEventRepairError):
    """Raised before payload access when Tenant ticket scope is unsafe."""


class TicketEventRepairConflict(TicketEventRepairError):
    """Raised when a row changed after a repair plan was produced."""


_EVENT_CLASS_MAPPING_VERSION = 2


@dataclass(frozen=True)
class TicketEventRepairDecision:
    event_id: int
    event_class: str
    reason: str
    original_digest: str
    replacement_digest: str
    replacement_payload_json: str = field(repr=False)
    replacement_field_name: str | None = field(repr=False)
    replacement_old_value: str | None = field(repr=False)
    replacement_new_value: str | None = field(repr=False)
    replacement_note: str | None = field(repr=False)


@dataclass(frozen=True)
class TicketEventRepairPlan:
    tenant_id: str
    scanned_count: int
    changed_count: int
    unclassified_count: int
    decisions: tuple[TicketEventRepairDecision, ...]
    event_class_counts: tuple[tuple[str, int], ...]
    reason_counts: tuple[tuple[str, int], ...]
    first_event_id: int | None
    last_event_id: int | None
    mapping_version: int = _EVENT_CLASS_MAPPING_VERSION

    def summary(self) -> dict[str, Any]:
        return {
            "schema": "nexus.ticket_event.repair.summary.v1",
            "tenant_id": self.tenant_id,
            "mapping_version": self.mapping_version,
            "scanned_count": self.scanned_count,
            "changed_count": self.changed_count,
            "unclassified_count": self.unclassified_count,
            "event_class_counts": dict(self.event_class_counts),
            "reason_counts": dict(self.reason_counts),
            "first_event_id": self.first_event_id,
            "last_event_id": self.last_event_id,
            "decision_digest": _decision_digest(self.decisions),
        }

    def summary_json(self) -> str:
        return json.dumps(
            self.summary(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )


@dataclass(frozen=True)
class TicketEventRepairApplyResult:
    tenant_id: str
    changed_count: int
    first_event_id: int | None
    last_event_id: int | None


def _validated_scope(
    tenant_id: str, authorized_ticket_ids: set[int] | frozenset[int] | None
) -> tuple[str, frozenset[int]]:
    normalized_tenant = str(tenant_id or "").strip()
    if not normalized_tenant or normalized_tenant.lower() == "default":
        raise TicketEventRepairScopeError(
            "tenant_id must be explicit and cannot use the default fallback"
        )
    if not authorized_ticket_ids:
        raise TicketEventRepairScopeError(
            "authoritative Tenant ticket scope is required"
        )
    normalized_ids: set[int] = set()
    for value in authorized_ticket_ids:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise TicketEventRepairScopeError(
                "authorized ticket IDs must be positive integers"
            )
        normalized_ids.add(value)
    if not normalized_ids:
        raise TicketEventRepairScopeError("authoritative Tenant ticket scope is empty")
    return normalized_tenant, frozenset(normalized_ids)


def _event_type(value: Any) -> EventType | None:
    if isinstance(value, EventType):
        return value
    if isinstance(value, str):
        try:
            return EventType(value)
        except ValueError:
            return None
    return None


def _parse_historical_payload(value: Any) -> tuple[dict[str, Any], str | None]:
    if value is None or value == "":
        return {}, None
    if isinstance(value, Mapping):
        return dict(value), None
    if not isinstance(value, str):
        raw = repr(type(value).__name__).encode("utf-8", errors="replace")
        return {
            "redacted": True,
            "category": "historical_payload_invalid_type",
            "present": True,
            "sha256_prefix": hashlib.sha256(raw).hexdigest()[:16],
        }, "historical_payload_invalid_type"
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        raw = value.encode("utf-8", errors="replace")
        return {
            "redacted": True,
            "category": "historical_payload_invalid_json",
            "present": bool(value),
            "sha256_prefix": hashlib.sha256(raw).hexdigest()[:16],
        }, "historical_payload_invalid_json"
    if not isinstance(decoded, Mapping):
        raw = value.encode("utf-8", errors="replace")
        return {
            "redacted": True,
            "category": "historical_payload_non_mapping",
            "present": decoded is not None,
            "sha256_prefix": hashlib.sha256(raw).hexdigest()[:16],
        }, "historical_payload_non_mapping"
    return dict(decoded), None


def _canonical_payload(value: str | None) -> Any:
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError, json.JSONDecodeError):
        return object()


def _row_state(row: Any) -> dict[str, Any]:
    return {
        "ticket_id": getattr(row, "ticket_id", None),
        "actor_id": getattr(row, "actor_id", None),
        "event_type": getattr(row, "event_type", None),
        "created_at": getattr(row, "created_at", None),
        "payload_json": getattr(row, "payload_json", None),
        "field_name": getattr(row, "field_name", None),
        "old_value": getattr(row, "old_value", None),
        "new_value": getattr(row, "new_value", None),
        "note": getattr(row, "note", None),
    }


def _state_digest(row: Any) -> str:
    encoded = json.dumps(
        _row_state(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replacement_digest(
    *,
    payload_json: str,
    field_name: str | None,
    old_value: str | None,
    new_value: str | None,
    note: str | None,
) -> str:
    encoded = json.dumps(
        {
            "payload_json": payload_json,
            "field_name": field_name,
            "old_value": old_value,
            "new_value": new_value,
            "note": note,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _decision_digest(decisions: Sequence[TicketEventRepairDecision]) -> str:
    encoded = json.dumps(
        [
            {
                "event_id": item.event_id,
                "event_class": item.event_class,
                "reason": item.reason,
                "original_digest": item.original_digest,
                "replacement_digest": item.replacement_digest,
            }
            for item in decisions
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def plan_ticket_event_repairs(
    rows: Iterable[Any],
    *,
    tenant_id: str,
    authorized_ticket_ids: set[int] | frozenset[int] | None,
) -> TicketEventRepairPlan:
    normalized_tenant, ticket_scope = _validated_scope(tenant_id, authorized_ticket_ids)

    scoped_rows = sorted(
        (row for row in rows if getattr(row, "ticket_id", None) in ticket_scope),
        key=lambda item: int(getattr(item, "id", 0)),
    )
    decisions: list[TicketEventRepairDecision] = []
    class_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    unclassified_count = 0

    for row in scoped_rows:
        resolved_type = _event_type(getattr(row, "event_type", None))
        if resolved_type is None:
            unclassified_count += 1
            reason_counts["ticket_event_type_unclassified"] += 1
            continue
        payload, parse_reason = _parse_historical_payload(
            getattr(row, "payload_json", None)
        )
        try:
            event_class = resolve_ticket_event_class(
                resolved_type,
                field_name=getattr(row, "field_name", None),
                payload=payload,
                note=getattr(row, "note", None),
            )
        except TicketEventClassificationError as exc:
            unclassified_count += 1
            reason_counts[str(exc)] += 1
            continue

        class_counts[event_class.value] += 1
        replacement = TicketEventWriter.build(
            ticket_id=int(getattr(row, "ticket_id")),
            actor_id=getattr(row, "actor_id", None),
            event_type=resolved_type,
            event_class=event_class,
            field_name=getattr(row, "field_name", None),
            old_value=getattr(row, "old_value", None),
            new_value=getattr(row, "new_value", None),
            note=getattr(row, "note", None),
            payload=payload,
            created_at=getattr(row, "created_at", None),
        )
        payload_changed = _canonical_payload(
            getattr(row, "payload_json", None)
        ) != _canonical_payload(replacement.payload_json)
        text_changed = any(
            getattr(row, field_name, None) != getattr(replacement, field_name, None)
            for field_name in ("field_name", "old_value", "new_value", "note")
        )
        if not payload_changed and not text_changed:
            continue
        reason = parse_reason or "policy_upgrade"
        reason_counts[reason] += 1
        replacement_payload = replacement.payload_json or "{}"
        decisions.append(
            TicketEventRepairDecision(
                event_id=int(getattr(row, "id")),
                event_class=event_class.value,
                reason=reason,
                original_digest=_state_digest(row),
                replacement_digest=_replacement_digest(
                    payload_json=replacement_payload,
                    field_name=replacement.field_name,
                    old_value=replacement.old_value,
                    new_value=replacement.new_value,
                    note=replacement.note,
                ),
                replacement_payload_json=replacement_payload,
                replacement_field_name=replacement.field_name,
                replacement_old_value=replacement.old_value,
                replacement_new_value=replacement.new_value,
                replacement_note=replacement.note,
            )
        )

    event_ids = [int(getattr(row, "id")) for row in scoped_rows]
    return TicketEventRepairPlan(
        tenant_id=normalized_tenant,
        scanned_count=len(scoped_rows),
        changed_count=len(decisions),
        unclassified_count=unclassified_count,
        decisions=tuple(decisions),
        event_class_counts=tuple(sorted(class_counts.items())),
        reason_counts=tuple(sorted(reason_counts.items())),
        first_event_id=min(event_ids) if event_ids else None,
        last_event_id=max(event_ids) if event_ids else None,
    )


def apply_ticket_event_repairs(
    rows: Iterable[Any],
    plan: TicketEventRepairPlan,
) -> TicketEventRepairApplyResult:
    if not isinstance(plan, TicketEventRepairPlan):
        raise TicketEventRepairError("plan must be a TicketEventRepairPlan")
    row_by_id = {int(getattr(row, "id")): row for row in rows}
    for decision in plan.decisions:
        row = row_by_id.get(decision.event_id)
        if row is None:
            raise TicketEventRepairConflict(
                f"event {decision.event_id} is no longer present"
            )
        if _state_digest(row) != decision.original_digest:
            raise TicketEventRepairConflict(
                f"event {decision.event_id} changed after planning"
            )

    for decision in plan.decisions:
        row = row_by_id[decision.event_id]
        row.payload_json = decision.replacement_payload_json
        row.field_name = decision.replacement_field_name
        row.old_value = decision.replacement_old_value
        row.new_value = decision.replacement_new_value
        row.note = decision.replacement_note

    event_ids = [item.event_id for item in plan.decisions]
    return TicketEventRepairApplyResult(
        tenant_id=plan.tenant_id,
        changed_count=len(plan.decisions),
        first_event_id=min(event_ids) if event_ids else None,
        last_event_id=max(event_ids) if event_ids else None,
    )


__all__ = [
    "TicketEventRepairApplyResult",
    "TicketEventRepairConflict",
    "TicketEventRepairDecision",
    "TicketEventRepairError",
    "TicketEventRepairPlan",
    "TicketEventRepairScopeError",
    "apply_ticket_event_repairs",
    "plan_ticket_event_repairs",
]
