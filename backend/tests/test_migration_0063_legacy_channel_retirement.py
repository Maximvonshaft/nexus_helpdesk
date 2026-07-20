from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend/alembic/versions/20260720_0063_retire_legacy_channel_persistence.py"


def _load(connection: sa.Connection):
    spec = importlib.util.spec_from_file_location("migration_0063_test", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.op = Operations(MigrationContext.configure(connection))
    return module


def _schema(metadata: sa.MetaData) -> None:
    sa.Table("tickets", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table("markets", metadata, sa.Column("id", sa.Integer, primary_key=True))
    sa.Table(
        "channel_accounts",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("account_id", sa.String(160), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("health_status", sa.String(40), nullable=False),
        sa.Column("fallback_account_id", sa.String(160)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "ticket_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, nullable=False),
        sa.Column("actor_id", sa.Integer),
        sa.Column("event_type", sa.String(120), nullable=False),
        sa.Column("field_name", sa.String(120)),
        sa.Column("old_value", sa.Text),
        sa.Column("new_value", sa.Text),
        sa.Column("note", sa.Text),
        sa.Column("payload_json", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "ticket_attachments",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, nullable=False),
        sa.Column("uploaded_by", sa.Integer),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(255)),
        sa.Column("file_path", sa.String(500)),
        sa.Column("file_url", sa.String(500)),
        sa.Column("mime_type", sa.String(120)),
        sa.Column("file_size", sa.Integer),
        sa.Column("visibility", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "background_jobs",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("queue_name", sa.String(80), nullable=False),
        sa.Column("job_type", sa.String(120), nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("dedupe_key", sa.String(255)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True)),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(120)),
        sa.Column("last_error", sa.Text),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "service_heartbeats",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("service_name", sa.String(80), nullable=False),
        sa.Column("instance_id", sa.String(120)),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("details_json", sa.JSON),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "operator_tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_type", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(160)),
        sa.Column("ticket_id", sa.Integer),
        sa.Column("webchat_conversation_id", sa.Integer),
        sa.Column("unresolved_event_id", sa.Integer),
        sa.Column("task_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("assignee_id", sa.Integer),
        sa.Column("reason_code", sa.String(160)),
        sa.Column("payload_json", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
    )
    sa.Index("ix_operator_tasks_unresolved_event_id", metadata.tables["operator_tasks"].c.unresolved_event_id)
    sa.Index(
        "uq_operator_tasks_active_external_channel_unresolved",
        metadata.tables["operator_tasks"].c.unresolved_event_id,
        unique=True,
        sqlite_where=sa.text("unresolved_event_id IS NOT NULL AND status NOT IN ('resolved', 'dropped', 'replayed', 'replay_failed', 'cancelled')"),
    )
    sa.Table(
        "channel_onboarding_tasks",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("requested_by", sa.Integer),
        sa.Column("market_id", sa.Integer),
        sa.Column("target_slot", sa.String(120)),
        sa.Column("desired_display_name", sa.String(160)),
        sa.Column("desired_channel_account_binding", sa.String(160)),
        sa.Column("external_channel_account_id", sa.String(160)),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    sa.Index(
        "ix_channel_onboarding_tasks_external_channel_account_id",
        metadata.tables["channel_onboarding_tasks"].c.external_channel_account_id,
    )
    sa.Table(
        "external_channel_conversation_links",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, nullable=False),
        sa.Column("session_key", sa.String(255), nullable=False),
        sa.Column("channel", sa.String(60)),
        sa.Column("recipient", sa.String(255)),
        sa.Column("account_id", sa.String(120)),
        sa.Column("thread_id", sa.String(120)),
        sa.Column("channel_account_id", sa.Integer),
        sa.Column("route_json", sa.JSON),
        sa.Column("last_cursor", sa.Integer),
        sa.Column("last_message_id", sa.String(255)),
        sa.Column("last_synced_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "external_channel_transcript_messages",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("conversation_id", sa.Integer, nullable=False),
        sa.Column("ticket_id", sa.Integer, nullable=False),
        sa.Column("session_key", sa.String(255), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32)),
        sa.Column("author_name", sa.String(160)),
        sa.Column("body_text", sa.Text),
        sa.Column("content_json", sa.JSON),
        sa.Column("received_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "external_channel_attachment_references",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ticket_id", sa.Integer, nullable=False),
        sa.Column("conversation_id", sa.Integer, nullable=False),
        sa.Column("transcript_message_id", sa.Integer, nullable=False),
        sa.Column("remote_attachment_id", sa.String(160), nullable=False),
        sa.Column("content_type", sa.String(120)),
        sa.Column("filename", sa.String(255)),
        sa.Column("metadata_json", sa.JSON),
        sa.Column("storage_status", sa.String(40), nullable=False),
        sa.Column("storage_key", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "external_channel_sync_cursors",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("cursor_value", sa.String(255)),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "external_channel_unresolved_events",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source", sa.String(80), nullable=False),
        sa.Column("session_key", sa.String(255)),
        sa.Column("event_type", sa.String(80)),
        sa.Column("recipient", sa.String(255)),
        sa.Column("source_chat_id", sa.String(120)),
        sa.Column("preferred_reply_contact", sa.String(160)),
        sa.Column("payload_json", sa.Text, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("replay_count", sa.Integer, nullable=False),
        sa.Column("last_error", sa.Text),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def _seed(connection: sa.Connection) -> None:
    now = datetime(2026, 7, 20, tzinfo=timezone.utc)
    tables = sa.MetaData()
    tables.reflect(connection)
    connection.execute(tables.tables["tickets"].insert(), [{"id": 1}])
    connection.execute(
        tables.tables["channel_accounts"].insert(),
        [{
            "id": 1,
            "provider": "external_channel",
            "account_id": "legacy-account",
            "is_active": True,
            "health_status": "healthy",
            "fallback_account_id": None,
            "updated_at": now,
        }],
    )
    connection.execute(
        tables.tables["external_channel_conversation_links"].insert(),
        [{
            "id": 10,
            "ticket_id": 1,
            "session_key": "session-1",
            "channel": "whatsapp",
            "recipient": "+41000000000",
            "account_id": "legacy-account",
            "thread_id": "thread-1",
            "channel_account_id": 1,
            "route_json": {"provider": "external_channel"},
            "last_cursor": 2,
            "last_message_id": "message-1",
            "last_synced_at": now,
            "created_at": now,
            "updated_at": now,
        }],
    )
    connection.execute(
        tables.tables["external_channel_transcript_messages"].insert(),
        [{
            "id": 20,
            "conversation_id": 10,
            "ticket_id": 1,
            "session_key": "session-1",
            "message_id": "message-1",
            "role": "customer",
            "author_name": "Customer",
            "body_text": "Where is my parcel?",
            "content_json": {"type": "text"},
            "received_at": now,
            "created_at": now,
        }],
    )
    connection.execute(
        tables.tables["external_channel_attachment_references"].insert(),
        [{
            "id": 30,
            "ticket_id": 1,
            "conversation_id": 10,
            "transcript_message_id": 20,
            "remote_attachment_id": "remote-1",
            "content_type": "image/png",
            "filename": "proof.png",
            "metadata_json": {"width": 100},
            "storage_status": "persisted",
            "storage_key": "attachments/proof.png",
            "created_at": now,
            "updated_at": now,
        }],
    )
    connection.execute(
        tables.tables["external_channel_sync_cursors"].insert(),
        [{"id": 40, "source": "external_channel", "cursor_value": "2", "updated_at": now}],
    )
    connection.execute(
        tables.tables["external_channel_unresolved_events"].insert(),
        [{
            "id": 50,
            "source": "external_channel",
            "session_key": "session-1",
            "event_type": "message",
            "recipient": "+41000000000",
            "source_chat_id": "chat-1",
            "preferred_reply_contact": "+41000000000",
            "payload_json": json.dumps({"message": "unresolved"}),
            "payload_hash": "a" * 64,
            "status": "pending",
            "replay_count": 0,
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }],
    )
    connection.execute(
        tables.tables["operator_tasks"].insert(),
        [{
            "id": 60,
            "source_type": "external_channel",
            "source_id": "50",
            "ticket_id": 1,
            "webchat_conversation_id": None,
            "unresolved_event_id": 50,
            "task_type": "unresolved_event",
            "status": "pending",
            "priority": 10,
            "assignee_id": None,
            "reason_code": "external_channel_failure",
            "payload_json": json.dumps({"source": "external_channel"}),
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }],
    )
    connection.execute(
        tables.tables["channel_onboarding_tasks"].insert(),
        [{
            "id": 70,
            "provider": "external_channel",
            "status": "pending",
            "requested_by": None,
            "market_id": None,
            "target_slot": "primary",
            "desired_display_name": "Legacy",
            "desired_channel_account_binding": None,
            "external_channel_account_id": "legacy-account",
            "last_error": None,
            "created_at": now,
            "updated_at": now,
            "started_at": None,
            "completed_at": None,
        }],
    )
    connection.execute(
        tables.tables["background_jobs"].insert(),
        [{
            "id": 80,
            "queue_name": "external_channel",
            "job_type": "external_channel.sync",
            "payload_json": json.dumps({"provider": "external_channel"}),
            "dedupe_key": "legacy-job",
            "status": "pending",
            "next_run_at": now,
            "locked_at": now,
            "locked_by": "worker",
            "last_error": None,
            "updated_at": now,
        }],
    )
    connection.execute(
        tables.tables["service_heartbeats"].insert(),
        [{
            "id": 90,
            "service_name": "external_channel-sync",
            "instance_id": "instance-1",
            "status": "healthy",
            "details_json": {"provider": "external_channel"},
            "last_seen_at": now,
            "updated_at": now,
        }],
    )


def _fixture_engine() -> sa.Engine:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        metadata = sa.MetaData()
        _schema(metadata)
        metadata.create_all(connection)
        _seed(connection)
    return engine


def test_upgrade_projects_history_neutralizes_execution_and_drops_legacy_schema() -> None:
    engine = _fixture_engine()
    with engine.begin() as connection:
        migration = _load(connection)
        migration.upgrade()
        inspector = sa.inspect(connection)
        tables = set(inspector.get_table_names())
        assert not {
            "external_channel_conversation_links",
            "external_channel_transcript_messages",
            "external_channel_attachment_references",
            "external_channel_sync_cursors",
            "external_channel_unresolved_events",
        } & tables
        assert "migration_retirement_archive" in tables
        assert "unresolved_event_id" not in {column["name"] for column in inspector.get_columns("operator_tasks")}
        assert "external_channel_account_id" not in {column["name"] for column in inspector.get_columns("channel_onboarding_tasks")}

        metadata = sa.MetaData()
        metadata.reflect(connection)
        events = connection.execute(sa.select(metadata.tables["ticket_events"])).mappings().all()
        assert {row["field_name"] for row in events} >= {
            "retired_channel_context",
            "retired_channel_message",
            "retired_channel_attachment",
        }
        attachments = connection.execute(sa.select(metadata.tables["ticket_attachments"])).mappings().all()
        assert len(attachments) == 1
        assert attachments[0]["storage_key"] == "attachments/proof.png"

        task = connection.execute(sa.select(metadata.tables["operator_tasks"])).mappings().one()
        assert task["source_type"] == "retired_source"
        assert task["status"] == "dropped"
        job = connection.execute(sa.select(metadata.tables["background_jobs"])).mappings().one()
        assert job["status"] == "dead"
        assert job["queue_name"] == "retired"
        account = connection.execute(sa.select(metadata.tables["channel_accounts"])).mappings().one()
        assert account["provider"] == "retired"
        assert account["is_active"] is False


def test_downgrade_restores_exact_legacy_rows_and_removes_migration_owned_projections() -> None:
    engine = _fixture_engine()
    with engine.begin() as connection:
        migration = _load(connection)
        migration.upgrade()
        migration.downgrade()
        inspector = sa.inspect(connection)
        tables = set(inspector.get_table_names())
        assert "migration_retirement_archive" not in tables
        assert {
            "external_channel_conversation_links",
            "external_channel_transcript_messages",
            "external_channel_attachment_references",
            "external_channel_sync_cursors",
            "external_channel_unresolved_events",
        } <= tables
        metadata = sa.MetaData()
        metadata.reflect(connection)
        transcript = connection.execute(sa.select(metadata.tables["external_channel_transcript_messages"])).mappings().one()
        assert transcript["body_text"] == "Where is my parcel?"
        task = connection.execute(sa.select(metadata.tables["operator_tasks"])).mappings().one()
        assert task["source_type"] == "external_channel"
        assert task["unresolved_event_id"] == 50
        assert connection.execute(sa.select(sa.func.count()).select_from(metadata.tables["ticket_events"])).scalar_one() == 0
        assert connection.execute(sa.select(sa.func.count()).select_from(metadata.tables["ticket_attachments"])).scalar_one() == 0


def test_downgrade_fails_closed_when_a_migration_owned_projection_changed() -> None:
    engine = _fixture_engine()
    with engine.begin() as connection:
        migration = _load(connection)
        migration.upgrade()
        events = sa.Table("ticket_events", sa.MetaData(), autoload_with=connection)
        event_id = connection.execute(sa.select(events.c.id).order_by(events.c.id.asc())).scalars().first()
        connection.execute(events.update().where(events.c.id == event_id).values(note="operator changed this evidence"))
        with pytest.raises(RuntimeError, match="retirement_downgrade_reference_changed"):
            migration.downgrade()
