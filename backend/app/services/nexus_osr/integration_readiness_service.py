from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ...models import Ticket, TicketEvent
from ...models_operations_dispatch import OperationsDispatchOutboxRecord
from ...models_osr import CaseContextRecord, RuntimeDecisionAuditRecord
from ...webchat_models import WebchatConversation, WebchatMessage

INTEGRATION_READINESS_SCHEMA_VERSION = "nexus_osr_integration_readiness_v1"
MAX_COUNT = 1_000_000
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d .()\-]{6,}\d)(?!\w)")
_RAW_GROUP_RE = re.compile(r"\b\d{10,24}@g\.us\b", re.I)
_RAW_TRACKING_RE = re.compile(
    r"\b(?=[A-Z0-9._-]{8,48}\b)(?=(?:[A-Z0-9._-]*\d){4})(?=[A-Z0-9._-]*[A-Z])[A-Z0-9][A-Z0-9._-]+\b",
    re.I,
)
_SECRET_RE = re.compile(
    r"(?:\bbearer\s+\S+|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}|"
    r"\b(?:password|secret|api[_-]?key|token)\s*[:=]\s*\S+)",
    re.I,
)


@dataclass(frozen=True)
class IntegrationRuntimeSignals:
    knowledge_policy_retrievable: bool
    live_tracking_routed_to_truth: bool


@dataclass(frozen=True)
class OSRIntegrationReadinessReport:
    status: str
    ready: bool
    evaluated_at: str
    reasons: tuple[str, ...]
    gates: dict[str, dict[str, Any]]
    counts: dict[str, int]
    metrics: tuple[dict[str, Any], ...]
    schema_version: str = INTEGRATION_READINESS_SCHEMA_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "ready": self.ready,
            "evaluated_at": self.evaluated_at,
            "reasons": list(self.reasons),
            "gates": {key: dict(value) for key, value in self.gates.items()},
            "counts": dict(self.counts),
            "metrics": [dict(value) for value in self.metrics],
        }


def _gate(ready: bool, reason: str) -> dict[str, Any]:
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "reason_codes": [] if ready else [reason],
    }


def _bounded(value: Any) -> int:
    try:
        return max(0, min(int(value or 0), MAX_COUNT))
    except (TypeError, ValueError, OverflowError):
        return 0


_SAFE_IDENTIFIER_KEYS = {
    "dispatch_key",
    "destination_group_key",
    "destination_group_hash",
    "tracking_number_hash",
    "routed_group_key",
    "source_id",
    "result_source_id",
}


def _contains_unsafe_runtime_material(value: Any, *, key: str | None = None) -> bool:
    if isinstance(value, dict):
        return any(
            _contains_unsafe_runtime_material(child, key=str(child_key).lower())
            for child_key, child in value.items()
        )
    if isinstance(value, (list, tuple, set)):
        return any(_contains_unsafe_runtime_material(child, key=key) for child in value)
    if not isinstance(value, str):
        return False
    if _EMAIL_RE.search(value) or _RAW_GROUP_RE.search(value) or _SECRET_RE.search(value):
        return True
    safe_identifier = (
        key in _SAFE_IDENTIFIER_KEYS
        or bool(key and key.endswith(("_at", "_date")))
        or bool(key and ("sha256" in key or key.endswith(("_hash", "_digest"))))
        or value.startswith(
            ("sha256:", "ops-dispatch:", "provider-group:", "tracking ending ", "[redacted_")
        )
    )
    return bool(not safe_identifier and (_PHONE_RE.search(value) or _RAW_TRACKING_RE.search(value)))


