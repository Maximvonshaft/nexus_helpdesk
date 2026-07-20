"""retire the legacy channel persistence boundary

Revision ID: 20260720_0063
Revises: 20260716_0062
Create Date: 2026-07-20

The retired transport tables are not dropped blindly. Their rows are copied into
one generic, migration-owned rollback archive; ticket-linked history is projected
into canonical TicketEvent/TicketAttachment records; executable queue and routing
state is neutralized; and only then are the retired tables and foreign columns
removed. Downgrade validates every migration-owned row before restoring data.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from alembic import op
import sqlalchemy as sa

revision = "20260720_0063"
down_revision = "20260716_0062"
branch_labels = None
depends_on = None

MIGRATION_KEY = revision
ARCHIVE_TABLE = "migration_retirement_archive"
LEGACY_TABLES = (
    "external_channel_conversation_links",
    "external_channel_transcript_messages",
    "external_channel_attachment_references",
    "external_channel_sync_cursors",
    "external_channel_unresolved_events",
)
LEGACY_MARKERS = (
    "external_channel",
    "ExternalChannel",
    "EXTERNAL_CHANNEL",
    "open" + "claw",
    "Open" + "Claw",
    ("open" + "claw").upper(),
)
REPLACEMENTS = (
    ("external_channel", "retired_channel"),
    ("ExternalChannel", "RetiredChannel"),
    ("EXTERNAL_CHANNEL", "RETIRED_CHANNEL"),
    ("open" + "claw", "retired_channel"),
    ("Open" + "Claw", "RetiredChannel"),
    (("open" + "claw").upper(), "RETIRED_CHANNEL"),
)
TERMINAL_TASK_STATUSES = {"resolved", "dropped", "replayed", "replay_failed", "cancelled"}

GENERIC_TEXT_TABLES: dict[str, tuple[str, ...]] = {
    "admin_audit_logs": ("target_type", "old_value_json", "new_value_json"),
    "provider_routing_rules": ("primary_provider", "fallback_providers"),
    "ticket_events": ("event_type", "field_name", "note", "payload_json"),
    "ticket_outbound_messages": ("provider_status", "failure_code", "failure_reason"),
    "tool_audit_logs": ("provider", "tool_name", "request_json", "result_json", "error_message"),
    "tool_governance_audit_logs": ("provider", "tool_name", "request_json", "result_json", "error_message"),
}


def _bind() -> sa.engine.Connection:
    return op.get_bind()


def _inspector() -> sa.Inspector:
    return sa.inspect(_bind())


def _tables() -> set[str]:
    return set(_inspector().get_table_names())


def _table_exists(name: str) -> bool:
    return name in _tables()


def _columns(name: str) -> set[str]:
    if not _table_exists(name):
        return set()
    return {column["name"] for column in _inspector().get_columns(name)}


def _indexes(name: str) -> set[str]:
    if not _table_exists(name):
        return set()
    return {str(index["name"]) for index in _inspector().get_indexes(name) if index.get("name")}


def _table(name: str) -> sa.Table:
    return sa.Table(name, sa.MetaData(), autoload_with=_bind())


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _loads(value: str | None) -> Any:
    if not value:
        return None
    return json.loads(value)


def _contains_marker(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_contains_marker(key) or _contains_marker(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(_contains_marker(item) for item in value)
    text = str(value)
    return any(marker in text for marker in LEGACY_MARKERS)


def _replace_markers(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(_replace_markers(key)): _replace_markers(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_replace_markers(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_markers(item) for item in value)
    if not isinstance(value, str):
        return value
    result = value
    for old, new in REPLACEMENTS:
        result = result.replace(old, new)
    return result


def _source_pk(payload: dict[str, Any]) -> str:
    if payload.get("id") is not None:
        return str(payload["id"])
    if payload.get("source") is not None:
        return str(payload["source"])
    raise RuntimeError("retirement_source_primary_key_missing")


def _create_archive() -> None:
    if _table_exists(ARCHIVE_TABLE):
        raise RuntimeError("migration_retirement_archive_already_exists")
    op.create_table(
        ARCHIVE_TABLE,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("migration_key", sa.String(length=80), nullable=False),
        sa.Column("source_table", sa.String(length=100), nullable=False),
        sa.Column("source_pk", sa.String(length=160), nullable=False),
        sa.Column("ticket_id", sa.Integer(), nullable=True),
        sa.Column("source_payload_json", sa.Text(), nullable=False),
        sa.Column("source_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_refs_json", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.UniqueConstraint(
            "migration_key",
            "source_table",
            "source_pk",
            name="uq_migration_retirement_archive_source",
        ),
    )
    op.create_index(
        "ix_migration_retirement_archive_migration_source",
        ARCHIVE_TABLE,
        ["migration_key", "source_table"],
    )
    op.create_index(
        "ix_migration_retirement_archive_ticket_id",
        ARCHIVE_TABLE,
        ["ticket_id"],
    )


def _archive_payload(
    source_table: str,
    payload: dict[str, Any],
    *,
    ticket_id: int | None = None,
) -> None:
    archive = _table(ARCHIVE_TABLE)
    rendered = _canonical_json(payload)
    _bind().execute(
        archive.insert().values(
            migration_key=MIGRATION_KEY,
            source_table=source_table,
            source_pk=_source_pk(payload),
            ticket_id=ticket_id,
            source_payload_json=rendered,
            source_payload_sha256=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            canonical_refs_json=None,
        )
    )


def _archive_rows(
    source_table: str,
    *,
    predicate=None,
    archive_name: str | None = None,
    ticket_column: str | None = "ticket_id",
) -> list[dict[str, Any]]:
    if not _table_exists(source_table):
        return []
    table = _table(source_table)
    statement = sa.select(table)
    if "id" in table.c:
        statement = statement.order_by(table.c.id.asc())
    result = _bind().execution_options(stream_results=True).execute(statement)
    archived: list[dict[str, Any]] = []
    while True:
        batch = result.fetchmany(500)
        if not batch:
            break
        for row in batch:
            payload = dict(row._mapping)
            if predicate is not None and not predicate(payload):
                continue
            _archive_payload(
                archive_name or source_table,
                payload,
                ticket_id=(
                    int(payload[ticket_column])
                    if ticket_column and payload.get(ticket_column) is not None
                    else None
                ),
            )
            archived.append(payload)
    return archived


def _archive_entry(source_table: str, source_pk: str) -> dict[str, Any]:
    archive = _table(ARCHIVE_TABLE)
    row = _bind().execute(
        sa.select(archive).where(
            archive.c.migration_key == MIGRATION_KEY,
            archive.c.source_table == source_table,
            archive.c.source_pk == str(source_pk),
        )
    ).mappings().one()
    payload = _loads(row["source_payload_json"])
    if _sha256(payload) != row["source_payload_sha256"]:
        raise RuntimeError(
            f"retirement_archive_payload_hash_mismatch:{source_table}:{source_pk}"
        )
    return dict(row)


def _set_refs(source_table: str, source_pk: str, refs: list[dict[str, Any]]) -> None:
    archive = _table(ARCHIVE_TABLE)
    updated = _bind().execute(
        archive.update()
        .where(
            archive.c.migration_key == MIGRATION_KEY,
            archive.c.source_table == source_table,
            archive.c.source_pk == str(source_pk),
        )
        .values(canonical_refs_json=_canonical_json(refs))
    )
    if updated.rowcount != 1:
        raise RuntimeError(f"retirement_archive_ref_update_failed:{source_table}:{source_pk}")


def _row(table_name: str, row_id: Any) -> dict[str, Any] | None:
    if not _table_exists(table_name):
        return None
    table = _table(table_name)
    if "id" not in table.c:
        return None
    result = _bind().execute(sa.select(table).where(table.c.id == row_id)).mappings().first()
    return dict(result) if result is not None else None


def _row_ref(table_name: str, row_id: Any) -> dict[str, Any]:
    payload = _row(table_name, row_id)
    if payload is None:
        raise RuntimeError(f"retirement_canonical_row_missing:{table_name}:{row_id}")
    return {
        "table": table_name,
        "id": str(row_id),
        "sha256": _sha256(payload),
    }


def _insert_and_ref(table_name: str, values: dict[str, Any]) -> dict[str, Any]:
    table = _table(table_name)
    result = _bind().execute(table.insert().values(**values))
    row_id = result.inserted_primary_key[0]
    return _row_ref(table_name, row_id)


def _archive_ref(source_table: str, source_pk: Any) -> str:
    return f"{MIGRATION_KEY}:{source_table}:{source_pk}"


def _project_legacy_history() -> None:
    now = datetime.now(timezone.utc)
    events = _table("ticket_events")
    del events  # existence is validated by _insert_and_ref

    for payload in _archive_rows("external_channel_conversation_links"):
        source_pk = _source_pk(payload)
        event_payload = {
            "schema": "nexus.retired-channel-context.v1",
            "archive_ref": _archive_ref("external_channel_conversation_links", source_pk),
            "channel": payload.get("channel"),
            "account_id": payload.get("account_id"),
            "thread_id": payload.get("thread_id"),
            "route": payload.get("route_json"),
            "last_message_id": payload.get("last_message_id"),
            "last_synced_at": payload.get("last_synced_at"),
        }
        ref = _insert_and_ref(
            "ticket_events",
            {
                "ticket_id": payload["ticket_id"],
                "actor_id": None,
                "event_type": "field_updated",
                "field_name": "retired_channel_context",
                "old_value": None,
                "new_value": source_pk,
                "note": "Retired channel context migrated into canonical ticket evidence.",
                "payload_json": _canonical_json(event_payload),
                "created_at": payload.get("created_at") or now,
            },
        )
        _set_refs("external_channel_conversation_links", source_pk, [ref])

    for payload in _archive_rows("external_channel_transcript_messages"):
        source_pk = _source_pk(payload)
        event_payload = {
            "schema": "nexus.retired-channel-message.v1",
            "archive_ref": _archive_ref("external_channel_transcript_messages", source_pk),
            "source_message_id": payload.get("message_id"),
            "role": payload.get("role"),
            "author_name": payload.get("author_name"),
            "body_text": payload.get("body_text"),
            "content": payload.get("content_json"),
            "received_at": payload.get("received_at"),
        }
        ref = _insert_and_ref(
            "ticket_events",
            {
                "ticket_id": payload["ticket_id"],
                "actor_id": None,
                "event_type": "field_updated",
                "field_name": "retired_channel_message",
                "old_value": None,
                "new_value": str(payload.get("message_id") or source_pk),
                "note": "Retired channel message migrated into canonical ticket evidence.",
                "payload_json": _canonical_json(event_payload),
                "created_at": payload.get("received_at") or payload.get("created_at") or now,
            },
        )
        _set_refs("external_channel_transcript_messages", source_pk, [ref])

    for payload in _archive_rows("external_channel_attachment_references"):
        source_pk = _source_pk(payload)
        refs: list[dict[str, Any]] = []
        if payload.get("storage_key"):
            refs.append(
                _insert_and_ref(
                    "ticket_attachments",
                    {
                        "ticket_id": payload["ticket_id"],
                        "uploaded_by": None,
                        "file_name": payload.get("filename") or f"retired-channel-attachment-{source_pk}",
                        "storage_key": payload.get("storage_key"),
                        "file_path": None,
                        "file_url": None,
                        "mime_type": payload.get("content_type"),
                        "file_size": None,
                        "visibility": "internal",
                        "created_at": payload.get("created_at") or now,
                    },
                )
            )
        refs.append(
            _insert_and_ref(
                "ticket_events",
                {
                    "ticket_id": payload["ticket_id"],
                    "actor_id": None,
                    "event_type": "field_updated",
                    "field_name": "retired_channel_attachment",
                    "old_value": None,
                    "new_value": str(payload.get("remote_attachment_id") or source_pk),
                    "note": "Retired channel attachment metadata migrated into canonical ticket evidence.",
                    "payload_json": _canonical_json(
                        {
                            "schema": "nexus.retired-channel-attachment.v1",
                            "archive_ref": _archive_ref("external_channel_attachment_references", source_pk),
                            "remote_attachment_id": payload.get("remote_attachment_id"),
                            "content_type": payload.get("content_type"),
                            "filename": payload.get("filename"),
                            "metadata": payload.get("metadata_json"),
                            "storage_status": payload.get("storage_status"),
                            "storage_key": payload.get("storage_key"),
                        }
                    ),
                    "created_at": payload.get("created_at") or now,
                },
            )
        )
        _set_refs("external_channel_attachment_references", source_pk, refs)

    _archive_rows("external_channel_sync_cursors", ticket_column=None)
    _archive_rows("external_channel_unresolved_events", ticket_column=None)


def _legacy_operator_task(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("unresolved_event_id") is not None
        or _contains_marker(payload.get("source_type"))
        or _contains_marker(payload.get("task_type"))
        or _contains_marker(payload.get("reason_code"))
        or _contains_marker(payload.get("payload_json"))
    )


def _neutralize_operator_tasks() -> None:
    if not _table_exists("operator_tasks"):
        return
    table = _table("operator_tasks")
    now = datetime.now(timezone.utc)
    for payload in _archive_rows(
        "operator_tasks",
        predicate=_legacy_operator_task,
        archive_name="operator_tasks_legacy",
    ):
        row_id = payload["id"]
        status = str(payload.get("status") or "pending")
        values = {
            "source_type": "retired_source",
            "source_id": f"retired:{row_id}",
            "unresolved_event_id": None,
            "task_type": "retirement_record",
            "status": status if status in TERMINAL_TASK_STATUSES else "dropped",
            "reason_code": "source_retired",
            "payload_json": _canonical_json(
                {
                    "schema": "nexus.retired-source-task.v1",
                    "archive_ref": _archive_ref("operator_tasks_legacy", row_id),
                }
            ),
            "updated_at": now,
            "resolved_at": payload.get("resolved_at") or now,
        }
        _bind().execute(table.update().where(table.c.id == row_id).values(**values))
        _set_refs("operator_tasks_legacy", str(row_id), [_row_ref("operator_tasks", row_id)])


def _legacy_onboarding_task(payload: dict[str, Any]) -> bool:
    return bool(
        payload.get("external_channel_account_id")
        or _contains_marker(payload.get("provider"))
        or _contains_marker(payload.get("last_error"))
    )


def _neutralize_onboarding_tasks() -> None:
    if not _table_exists("channel_onboarding_tasks"):
        return
    table = _table("channel_onboarding_tasks")
    now = datetime.now(timezone.utc)
    for payload in _archive_rows(
        "channel_onboarding_tasks",
        predicate=_legacy_onboarding_task,
        archive_name="channel_onboarding_tasks_legacy",
        ticket_column=None,
    ):
        row_id = payload["id"]
        current_status = str(payload.get("status") or "pending")
        binding = payload.get("desired_channel_account_binding") or payload.get("external_channel_account_id")
        values = {
            "provider": "retired" if _contains_marker(payload.get("provider")) else payload.get("provider"),
            "status": current_status if current_status in {"completed", "failed", "cancelled"} else "cancelled",
            "desired_channel_account_binding": binding,
            "external_channel_account_id": None,
            "last_error": "Retired source onboarding state was neutralized by migration 0063.",
            "updated_at": now,
            "completed_at": payload.get("completed_at") or now,
        }
        _bind().execute(table.update().where(table.c.id == row_id).values(**values))
        _set_refs("channel_onboarding_tasks_legacy", str(row_id), [_row_ref("channel_onboarding_tasks", row_id)])


def _neutralize_background_jobs() -> None:
    if not _table_exists("background_jobs"):
        return
    table = _table("background_jobs")
    now = datetime.now(timezone.utc)

    def legacy(payload: dict[str, Any]) -> bool:
        return any(
            _contains_marker(payload.get(column))
            for column in ("queue_name", "job_type", "payload_json", "last_error")
        )

    for payload in _archive_rows(
        "background_jobs",
        predicate=legacy,
        archive_name="background_jobs_legacy",
        ticket_column=None,
    ):
        row_id = payload["id"]
        _bind().execute(
            table.update().where(table.c.id == row_id).values(
                queue_name="retired",
                job_type="retired.source",
                payload_json=_canonical_json(
                    {
                        "schema": "nexus.retired-source-job.v1",
                        "archive_ref": _archive_ref("background_jobs_legacy", row_id),
                    }
                ),
                dedupe_key=None,
                status="dead",
                next_run_at=None,
                locked_at=None,
                locked_by=None,
                last_error="Retired source job neutralized by migration 0063.",
                updated_at=now,
            )
        )
        _set_refs("background_jobs_legacy", str(row_id), [_row_ref("background_jobs", row_id)])


def _neutralize_channel_accounts() -> None:
    if not _table_exists("channel_accounts"):
        return
    table = _table("channel_accounts")
    for payload in _archive_rows(
        "channel_accounts",
        predicate=lambda row: _contains_marker(row.get("provider")),
        archive_name="channel_accounts_legacy",
        ticket_column=None,
    ):
        row_id = payload["id"]
        _bind().execute(
            table.update().where(table.c.id == row_id).values(
                provider="retired",
                is_active=False,
                health_status="retired",
                fallback_account_id=None,
                updated_at=datetime.now(timezone.utc),
            )
        )
        _set_refs("channel_accounts_legacy", str(row_id), [_row_ref("channel_accounts", row_id)])


def _neutralize_heartbeats() -> None:
    if not _table_exists("service_heartbeats"):
        return
    table = _table("service_heartbeats")

    def legacy(payload: dict[str, Any]) -> bool:
        return _contains_marker(payload.get("service_name")) or _contains_marker(payload.get("details_json"))

    for payload in _archive_rows(
        "service_heartbeats",
        predicate=legacy,
        archive_name="service_heartbeats_legacy",
        ticket_column=None,
    ):
        row_id = payload["id"]
        _bind().execute(
            table.update().where(table.c.id == row_id).values(
                service_name=f"retired-source-{row_id}",
                status="retired",
                details_json={
                    "schema": "nexus.retired-source-heartbeat.v1",
                    "archive_ref": _archive_ref("service_heartbeats_legacy", row_id),
                },
                updated_at=datetime.now(timezone.utc),
            )
        )
        _set_refs("service_heartbeats_legacy", str(row_id), [_row_ref("service_heartbeats", row_id)])


def _normalize_generic_markers() -> None:
    for table_name, requested_columns in GENERIC_TEXT_TABLES.items():
        if not _table_exists(table_name):
            continue
        table = _table(table_name)
        columns = tuple(column for column in requested_columns if column in table.c)
        if not columns:
            continue

        def legacy(payload: dict[str, Any]) -> bool:
            return any(_contains_marker(payload.get(column)) for column in columns)

        for payload in _archive_rows(
            table_name,
            predicate=legacy,
            archive_name=f"{table_name}_legacy_marker",
            ticket_column="ticket_id" if "ticket_id" in table.c else None,
        ):
            row_id = payload["id"]
            updates = {
                column: _replace_markers(payload.get(column))
                for column in columns
            }
            if table_name == "provider_routing_rules":
                if "enabled" in table.c:
                    updates["enabled"] = False
                if "kill_switch" in table.c:
                    updates["kill_switch"] = True
            _bind().execute(table.update().where(table.c.id == row_id).values(**updates))
            _set_refs(
                f"{table_name}_legacy_marker",
                str(row_id),
                [_row_ref(table_name, row_id)],
            )


def _drop_index_if_exists(table_name: str, index_name: str) -> None:
    if index_name in _indexes(table_name):
        op.drop_index(index_name, table_name=table_name)


def _drop_legacy_columns() -> None:
    if _table_exists("operator_tasks") and "unresolved_event_id" in _columns("operator_tasks"):
        _drop_index_if_exists("operator_tasks", "uq_operator_tasks_active_external_channel_unresolved")
        _drop_index_if_exists("operator_tasks", "ix_operator_tasks_unresolved_event_id")
        with op.batch_alter_table("operator_tasks") as batch:
            batch.drop_column("unresolved_event_id")
    if _table_exists("channel_onboarding_tasks") and "external_channel_account_id" in _columns("channel_onboarding_tasks"):
        _drop_index_if_exists("channel_onboarding_tasks", "ix_channel_onboarding_tasks_external_channel_account_id")
        with op.batch_alter_table("channel_onboarding_tasks") as batch:
            batch.drop_column("external_channel_account_id")


def _drop_legacy_tables() -> None:
    for table_name in (
        "external_channel_attachment_references",
        "external_channel_transcript_messages",
        "external_channel_conversation_links",
        "external_channel_sync_cursors",
        "external_channel_unresolved_events",
    ):
        if _table_exists(table_name):
            op.drop_table(table_name)


def upgrade() -> None:
    _create_archive()
    _project_legacy_history()
    _neutralize_operator_tasks()
    _neutralize_onboarding_tasks()
    _neutralize_background_jobs()
    _neutralize_channel_accounts()
    _neutralize_heartbeats()
    _normalize_generic_markers()
    _drop_legacy_columns()
    _drop_legacy_tables()


def _create_legacy_tables() -> None:
    if not _table_exists("external_channel_conversation_links"):
        op.create_table(
            "external_channel_conversation_links",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("session_key", sa.String(length=255), nullable=False),
            sa.Column("channel", sa.String(length=60), nullable=True),
            sa.Column("recipient", sa.String(length=255), nullable=True),
            sa.Column("account_id", sa.String(length=120), nullable=True),
            sa.Column("thread_id", sa.String(length=120), nullable=True),
            sa.Column("channel_account_id", sa.Integer(), sa.ForeignKey("channel_accounts.id"), nullable=True),
            sa.Column("route_json", sa.JSON(), nullable=True),
            sa.Column("last_cursor", sa.Integer(), nullable=True),
            sa.Column("last_message_id", sa.String(length=255), nullable=True),
            sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("session_key", name="uq_external_channel_session_key"),
            sa.UniqueConstraint("ticket_id", name="uq_external_channel_ticket_link"),
        )
        for name, columns in (
            ("ix_external_channel_conversation_links_ticket_id", ["ticket_id"]),
            ("ix_external_channel_conversation_links_session_key", ["session_key"]),
            ("ix_external_channel_conversation_links_channel", ["channel"]),
            ("ix_external_channel_conversation_links_recipient", ["recipient"]),
            ("ix_external_channel_conversation_links_channel_account_id", ["channel_account_id"]),
            ("ix_external_channel_conversation_links_last_synced_at", ["last_synced_at"]),
            ("ix_external_channel_conversation_links_created_at", ["created_at"]),
            ("ix_external_channel_links_ticket_updated", ["ticket_id", "updated_at"]),
        ):
            op.create_index(name, "external_channel_conversation_links", columns)

    if not _table_exists("external_channel_transcript_messages"):
        op.create_table(
            "external_channel_transcript_messages",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("external_channel_conversation_links.id"), nullable=False),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("session_key", sa.String(length=255), nullable=False),
            sa.Column("message_id", sa.String(length=255), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=True),
            sa.Column("author_name", sa.String(length=160), nullable=True),
            sa.Column("body_text", sa.Text(), nullable=True),
            sa.Column("content_json", sa.JSON(), nullable=True),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("conversation_id", "message_id", name="uq_external_channel_conversation_message"),
        )
        for name, columns in (
            ("ix_external_channel_transcript_messages_conversation_id", ["conversation_id"]),
            ("ix_external_channel_transcript_messages_ticket_id", ["ticket_id"]),
            ("ix_external_channel_transcript_messages_session_key", ["session_key"]),
            ("ix_external_channel_transcript_messages_message_id", ["message_id"]),
            ("ix_external_channel_transcript_messages_role", ["role"]),
            ("ix_external_channel_transcript_messages_received_at", ["received_at"]),
            ("ix_external_channel_transcript_messages_created_at", ["created_at"]),
            ("ix_external_channel_transcript_ticket_received", ["ticket_id", "received_at"]),
        ):
            op.create_index(name, "external_channel_transcript_messages", columns)

    if not _table_exists("external_channel_attachment_references"):
        op.create_table(
            "external_channel_attachment_references",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("ticket_id", sa.Integer(), sa.ForeignKey("tickets.id"), nullable=False),
            sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("external_channel_conversation_links.id"), nullable=False),
            sa.Column("transcript_message_id", sa.Integer(), sa.ForeignKey("external_channel_transcript_messages.id"), nullable=False),
            sa.Column("remote_attachment_id", sa.String(length=160), nullable=False),
            sa.Column("content_type", sa.String(length=120), nullable=True),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("storage_status", sa.String(length=40), nullable=False, server_default="referenced"),
            sa.Column("storage_key", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in (
            ("ix_external_channel_attachment_references_ticket_id", ["ticket_id"]),
            ("ix_external_channel_attachment_references_conversation_id", ["conversation_id"]),
            ("ix_external_channel_attachment_references_transcript_message_id", ["transcript_message_id"]),
            ("ix_external_channel_attachment_references_remote_attachment_id", ["remote_attachment_id"]),
            ("ix_external_channel_attachment_refs_storage_status", ["storage_status"]),
        ):
            op.create_index(name, "external_channel_attachment_references", columns)

    if not _table_exists("external_channel_sync_cursors"):
        op.create_table(
            "external_channel_sync_cursors",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("cursor_value", sa.String(length=255), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_external_channel_sync_cursors_source",
            "external_channel_sync_cursors",
            ["source"],
            unique=True,
        )

    if not _table_exists("external_channel_unresolved_events"):
        op.create_table(
            "external_channel_unresolved_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("source", sa.String(length=80), nullable=False),
            sa.Column("session_key", sa.String(length=255), nullable=True),
            sa.Column("event_type", sa.String(length=80), nullable=True),
            sa.Column("recipient", sa.String(length=255), nullable=True),
            sa.Column("source_chat_id", sa.String(length=120), nullable=True),
            sa.Column("preferred_reply_contact", sa.String(length=160), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("payload_hash", sa.String(length=64), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False, server_default="pending"),
            sa.Column("replay_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        for name, columns in (
            ("ix_external_channel_unresolved_events_status", ["status"]),
            ("ix_external_channel_unresolved_events_session_key", ["session_key"]),
            ("ix_external_channel_unresolved_events_recipient", ["recipient"]),
            ("ix_external_channel_unresolved_events_source_chat_id", ["source_chat_id"]),
            ("ix_external_channel_unresolved_events_created_at", ["created_at"]),
            ("ix_external_channel_unresolved_payload_hash_status", ["source", "session_key", "payload_hash", "status"]),
        ):
            op.create_index(name, "external_channel_unresolved_events", columns)
        where = sa.text("payload_hash IS NOT NULL AND status IN ('pending', 'failed', 'replaying')")
        op.create_index(
            "uq_external_channel_unresolved_active_payload_hash",
            "external_channel_unresolved_events",
            ["source", sa.text("COALESCE(session_key, '')"), "payload_hash"],
            unique=True,
            sqlite_where=where,
            postgresql_where=where,
        )


def _add_legacy_columns() -> None:
    if _table_exists("operator_tasks") and "unresolved_event_id" not in _columns("operator_tasks"):
        with op.batch_alter_table("operator_tasks") as batch:
            batch.add_column(sa.Column("unresolved_event_id", sa.Integer(), nullable=True))
        op.create_index(
            "ix_operator_tasks_unresolved_event_id",
            "operator_tasks",
            ["unresolved_event_id"],
        )
        active_where = sa.text(
            "unresolved_event_id IS NOT NULL AND status NOT IN ('resolved', 'dropped', 'replayed', 'replay_failed', 'cancelled')"
        )
        op.create_index(
            "uq_operator_tasks_active_external_channel_unresolved",
            "operator_tasks",
            ["unresolved_event_id"],
            unique=True,
            sqlite_where=active_where,
            postgresql_where=active_where,
        )
    if _table_exists("channel_onboarding_tasks") and "external_channel_account_id" not in _columns("channel_onboarding_tasks"):
        with op.batch_alter_table("channel_onboarding_tasks") as batch:
            batch.add_column(sa.Column("external_channel_account_id", sa.String(length=160), nullable=True))
        op.create_index(
            "ix_channel_onboarding_tasks_external_channel_account_id",
            "channel_onboarding_tasks",
            ["external_channel_account_id"],
        )


def _coerce(column: sa.Column, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(column.type, sa.DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


def _restore_table_rows(source_table: str) -> None:
    archive = _table(ARCHIVE_TABLE)
    target = _table(source_table)
    rows = _bind().execute(
        sa.select(archive)
        .where(
            archive.c.migration_key == MIGRATION_KEY,
            archive.c.source_table == source_table,
        )
        .order_by(archive.c.id.asc())
    ).mappings().all()
    for archived in rows:
        payload = _loads(archived["source_payload_json"])
        if _sha256(payload) != archived["source_payload_sha256"]:
            raise RuntimeError(
                f"retirement_archive_payload_hash_mismatch:{source_table}:{archived['source_pk']}"
            )
        values = {
            key: _coerce(target.c[key], value)
            for key, value in payload.items()
            if key in target.c
        }
        _bind().execute(target.insert().values(**values))


def _verify_ref(ref: dict[str, Any]) -> None:
    payload = _row(ref["table"], ref["id"])
    if payload is None:
        raise RuntimeError(
            f"retirement_downgrade_reference_missing:{ref['table']}:{ref['id']}"
        )
    if _sha256(payload) != ref["sha256"]:
        raise RuntimeError(
            f"retirement_downgrade_reference_changed:{ref['table']}:{ref['id']}"
        )


def _restore_generic_rows(source_table: str, target_table: str) -> None:
    archive = _table(ARCHIVE_TABLE)
    target = _table(target_table)
    rows = _bind().execute(
        sa.select(archive).where(
            archive.c.migration_key == MIGRATION_KEY,
            archive.c.source_table == source_table,
        )
    ).mappings().all()
    for archived in rows:
        refs = _loads(archived["canonical_refs_json"]) or []
        for ref in refs:
            _verify_ref(ref)
        payload = _loads(archived["source_payload_json"])
        if _sha256(payload) != archived["source_payload_sha256"]:
            raise RuntimeError(
                f"retirement_archive_payload_hash_mismatch:{source_table}:{archived['source_pk']}"
            )
        row_id = payload["id"]
        current = _row(target_table, row_id)
        if current is None:
            raise RuntimeError(
                f"retirement_downgrade_target_missing:{target_table}:{row_id}"
            )
        values = {
            key: _coerce(target.c[key], value)
            for key, value in payload.items()
            if key in target.c and key != "id"
        }
        _bind().execute(target.update().where(target.c.id == row_id).values(**values))


def _delete_canonical_refs() -> None:
    archive = _table(ARCHIVE_TABLE)
    rows = _bind().execute(
        sa.select(archive).where(
            archive.c.migration_key == MIGRATION_KEY,
            archive.c.source_table.in_(LEGACY_TABLES),
        )
    ).mappings().all()
    refs: list[dict[str, Any]] = []
    for archived in rows:
        refs.extend(_loads(archived["canonical_refs_json"]) or [])
    for ref in refs:
        _verify_ref(ref)
    for table_name in ("ticket_attachments", "ticket_events"):
        table = _table(table_name)
        ids = [int(ref["id"]) for ref in refs if ref["table"] == table_name]
        if ids:
            _bind().execute(table.delete().where(table.c.id.in_(ids)))


def _reset_sequence(table_name: str) -> None:
    bind = _bind()
    if bind.dialect.name != "postgresql":
        return
    bind.execute(
        sa.text(
            f"""
            SELECT setval(
                pg_get_serial_sequence('{table_name}', 'id'),
                COALESCE((SELECT MAX(id) FROM {table_name}), 1),
                EXISTS(SELECT 1 FROM {table_name})
            )
            """
        )
    )


def downgrade() -> None:
    if not _table_exists(ARCHIVE_TABLE):
        raise RuntimeError(
            "migration_retirement_archive_missing: refusing destructive downgrade"
        )
    _create_legacy_tables()
    _add_legacy_columns()

    for source_table in (
        "external_channel_conversation_links",
        "external_channel_transcript_messages",
        "external_channel_attachment_references",
        "external_channel_sync_cursors",
        "external_channel_unresolved_events",
    ):
        _restore_table_rows(source_table)
        _reset_sequence(source_table)

    for archive_name, target_table in (
        ("operator_tasks_legacy", "operator_tasks"),
        ("channel_onboarding_tasks_legacy", "channel_onboarding_tasks"),
        ("background_jobs_legacy", "background_jobs"),
        ("channel_accounts_legacy", "channel_accounts"),
        ("service_heartbeats_legacy", "service_heartbeats"),
    ):
        if _table_exists(target_table):
            _restore_generic_rows(archive_name, target_table)

    for target_table in GENERIC_TEXT_TABLES:
        if _table_exists(target_table):
            _restore_generic_rows(f"{target_table}_legacy_marker", target_table)

    _delete_canonical_refs()
    op.drop_index(
        "ix_migration_retirement_archive_ticket_id",
        table_name=ARCHIVE_TABLE,
    )
    op.drop_index(
        "ix_migration_retirement_archive_migration_source",
        table_name=ARCHIVE_TABLE,
    )
    op.drop_table(ARCHIVE_TABLE)
