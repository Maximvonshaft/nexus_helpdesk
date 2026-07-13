from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.enums import EventType
from app.services.ticket_event_classification import (
    TicketEventClassificationError,
    resolve_ticket_event_class,
)
from app.services.ticket_event_repair import plan_ticket_event_repairs
from app.services.ticket_event_writer import (
    TicketEventClass,
    TicketEventWriter,
)


def _row(
    row_id: int,
    *,
    event_type: EventType,
    payload: dict[str, object],
    field_name: str | None = None,
    note: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id,
        ticket_id=1,
        actor_id=None,
        event_type=event_type,
        field_name=field_name,
        old_value=None,
        new_value=None,
        note=note,
        payload_json=json.dumps(payload, ensure_ascii=False),
        created_at=None,
    )


def test_unambiguous_and_visibility_scoped_event_types_resolve_deterministically() -> None:
    assert resolve_ticket_event_class(EventType.outbound_sent) is TicketEventClass.CUSTOMER_VISIBLE
    assert resolve_ticket_event_class(EventType.ai_intake_added) is TicketEventClass.PROVIDER
    assert resolve_ticket_event_class(
        EventType.comment_added,
        payload={"visibility": "external"},
    ) is TicketEventClass.CUSTOMER_VISIBLE
    assert resolve_ticket_event_class(
        EventType.comment_added,
        field_name="email.inbound",
        payload={"mailbox_message_id": "<reply@example.test>"},
    ) is TicketEventClass.PROVIDER
    assert resolve_ticket_event_class(
        EventType.comment_added,
        payload={"visibility": "internal"},
    ) is TicketEventClass.INTERNAL_AUDIT
    with pytest.raises(TicketEventClassificationError, match="comment_visibility_missing"):
        resolve_ticket_event_class(EventType.comment_added, payload={})


def test_field_updated_requires_one_high_confidence_class() -> None:
    assert resolve_ticket_event_class(
        EventType.field_updated,
        payload={"dispatch": {"outbox_id": 7}},
        note="Nexus OSR operations dispatch claimed",
    ) is TicketEventClass.DISPATCH
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="speedaf_voice_callback",
        payload={"job_id": 8, "dedupe_key": "callback-8"},
    ) is TicketEventClass.TOOL
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="webcall.voice.action",
        payload={"voice_session_id": "voice-9"},
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="priority",
        payload={"summary": "Priority updated and SLA recalculated"},
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="qa_knowledge_gap",
        payload={"task_id": 10, "created_resource": True},
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="qa_agent_appeal",
        payload={"task_id": 11, "created": True},
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        payload={"field": "conversation_state", "new_value": "human_review_required"},
        note="Conversation state updated",
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        payload={"fields": ["priority"]},
        note="Lite case fields updated",
    ) is TicketEventClass.INTERNAL_AUDIT

    with pytest.raises(TicketEventClassificationError, match="field_update_unclassified"):
        resolve_ticket_event_class(EventType.field_updated, payload={"summary": "unknown"})
    with pytest.raises(TicketEventClassificationError, match="field_update_unclassified"):
        resolve_ticket_event_class(
            EventType.field_updated,
            field_name="future_unregistered_field",
            payload={"summary": "unknown"},
        )
    with pytest.raises(TicketEventClassificationError, match="field_update_ambiguous"):
        resolve_ticket_event_class(
            EventType.field_updated,
            payload={"dispatch": {"outbox_id": 1}, "tool_name": "x"},
        )


def test_low_cardinality_labels_and_cross_class_join_ids_do_not_create_false_conflicts() -> None:
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="webcall.voice.action",
        payload={
            "voice_session_id": "wv_public_9",
            "provider": "livekit",
            "provider_status": "not_executed",
            "status": "recorded",
        },
        note="WebCall session action recorded",
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.field_updated,
        field_name="speedaf_voice_callback",
        payload={
            "voice_session_id": "wv_public_10",
            "job_id": 10,
            "dedupe_key": "speedaf-voice-callback:10",
            "status": "queued",
        },
        note="Speedaf voice callback queued",
    ) is TicketEventClass.TOOL


def test_repair_preserves_dispatch_tool_and_internal_semantics_and_skips_ambiguity() -> None:
    rows = [
        _row(
            1,
            event_type=EventType.field_updated,
            payload={"dispatch": {"outbox_id": 71, "dispatch_status": "pending"}},
            note="Nexus OSR operations dispatch claimed",
        ),
        _row(
            2,
            event_type=EventType.field_updated,
            field_name="speedaf_voice_callback",
            payload={"job_id": 72, "dedupe_key": "voice-callback-72"},
        ),
        _row(
            3,
            event_type=EventType.internal_note_added,
            payload={"tool_name": "timeline.event.create", "tool_call_log_id": 73},
        ),
        _row(
            4,
            event_type=EventType.field_updated,
            payload={"dispatch": {"outbox_id": 74}, "tool_name": "conflict"},
        ),
        _row(
            5,
            event_type=EventType.field_updated,
            field_name="country_code",
            payload={"summary": "country_code updated"},
        ),
    ]

    plan = plan_ticket_event_repairs(
        rows,
        tenant_id="tenant-a",
        authorized_ticket_ids={1},
    )

    assert plan.mapping_version == 2
    assert plan.changed_count == 4
    assert plan.unclassified_count == 1
    decisions = {decision.event_id: decision for decision in plan.decisions}
    assert decisions[1].event_class == TicketEventClass.DISPATCH.value
    assert decisions[2].event_class == TicketEventClass.TOOL.value
    assert decisions[3].event_class == TicketEventClass.TOOL.value
    assert decisions[5].event_class == TicketEventClass.INTERNAL_AUDIT.value
    assert 4 not in decisions
    assert json.loads(decisions[1].replacement_payload_json)["dispatch"]["outbox_id"] == 71
    assert json.loads(decisions[2].replacement_payload_json)["job_id"] == 72


