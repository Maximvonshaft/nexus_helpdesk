"""add canonical Agent definition, release, deployment and run evidence

Revision ID: 20260721_0068
Revises: 20260720_0067
Create Date: 2026-07-21
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260721_0068"
down_revision = "20260720_0067"
branch_labels = None
depends_on = None


def _playbook(
    name: str,
    description: str,
    tools: list[str],
    instructions: list[str],
    priority: int,
) -> dict[str, Any]:
    return {
        "schema_version": "nexus.agent_playbook.v1",
        "name": name,
        "display_name": name.replace("_", " ").title(),
        "description": description,
        "tools": tools,
        "instructions": instructions,
        "priority": priority,
        "channels": [],
        "languages": [],
        "enabled": True,
    }


_DEFAULT_RESOURCES: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    (
        "agent.playbook.shipment-tracking",
        "playbook",
        "Shipment tracking",
        _playbook(
            "shipment_tracking",
            "Query current shipment facts and event history.",
            [
                "speedaf.order.query",
                "speedaf.express.track.query",
                "speedaf.order.waybillCode.query",
            ],
            [
                "Query a shipment Tool before stating current status, ETA, outcome, customs state or route progress.",
                "Use Tool observations as the source of truth; never invent missing facts or expose raw payloads or PII.",
            ],
            10,
        ),
    ),
    (
        "agent.playbook.approved-knowledge",
        "playbook",
        "Approved knowledge",
        _playbook(
            "approved_knowledge",
            "Answer from approved customer-visible knowledge.",
            ["knowledge.search"],
            [
                "Search approved knowledge for company-specific, market-specific or current internal facts.",
                "Answer only from returned customer-visible evidence and state when no approved answer exists.",
            ],
            20,
        ),
    ),
    (
        "agent.playbook.human-handoff",
        "playbook",
        "Human handoff",
        _playbook(
            "human_handoff",
            "Request governed human support.",
            ["support.availability", "handoff.request.create"],
            [
                "Request handoff when explicitly requested or when legal, privacy, compensation or authority boundaries require it.",
                "Never claim a person accepted the case unless a committed Tool observation states it.",
            ],
            30,
        ),
    ),
    (
        "agent.playbook.delivery-followup",
        "playbook",
        "Delivery follow-up",
        _playbook(
            "delivery_followup",
            "Create a governed delivery follow-up work order.",
            ["speedaf.workOrder.create"],
            [
                "Create a work order only for an explicit follow-up or expedition request with required data.",
                "Never claim success until a committed Tool observation confirms it.",
            ],
            40,
        ),
    ),
    (
        "agent.playbook.case-operations",
        "playbook",
        "Case operations",
        _playbook(
            "case_operations",
            "Perform governed case and confirmed customer operations.",
            [
                "ticket.create",
                "timeline.event.create",
                "speedaf.order.cancel.request",
                "speedaf.order.updateAddress.request",
                "speedaf.voice.callback",
            ],
            [
                "Use write Tools only for explicit customer requests and respect every confirmation requirement.",
                "Never state that a write action succeeded until a committed Tool observation confirms it.",
            ],
            50,
        ),
    ),
    (
        "agent.runtime.default",
        "runtime_policy",
        "Default Agent runtime policy",
        {
            "schema_version": "nexus.agent_runtime_policy.v1",
            "max_tool_rounds": 3,
            "allow_high_risk_writes": False,
            "allowed_tools": [],
            "provider_timeout_ms": 15000,
            "enabled": True,
        },
    ),
    (
        "agent.model.private-default",
        "model_profile",
        "Private Agent model",
        {
            "schema_version": "nexus.agent_model_profile.v1",
            "provider": "private_ai_runtime",
            "endpoint_url": None,
            "credential_ref": None,
            "request_path": "/api/chat",
            "request_shape": "ollama_chat",
            "model": "qwen2.5:3b",
            "temperature": 0.1,
            "top_p": 0.85,
            "max_prompt_chars": 12000,
            "max_output_chars": 4000,
            "num_predict": 512,
            "num_ctx": 8192,
            "keep_alive": "24h",
            "timeout_seconds": 12,
            "enabled": True,
        },
    ),
)

_DEFAULT_TOOL_POLICIES = (
    ("integration.read", "medium", False, False),
    ("integration.write", "high", True, False),
)

_LEGACY_CONFIG_TYPES = (
    "persona",
    "knowledge",
    "sop",
    "policy",
    "rule",
    "rules",
    "status_dictionary",
    "channel_policy",
    "support_runtime",
)


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _release_manifest() -> dict[str, Any]:
    return {
        "schema_version": "nexus.agent_release.v1",
        "persona": None,
        "playbooks": [
            {"resource_key": key, "version": 1}
            for key, config_type, _name, _content in _DEFAULT_RESOURCES
            if config_type == "playbook"
        ],
        "integrations": [],
        "model_profile": {"resource_key": "agent.model.private-default", "version": 1},
        "runtime_policy": {"resource_key": "agent.runtime.default", "version": 1},
        "knowledge": [],
        "metadata": {"origin": "migration_0068"},
    }


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("definition_key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("purpose", sa.Text(), nullable=True),
        sa.Column("owner_team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("draft_manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_key", "definition_key", name="uq_agent_definition_tenant_key"),
        sa.CheckConstraint("length(trim(tenant_key)) > 0", name="ck_agent_definition_tenant_nonempty"),
        sa.CheckConstraint("length(trim(definition_key)) > 0", name="ck_agent_definition_key_nonempty"),
    )
    op.create_index("ix_agent_definitions_tenant_key", "agent_definitions", ["tenant_key"])
    op.create_index("ix_agent_definitions_definition_key", "agent_definitions", ["definition_key"])
    op.create_index("ix_agent_definitions_name", "agent_definitions", ["name"])
    op.create_index("ix_agent_definitions_owner_team_id", "agent_definitions", ["owner_team_id"])
    op.create_index("ix_agent_definitions_is_active", "agent_definitions", ["is_active"])
    op.create_index("ix_agent_definitions_created_by", "agent_definitions", ["created_by"])
    op.create_index("ix_agent_definitions_updated_by", "agent_definitions", ["updated_by"])
    op.create_index("ix_agent_definitions_created_at", "agent_definitions", ["created_at"])
    op.create_index("ix_agent_definitions_updated_at", "agent_definitions", ["updated_at"])
    op.create_index(
        "ix_agent_definitions_tenant_active", "agent_definitions", ["tenant_key", "is_active"]
    )

    op.create_table(
        "agent_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "definition_id",
            sa.Integer(),
            sa.ForeignKey("agent_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="approved"),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("approved_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("definition_id", "version", name="uq_agent_release_definition_version"),
        sa.CheckConstraint(
            "status IN ('approved', 'canary', 'active', 'retired')",
            name="ck_agent_release_status",
        ),
    )
    op.create_index("ix_agent_releases_definition_id", "agent_releases", ["definition_id"])
    op.create_index("ix_agent_releases_version", "agent_releases", ["version"])
    op.create_index("ix_agent_releases_status", "agent_releases", ["status"])
    op.create_index("ix_agent_releases_manifest_sha256", "agent_releases", ["manifest_sha256"])
    op.create_index("ix_agent_releases_created_by", "agent_releases", ["created_by"])
    op.create_index("ix_agent_releases_approved_by", "agent_releases", ["approved_by"])
    op.create_index("ix_agent_releases_created_at", "agent_releases", ["created_at"])
    op.create_index("ix_agent_releases_approved_at", "agent_releases", ["approved_at"])
    op.create_index(
        "ix_agent_releases_definition_status", "agent_releases", ["definition_id", "status"]
    )

    op.create_table(
        "agent_deployments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("environment", sa.String(length=24), nullable=False, server_default="production"),
        sa.Column("scope_key", sa.String(length=320), nullable=False),
        sa.Column("market_id", sa.Integer(), sa.ForeignKey("markets.id"), nullable=True),
        sa.Column("channel", sa.String(length=40), nullable=True),
        sa.Column("language", sa.String(length=24), nullable=True),
        sa.Column("case_type", sa.String(length=80), nullable=True),
        sa.Column("active_release_id", sa.Integer(), sa.ForeignKey("agent_releases.id"), nullable=False),
        sa.Column("canary_release_id", sa.Integer(), sa.ForeignKey("agent_releases.id"), nullable=True),
        sa.Column("canary_percent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("activated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_key", "environment", "scope_key", name="uq_agent_deployment_scope"),
        sa.CheckConstraint("canary_percent >= 0 AND canary_percent <= 100", name="ck_agent_canary_percent"),
        sa.CheckConstraint(
            "environment IN ('test', 'staging', 'production')", name="ck_agent_environment"
        ),
    )
    for column in (
        "tenant_key",
        "environment",
        "market_id",
        "channel",
        "language",
        "case_type",
        "active_release_id",
        "canary_release_id",
        "is_active",
        "activated_by",
        "activated_at",
        "updated_at",
    ):
        op.create_index(f"ix_agent_deployments_{column}", "agent_deployments", [column])
    op.create_index(
        "ix_agent_deployments_lookup",
        "agent_deployments",
        ["tenant_key", "environment", "is_active"],
    )

    op.create_table(
        "agent_run_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("session_id", sa.String(length=160), nullable=False),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("deployment_id", sa.Integer(), sa.ForeignKey("agent_deployments.id"), nullable=True),
        sa.Column("release_id", sa.Integer(), sa.ForeignKey("agent_releases.id"), nullable=True),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_json", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False, server_default="deployment"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("request_id", name="uq_agent_run_snapshot_request"),
    )
    for column in (
        "request_id",
        "session_id",
        "tenant_key",
        "deployment_id",
        "release_id",
        "snapshot_sha256",
        "created_at",
    ):
        op.create_index(f"ix_agent_run_snapshots_{column}", "agent_run_snapshots", [column])
    op.create_index(
        "ix_agent_run_snapshots_tenant_session",
        "agent_run_snapshots",
        ["tenant_key", "session_id", "created_at"],
    )

    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    resources = sa.table(
        "ai_config_resources",
        sa.column("id", sa.Integer()),
        sa.column("resource_key", sa.String()),
        sa.column("config_type", sa.String()),
        sa.column("name", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("scope_type", sa.String()),
        sa.column("scope_value", sa.String()),
        sa.column("market_id", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("draft_summary", sa.Text()),
        sa.column("draft_content_json", sa.JSON()),
        sa.column("published_summary", sa.Text()),
        sa.column("published_content_json", sa.JSON()),
        sa.column("published_version", sa.Integer()),
        sa.column("published_at", sa.DateTime(timezone=True)),
        sa.column("created_by", sa.Integer()),
        sa.column("updated_by", sa.Integer()),
        sa.column("published_by", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    versions = sa.table(
        "ai_config_versions",
        sa.column("resource_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("snapshot_json", sa.JSON()),
        sa.column("summary", sa.Text()),
        sa.column("notes", sa.Text()),
        sa.column("published_by", sa.Integer()),
        sa.column("published_at", sa.DateTime(timezone=True)),
    )

    resource_evidence: list[dict[str, Any]] = []
    for resource_key, config_type, name, content in _DEFAULT_RESOURCES:
        existing = bind.execute(
            sa.select(resources.c.id).where(resources.c.resource_key == resource_key)
        ).scalar_one_or_none()
        if existing is not None:
            raise RuntimeError(f"migration_0068_resource_conflict:{resource_key}")
        summary = str(content.get("description") or name)
        bind.execute(
            sa.insert(resources).values(
                resource_key=resource_key,
                config_type=config_type,
                name=name,
                description=summary,
                scope_type="global",
                scope_value=None,
                market_id=None,
                is_active=True,
                draft_summary=summary,
                draft_content_json=content,
                published_summary=summary,
                published_content_json=content,
                published_version=1,
                published_at=now,
                created_by=None,
                updated_by=None,
                published_by=None,
                created_at=now,
                updated_at=now,
            )
        )
        resource_id = bind.execute(
            sa.select(resources.c.id).where(resources.c.resource_key == resource_key)
        ).scalar_one()
        bind.execute(
            sa.insert(versions).values(
                resource_id=resource_id,
                version=1,
                snapshot_json=content,
                summary=summary,
                notes="Seed canonical Agent control plane",
                published_by=None,
                published_at=now,
            )
        )
        resource_evidence.append(
            {
                "id": resource_id,
                "resource_key": resource_key,
                "config_type": config_type,
                "version": 1,
                "content": content,
            }
        )

    bind.execute(
        sa.update(resources)
        .where(resources.c.config_type.in_(_LEGACY_CONFIG_TYPES))
        .values(is_active=False, updated_at=now)
    )

    for tool_name, risk_level, customer_confirmation, human_confirmation in _DEFAULT_TOOL_POLICIES:
        existing = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM tool_execution_policies "
                "WHERE tool_name = :tool_name AND country_code = 'GLOBAL' AND channel = 'all'"
            ),
            {"tool_name": tool_name},
        ).scalar_one()
        if int(existing or 0):
            raise RuntimeError(f"migration_0068_tool_policy_conflict:{tool_name}")
        bind.execute(
            sa.text(
                """
                INSERT INTO tool_execution_policies
                    (tool_name, country_code, channel, enabled, ai_auto_executable, risk_level,
                     requires_tracking_number, requires_contact, requires_customer_confirmation,
                     requires_human_confirmation, audit_level, created_at, updated_at)
                VALUES (:tool_name, 'GLOBAL', 'all', true, true, :risk_level, false, false,
                        :customer_confirmation, :human_confirmation, 'detailed',
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            ),
            {
                "tool_name": tool_name,
                "risk_level": risk_level,
                "customer_confirmation": customer_confirmation,
                "human_confirmation": human_confirmation,
            },
        )

    manifest = _release_manifest()
    definition_result = bind.execute(
        sa.text(
            """
            INSERT INTO agent_definitions
                (tenant_key, definition_key, name, purpose, owner_team_id, is_active,
                 draft_manifest_json, created_by, updated_by, created_at, updated_at)
            VALUES
                ('default', 'default-support-agent', 'Default support Agent',
                 'Canonical platform fallback for governed customer support.', NULL, true,
                 :manifest, NULL, NULL, :now, :now)
            RETURNING id
            """
        ),
        {"manifest": manifest, "now": now},
    )
    definition_id = definition_result.scalar_one()
    validation = {"resources": resource_evidence, "knowledge": [], "persona": None}
    release_result = bind.execute(
        sa.text(
            """
            INSERT INTO agent_releases
                (definition_id, version, status, manifest_json, manifest_sha256,
                 validation_json, created_by, approved_by, created_at, approved_at)
            VALUES
                (:definition_id, 1, 'active', :manifest, :digest,
                 :validation, NULL, NULL, :now, :now)
            RETURNING id
            """
        ),
        {
            "definition_id": definition_id,
            "manifest": manifest,
            "digest": _digest(manifest),
            "validation": validation,
            "now": now,
        },
    )
    release_id = release_result.scalar_one()
    bind.execute(
        sa.text(
            """
            INSERT INTO agent_deployments
                (tenant_key, environment, scope_key, market_id, channel, language, case_type,
                 active_release_id, canary_release_id, canary_percent, is_active,
                 activated_by, activated_at, updated_at)
            VALUES
                ('default', 'production', '*|*|*|*', NULL, NULL, NULL, NULL,
                 :release_id, NULL, 0, true, NULL, :now, :now)
            """
        ),
        {"release_id": release_id, "now": now},
    )