def build_osr_integration_readiness(
    db: Session,
    *,
    ticket_id: int,
    conversation_id: int,
    tenant_id: str,
    signals: IntegrationRuntimeSignals,
    evaluated_at: datetime | None = None,
) -> OSRIntegrationReadinessReport:
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    try:
        ticket = db.get(Ticket, ticket_id)
        conversation = db.get(WebchatConversation, conversation_id)
        contexts = (
            db.query(CaseContextRecord)
            .filter(
                CaseContextRecord.tenant_id == tenant_id,
                CaseContextRecord.conversation_id == conversation_id,
                CaseContextRecord.ticket_id == ticket_id,
                CaseContextRecord.is_active.is_(True),
            )
            .all()
        )
        audits = (
            db.query(RuntimeDecisionAuditRecord)
            .filter(
                RuntimeDecisionAuditRecord.tenant_id == tenant_id,
                RuntimeDecisionAuditRecord.conversation_id == conversation_id,
                RuntimeDecisionAuditRecord.ticket_id == ticket_id,
            )
            .all()
        )
        outbox = (
            db.query(OperationsDispatchOutboxRecord)
            .filter(OperationsDispatchOutboxRecord.ticket_id == ticket_id)
            .all()
        )
        events = db.query(TicketEvent).filter(TicketEvent.ticket_id == ticket_id).all()
        customer_visible_messages = int(
            db.query(WebchatMessage)
            .filter(
                WebchatMessage.conversation_id == conversation_id,
                WebchatMessage.direction == "agent",
            )
            .count()
        )

        linkage_ready = bool(
            ticket is not None
            and conversation is not None
            and conversation.ticket_id == ticket_id
            and conversation.tenant_key == tenant_id
        )
        context_ready = bool(
            len(contexts) == 1
            and contexts[0].status == "routed"
            and contexts[0].routed_group_key
        )
        escalation_audits = [
            row for row in audits
            if row.allowed and row.next_action in {"create_ticket", "request_handoff"}
        ]
        blocked_tool_audits = [
            row for row in audits
            if not row.allowed and row.next_action == "call_tool"
        ]
        active_dispatch = [
            row for row in outbox
            if row.tenant_key == tenant_id
            and row.country_code == getattr(ticket, "country_code", None)
            and row.channel_key == "whatsapp"
            and row.status in {"pending", "processing", "retryable", "dispatched"}
        ]
        event_notes = {str(row.note or "") for row in events}
        timeline_ready = {
            "Nexus OSR escalation orchestration",
            "Nexus OSR operations routing routed",
        }.issubset(event_notes)

        event_payloads: list[Any] = []
        event_payloads_valid = True
        for event in events:
            try:
                parsed = json.loads(event.payload_json or "{}")
            except (TypeError, ValueError, RecursionError):
                event_payloads_valid = False
                continue
            if not isinstance(parsed, (dict, list)):
                event_payloads_valid = False
                continue
            event_payloads.append(parsed)

        safe_surfaces = {
            "case_contexts": [
                {
                    "safe_tracking_reference": row.safe_tracking_reference,
                    "tracking_number_hash": row.tracking_number_hash,
                    "customer_claim_summary": row.customer_claim_summary,
                    "contact_methods": row.contact_methods_json,
                    "last_mcp_fact": row.last_mcp_fact_json,
                    "handover": row.agent_handover_summary,
                    "routed_group_key": row.routed_group_key,
                }
                for row in contexts
            ],
            "audits": [
                {
                    "decision": row.decision_json,
                    "context": row.case_context_json,
                    "violations": row.violations_json,
                    "warnings": row.warnings_json,
                }
                for row in audits
            ],
            "outbox": [
                {
                    "dispatch_key": row.dispatch_key,
                    "destination_group_key": row.destination_group_key,
                    "destination_group_hash": row.destination_group_hash,
                    "ack": row.provider_acknowledgement,
                    "external_reference": row.external_reference_safe,
                    "error": row.error_summary_redacted,
                }
                for row in outbox
            ],
            "events": event_payloads,
        }
        privacy_ready = event_payloads_valid and not _contains_unsafe_runtime_material(safe_surfaces)
        gates = {
            "fact_routing": _gate(
                signals.knowledge_policy_retrievable and signals.live_tracking_routed_to_truth,
                "fact_routing_boundary_not_proven",
            ),
            "ticket_conversation_linkage": _gate(linkage_ready, "ticket_conversation_linkage_missing"),
            "case_context": _gate(context_ready, "active_routed_case_context_missing"),
            "runtime_audit": _gate(bool(escalation_audits), "escalation_runtime_audit_missing"),
            "tool_policy": _gate(bool(blocked_tool_audits), "blocked_tool_policy_evidence_missing"),
            "operations_dispatch": _gate(len(active_dispatch) == 1, "durable_operations_dispatch_missing"),
            "audit_timeline": _gate(timeline_ready, "integration_audit_timeline_incomplete"),
            "customer_visible_boundary": _gate(customer_visible_messages == 0, "unowned_customer_visible_message_emitted"),
            "privacy": _gate(privacy_ready, "unsafe_material_in_integration_surfaces"),
        }
        reasons = tuple(
            reason
            for gate in gates.values()
            for reason in gate["reason_codes"]
        )
        status = "ready" if not reasons else "not_ready"
        counts = {
            "active_case_contexts": _bounded(len(contexts)),
            "runtime_audits": _bounded(len(audits)),
            "operations_dispatches": _bounded(len(outbox)),
            "ticket_events": _bounded(len(events)),
            "customer_visible_messages": _bounded(customer_visible_messages),
        }
        return OSRIntegrationReadinessReport(
            status=status,
            ready=status == "ready",
            evaluated_at=now.isoformat(),
            reasons=reasons,
            gates=gates,
            counts=counts,
            metrics=(
                {"name": "nexus_osr_integration_ready", "value": 1 if status == "ready" else 0, "labels": {"status": status}},
                {"name": "nexus_osr_integration_dispatches", "value": counts["operations_dispatches"], "labels": {}},
                {"name": "nexus_osr_integration_audits", "value": counts["runtime_audits"], "labels": {}},
            ),
        )
    except Exception:
        return unavailable_osr_integration_readiness(evaluated_at=now)


def unavailable_osr_integration_readiness(*, evaluated_at: datetime | None = None) -> OSRIntegrationReadinessReport:
    now = evaluated_at or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    gate = {"status": "unavailable", "ready": False, "reason_codes": ["osr_integration_readiness_unavailable"]}
    return OSRIntegrationReadinessReport(
        status="unavailable",
        ready=False,
        evaluated_at=now.astimezone(timezone.utc).isoformat(),
        reasons=("osr_integration_readiness_unavailable",),
        gates={"runtime": gate},
        counts={
            "active_case_contexts": 0,
            "runtime_audits": 0,
            "operations_dispatches": 0,
            "ticket_events": 0,
            "customer_visible_messages": 0,
        },
        metrics=({"name": "nexus_osr_integration_ready", "value": 0, "labels": {"status": "unavailable"}},),
    )
