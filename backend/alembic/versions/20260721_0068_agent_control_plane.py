"""add canonical Agent control plane and governed customer memory

Revision ID: 20260721_0068
Revises: 20260720_0067
Create Date: 2026-07-21
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260721_0068"
down_revision = "20260720_0067"
branch_labels = None
depends_on = None


def _playbook(name: str, description: str, tools: list[str], instructions: list[str], priority: int) -> dict[str, Any]:
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
            ["speedaf.order.query", "speedaf.express.track.query", "speedaf.order.waybillCode.query"],
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
        "agent.playbook.customer-continuity",
        "playbook",
        "Customer continuity",
        _playbook(
            "customer_continuity",
            "Use governed long-term customer facts.",
            ["customer.memory.read", "customer.memory.write"],
            [
                "Use customer memory only when it materially improves the current task and never as authority for current external facts.",
                "Write only explicitly confirmed permitted facts; never store credentials, payment data, raw transcripts, health data or government identifiers.",
            ],
            60,
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
    (
        "agent.memory.default",
        "memory_policy",
        "Default customer memory policy",
        {
            "schema_version": "nexus.customer_memory_policy.v1",
            "injection_enabled": True,
            "write_enabled": False,
            "require_explicit_consent": True,
            "max_facts": 12,
            "retention_days": 180,
            "allowed_keys": [
                "preferred_language",
                "preferred_contact_channel",
                "delivery_instructions",
                "accessibility_preference",
                "communication_preference",
            ],
            "prohibited_categories": [
                "credential",
                "payment_card",
                "government_identifier",
                "health",
                "biometric",
                "raw_transcript",
            ],
            "enabled": True,
        },
    ),
)

_DEFAULT_TOOL_POLICIES = (
    ("integration.read", "medium", False, False),
    ("integration.write", "high", True, False),
    ("customer.memory.read", "low", False, False),
    ("customer.memory.write", "high", True, False),
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


def upgrade() -> None:
    op.create_table(
        "customer_memory_facts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("customer_id", sa.Integer(), sa.ForeignKey("customers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("memory_key", sa.String(length=120), nullable=False),
        sa.Column("value_text", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=False, server_default="operator"),
        sa.Column("source_reference", sa.String(length=200), nullable=True),
        sa.Column("consent_basis", sa.String(length=80), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("sensitivity", sa.String(length=20), nullable=False, server_default="standard"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("tenant_key", "customer_id", "memory_key", name="uq_customer_memory_fact_scope_key"),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_customer_memory_confidence_range"),
        sa.CheckConstraint("sensitivity IN ('standard', 'restricted')", name="ck_customer_memory_sensitivity"),
    )
    op.create_index("ix_customer_memory_facts_tenant_key", "customer_memory_facts", ["tenant_key"])
    op.create_index("ix_customer_memory_facts_customer_id", "customer_memory_facts", ["customer_id"])
    op.create_index("ix_customer_memory_facts_memory_key", "customer_memory_facts", ["memory_key"])
    op.create_index("ix_customer_memory_facts_is_active", "customer_memory_facts", ["is_active"])
    op.create_index("ix_customer_memory_facts_expires_at", "customer_memory_facts", ["expires_at"])
    op.create_index(
        "ix_customer_memory_runtime_lookup",
        "customer_memory_facts",
        ["tenant_key", "customer_id", "is_active", "expires_at"],
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
        sa.column("id", sa.Integer()),
        sa.column("resource_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("snapshot_json", sa.JSON()),
        sa.column("summary", sa.Text()),
        sa.column("notes", sa.Text()),
        sa.column("published_by", sa.Integer()),
        sa.column("published_at", sa.DateTime(timezone=True)),
    )

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

    op.drop_index("ix_customer_memory_runtime_lookup", table_name="customer_memory_facts")
    op.drop_index("ix_customer_memory_facts_expires_at", table_name="customer_memory_facts")
    op.drop_index("ix_customer_memory_facts_is_active", table_name="customer_memory_facts")
    op.drop_index("ix_customer_memory_facts_memory_key", table_name="customer_memory_facts")
    op.drop_index("ix_customer_memory_facts_customer_id", table_name="customer_memory_facts")
    op.drop_index("ix_customer_memory_facts_tenant_key", table_name="customer_memory_facts")
    op.drop_table("customer_memory_facts")