def downgrade() -> None:
    bind = op.get_bind()
    resource_keys = tuple(item[0] for item in _DEFAULT_RESOURCES)
    resource_ids = [
        row[0]
        for row in bind.execute(
            sa.text(
                "SELECT id FROM ai_config_resources WHERE resource_key IN "
                f"({', '.join(f':key{i}' for i in range(len(resource_keys)))})"
            ),
            {f"key{i}": key for i, key in enumerate(resource_keys)},
        ).all()
    ]

    bind.execute(sa.text("DELETE FROM agent_run_snapshots"))
    bind.execute(sa.text("DELETE FROM agent_deployments WHERE tenant_key = 'default'"))
    bind.execute(
        sa.text(
            "DELETE FROM agent_releases WHERE definition_id IN "
            "(SELECT id FROM agent_definitions WHERE tenant_key = 'default' "
            "AND definition_key = 'default-support-agent')"
        )
    )
    bind.execute(
        sa.text(
            "DELETE FROM agent_definitions WHERE tenant_key = 'default' "
            "AND definition_key = 'default-support-agent'"
        )
    )

    if resource_ids:
        bind.execute(
            sa.text(
                "DELETE FROM ai_config_versions WHERE resource_id IN "
                f"({', '.join(f':id{i}' for i in range(len(resource_ids)))})"
            ),
            {f"id{i}": value for i, value in enumerate(resource_ids)},
        )
        bind.execute(
            sa.text(
                "DELETE FROM ai_config_resources WHERE id IN "
                f"({', '.join(f':id{i}' for i in range(len(resource_ids)))})"
            ),
            {f"id{i}": value for i, value in enumerate(resource_ids)},
        )

    bind.execute(
        sa.text(
            "DELETE FROM tool_execution_policies WHERE country_code = 'GLOBAL' "
            "AND channel = 'all' AND tool_name IN "
            f"({', '.join(f':tool{i}' for i in range(len(_DEFAULT_TOOL_POLICIES)))})"
        ),
        {f"tool{i}": item[0] for i, item in enumerate(_DEFAULT_TOOL_POLICIES)},
    )
    placeholders = ", ".join(f":type{i}" for i in range(len(_LEGACY_CONFIG_TYPES)))
    bind.execute(
        sa.text(
            "UPDATE ai_config_resources SET is_active = true, updated_at = CURRENT_TIMESTAMP "
            f"WHERE config_type IN ({placeholders})"
        ),
        {f"type{i}": value for i, value in enumerate(_LEGACY_CONFIG_TYPES)},
    )

    op.drop_index("ix_agent_run_snapshots_tenant_session", table_name="agent_run_snapshots")
    for column in reversed(
        (
            "request_id",
            "session_id",
            "tenant_key",
            "deployment_id",
            "release_id",
            "snapshot_sha256",
            "created_at",
        )
    ):
        op.drop_index(f"ix_agent_run_snapshots_{column}", table_name="agent_run_snapshots")
    op.drop_table("agent_run_snapshots")

    op.drop_index("ix_agent_deployments_lookup", table_name="agent_deployments")
    for column in reversed(
        (
            "tenant_key",
            "environment",
            "market_id",
            "channel",
            "language",
            "case_type",
            "active_release_id",
            "canary_release_id",
            "is_active",
            "activated_by",
            "activated_at",
            "updated_at",
        )
    ):
        op.drop_index(f"ix_agent_deployments_{column}", table_name="agent_deployments")
    op.drop_table("agent_deployments")

    op.drop_index("ix_agent_releases_definition_status", table_name="agent_releases")
    for column in reversed(
        (
            "definition_id",
            "version",
            "status",
            "manifest_sha256",
            "created_by",
            "approved_by",
            "created_at",
            "approved_at",
        )
    ):
        op.drop_index(f"ix_agent_releases_{column}", table_name="agent_releases")
    op.drop_table("agent_releases")

    op.drop_index("ix_agent_definitions_tenant_active", table_name="agent_definitions")
    for column in reversed(
        (
            "tenant_key",
            "definition_key",
            "name",
            "owner_team_id",
            "is_active",
            "created_by",
            "updated_by",
            "created_at",
            "updated_at",
        )
    ):
        op.drop_index(f"ix_agent_definitions_{column}", table_name="agent_definitions")
    op.drop_table("agent_definitions")