def test_current_governed_metadata_cannot_conflict_with_unambiguous_event_type() -> None:
    with pytest.raises(TicketEventClassificationError, match="class_conflicts_with_type"):
        resolve_ticket_event_class(
            EventType.outbound_sent,
            payload={
                "event_contract": "nexus.ticket_event.writer.v1",
                "event_class": "internal_audit",
                "schema_version": 1,
            },
        )


def test_governed_metadata_cannot_override_conflicting_field_update_evidence() -> None:
    with pytest.raises(TicketEventClassificationError, match="class_conflicts_with_evidence"):
        resolve_ticket_event_class(
            EventType.field_updated,
            payload={
                "event_contract": "nexus.ticket_event.writer.v1",
                "event_class": "internal_audit",
                "schema_version": 1,
                "dispatch": {"outbox_id": 91, "dispatch_status": "pending"},
            },
        )


def test_governed_metadata_requires_evidence_for_ambiguous_event_types() -> None:
    governed = {
        "event_contract": "nexus.ticket_event.writer.v1",
        "event_class": "internal_audit",
        "schema_version": 1,
    }

    with pytest.raises(TicketEventClassificationError, match="comment_visibility_missing"):
        resolve_ticket_event_class(EventType.comment_added, payload=governed)
    with pytest.raises(TicketEventClassificationError, match="field_update_unclassified"):
        resolve_ticket_event_class(EventType.field_updated, payload=governed)


def test_governed_internal_notes_require_evidence_and_accept_specialized_evidence() -> None:
    base = {
        "event_contract": "nexus.ticket_event.writer.v1",
        "schema_version": 1,
    }

    with pytest.raises(
        TicketEventClassificationError,
        match="internal_note_evidence_missing",
    ):
        resolve_ticket_event_class(
            EventType.internal_note_added,
            payload={**base, "event_class": "internal_audit"},
        )
    assert resolve_ticket_event_class(
        EventType.internal_note_added,
        payload={**base, "event_class": "internal_audit", "note_id": 42},
    ) is TicketEventClass.INTERNAL_AUDIT
    assert resolve_ticket_event_class(
        EventType.internal_note_added,
        payload={
            **base,
            "event_class": "tool",
            "tool_name": "timeline.event.create",
        },
    ) is TicketEventClass.TOOL
    assert resolve_ticket_event_class(
        EventType.internal_note_added,
        payload={
            **base,
            "event_class": "provider",
            "ai_turn_id": 44,
            "public_conversation_id": "wc-public-44",
            "visitor_message_id": 45,
        },
    ) is TicketEventClass.PROVIDER
    assert resolve_ticket_event_class(
        EventType.internal_note_added,
        payload={},
    ) is TicketEventClass.INTERNAL_AUDIT

    for conflicting_class in ("customer_visible", "provider", "tracking", "dispatch"):
        with pytest.raises(
            TicketEventClassificationError,
            match="class_conflicts_with_evidence",
        ):
            resolve_ticket_event_class(
                EventType.internal_note_added,
                payload={**base, "event_class": conflicting_class, "note_id": 43},
            )


def test_internal_note_mixed_evidence_fails_closed() -> None:
    base = {
        "event_contract": "nexus.ticket_event.writer.v1",
        "event_class": "tool",
        "schema_version": 1,
    }

    with pytest.raises(TicketEventClassificationError, match="internal_note_ambiguous"):
        resolve_ticket_event_class(
            EventType.internal_note_added,
            payload={**base, "tool_name": "timeline.event.create", "note_id": 51},
        )
    with pytest.raises(TicketEventClassificationError, match="internal_note_ambiguous"):
        resolve_ticket_event_class(
            EventType.internal_note_added,
            payload={**base, "tool_name": "timeline.event.create", "ai_turn_id": 52},
        )
    with pytest.raises(TicketEventClassificationError, match="internal_note_ambiguous"):
        resolve_ticket_event_class(
            EventType.internal_note_added,
            payload={
                **base,
                "event_class": "provider",
                "ai_turn_id": 53,
                "note_id": 54,
            },
        )


def test_secret_shaped_provider_reference_is_not_merged_after_sanitization() -> None:
    secret_shaped_reference = "sk-" + "proj-" + ("A" * 24)
    row = TicketEventWriter.build(
        ticket_id=42,
        event_type=EventType.outbound_queued,
        event_class=TicketEventClass.CUSTOMER_VISIBLE,
        payload={
            "outbound_message_id": 92,
            "provider_message_id": secret_shaped_reference,
            "status": "queued",
        },
    )

    payload = json.loads(row.payload_json or "{}")
    assert payload["status"] == "queued"
    assert "provider_message_id" not in payload
    assert secret_shaped_reference not in (row.payload_json or "")
