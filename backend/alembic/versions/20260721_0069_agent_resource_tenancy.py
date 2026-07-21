"""close Agent resource tenancy and release-schema gaps

Revision ID: 20260721_0069
Revises: 20260721_0068
Create Date: 2026-07-21
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from alembic import op

revision = "20260721_0069"
down_revision = "20260721_0068"
branch_labels = None
depends_on = None


def _digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.create_table(
        "agent_resource_bindings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_key", sa.String(length=80), nullable=False),
        sa.Column("resource_type", sa.String(length=24), nullable=False),
        sa.Column("resource_id", sa.Integer(), nullable=False),
        sa.Column("is_global_template", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "resource_type", "resource_id", name="uq_agent_resource_binding_target"
        ),
        sa.CheckConstraint(
            "resource_type IN ('persona', 'ai_config')",
            name="ck_agent_resource_binding_type",
        ),
        sa.CheckConstraint(
            "length(trim(tenant_key)) > 0",
            name="ck_agent_resource_binding_tenant_nonempty",
        ),
    )
    for column in (
        "tenant_key",
        "resource_type",
        "resource_id",
        "is_global_template",
        "created_by",
        "created_at",
    ):
        op.create_index(
            f"ix_agent_resource_bindings_{column}",
            "agent_resource_bindings",
            [column],
        )
    op.create_index(
        "ix_agent_resource_bindings_tenant_type",
        "agent_resource_bindings",
        ["tenant_key", "resource_type"],
    )

    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    bindings = sa.table(
        "agent_resource_bindings",
        sa.column("tenant_key", sa.String()),
        sa.column("resource_type", sa.String()),
        sa.column("resource_id", sa.Integer()),
        sa.column("is_global_template", sa.Boolean()),
        sa.column("created_by", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )

    for resource_type, table_name in (
        ("ai_config", "ai_config_resources"),
        ("persona", "persona_profiles"),
    ):
        rows = bind.execute(
            sa.text(
                f"""
                SELECT r.id AS resource_id,
                       COALESCE(t.tenant_key, 'default') AS tenant_key,
                       r.created_by AS created_by
                FROM {table_name} r
                LEFT JOIN markets m ON m.id = r.market_id
                LEFT JOIN tenants t ON t.id = m.tenant_id
                ORDER BY r.id
                """
            )
        ).mappings().all()
        for row in rows:
            tenant_key = str(row["tenant_key"] or "default").strip().lower()
            bind.execute(
                sa.insert(bindings).values(
                    tenant_key=tenant_key,
                    resource_type=resource_type,
                    resource_id=int(row["resource_id"]),
                    is_global_template=tenant_key == "default",
                    created_by=row["created_by"],
                    created_at=now,
                )
            )

    # Release state belongs to deployment pointers. Immutable releases are only
    # approved or retired.
    bind.execute(
        sa.text(
            "UPDATE agent_releases SET status = 'approved' "
            "WHERE status IN ('active', 'canary')"
        )
    )
    with op.batch_alter_table("agent_releases") as batch:
        batch.drop_constraint("ck_agent_release_status", type_="check")
        batch.create_check_constraint(
            "ck_agent_release_status",
            "status IN ('approved', 'retired')",
        )

    bind.execute(
        sa.text(
            "UPDATE agent_deployments "
            "SET scope_key = 'market:*|channel:*|language:*|case:*' "
            "WHERE scope_key = '*|*|*|*'"
        )
    )

    definitions = sa.table(
        "agent_definitions",
        sa.column("id", sa.Integer()),
        sa.column("tenant_key", sa.String()),
        sa.column("definition_key", sa.String()),
        sa.column("name", sa.String()),
        sa.column("purpose", sa.Text()),
        sa.column("owner_team_id", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("draft_manifest_json", sa.JSON()),
        sa.column("created_by", sa.Integer()),
        sa.column("updated_by", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    releases = sa.table(
        "agent_releases",
        sa.column("id", sa.Integer()),
        sa.column("definition_id", sa.Integer()),
        sa.column("version", sa.Integer()),
        sa.column("status", sa.String()),
        sa.column("manifest_json", sa.JSON()),
        sa.column("manifest_sha256", sa.String()),
        sa.column("validation_json", sa.JSON()),
        sa.column("created_by", sa.Integer()),
        sa.column("approved_by", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("approved_at", sa.DateTime(timezone=True)),
    )
    deployments = sa.table(
        "agent_deployments",
        sa.column("tenant_key", sa.String()),
        sa.column("environment", sa.String()),
        sa.column("scope_key", sa.String()),
        sa.column("market_id", sa.Integer()),
        sa.column("channel", sa.String()),
        sa.column("language", sa.String()),
        sa.column("case_type", sa.String()),
        sa.column("active_release_id", sa.Integer()),
        sa.column("canary_release_id", sa.Integer()),
        sa.column("canary_percent", sa.Integer()),
        sa.column("is_active", sa.Boolean()),
        sa.column("activated_by", sa.Integer()),
        sa.column("activated_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )

    default_definition = bind.execute(
        sa.select(
            definitions.c.id,
            definitions.c.draft_manifest_json,
        ).where(
            definitions.c.tenant_key == "default",
            definitions.c.definition_key == "default-support-agent",
        )
    ).mappings().one()
    default_release = bind.execute(
        sa.select(
            releases.c.manifest_json,
            releases.c.validation_json,
        ).where(
            releases.c.definition_id == int(default_definition["id"]),
            releases.c.version == 1,
        )
    ).mappings().one()
    manifest = dict(default_release["manifest_json"] or default_definition["draft_manifest_json"] or {})
    validation = dict(default_release["validation_json"] or {})
    validation["manifest_sha256"] = _digest(manifest)
    validation.setdefault("allowed_tools", [])
    validation.setdefault("resources", [])
    validation.setdefault("knowledge", [])
    validation.setdefault("persona", None)
    validation["tenant_key"] = "default"
    bind.execute(
        sa.update(releases)
        .where(
            releases.c.definition_id == int(default_definition["id"]),
            releases.c.version == 1,
        )
        .values(
            status="approved",
            manifest_sha256=_digest(manifest),
            validation_json=validation,
        )
    )

    tenant_keys = [
        str(row[0]).strip().lower()
        for row in bind.execute(
            sa.text("SELECT tenant_key FROM tenants WHERE is_active = true ORDER BY tenant_key")
        ).all()
        if str(row[0]).strip().lower() != "default"
    ]
    for tenant_key in tenant_keys:
        exists = bind.execute(
            sa.select(definitions.c.id).where(
                definitions.c.tenant_key == tenant_key,
                definitions.c.definition_key == "default-support-agent",
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        bind.execute(
            sa.insert(definitions).values(
                tenant_key=tenant_key,
                definition_key="default-support-agent",
                name="Default support Agent",
                purpose="Canonical tenant Agent bootstrap. Replace through governed releases.",
                owner_team_id=None,
                is_active=True,
                draft_manifest_json=manifest,
                created_by=None,
                updated_by=None,
                created_at=now,
                updated_at=now,
            )
        )
        definition_id = bind.execute(
            sa.select(definitions.c.id).where(
                definitions.c.tenant_key == tenant_key,
                definitions.c.definition_key == "default-support-agent",
            )
        ).scalar_one()
        tenant_validation = dict(validation)
        tenant_validation["tenant_key"] = tenant_key
        bind.execute(
            sa.insert(releases).values(
                definition_id=definition_id,
                version=1,
                status="approved",
                manifest_json=manifest,
                manifest_sha256=_digest(manifest),
                validation_json=tenant_validation,
                created_by=None,
                approved_by=None,
                created_at=now,
                approved_at=now,
            )
        )
        release_id = bind.execute(
            sa.select(releases.c.id).where(
                releases.c.definition_id == definition_id,
                releases.c.version == 1,
            )
        ).scalar_one()
        bind.execute(
            sa.insert(deployments).values(
                tenant_key=tenant_key,
                environment="production",
                scope_key="market:*|channel:*|language:*|case:*",
                market_id=None,
                channel=None,
                language=None,
                case_type=None,
                active_release_id=release_id,
                canary_release_id=None,
                canary_percent=0,
                is_active=True,
                activated_by=None,
                activated_at=now,
                updated_at=now,
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    tenant_definition_ids = [
        int(row[0])
        for row in bind.execute(
            sa.text(
                "SELECT id FROM agent_definitions "
                "WHERE definition_key = 'default-support-agent' AND tenant_key <> 'default'"
            )
        ).all()
    ]
    if tenant_definition_ids:
        placeholders = ", ".join(f":id{i}" for i in range(len(tenant_definition_ids)))
        params = {f"id{i}": value for i, value in enumerate(tenant_definition_ids)}
        bind.execute(
            sa.text(
                "DELETE FROM agent_deployments WHERE active_release_id IN "
                f"(SELECT id FROM agent_releases WHERE definition_id IN ({placeholders}))"
            ),
            params,
        )
        bind.execute(
            sa.text(f"DELETE FROM agent_releases WHERE definition_id IN ({placeholders})"),
            params,
        )
        bind.execute(
            sa.text(f"DELETE FROM agent_definitions WHERE id IN ({placeholders})"),
            params,
        )

    with op.batch_alter_table("agent_releases") as batch:
        batch.drop_constraint("ck_agent_release_status", type_="check")
        batch.create_check_constraint(
            "ck_agent_release_status",
            "status IN ('approved', 'canary', 'active', 'retired')",
        )
    bind.execute(
        sa.text(
            "UPDATE agent_releases SET status = 'active' "
            "WHERE definition_id IN (SELECT id FROM agent_definitions WHERE tenant_key = 'default')"
        )
    )
    bind.execute(
        sa.text(
            "UPDATE agent_deployments SET scope_key = '*|*|*|*' "
            "WHERE scope_key = 'market:*|channel:*|language:*|case:*'"
        )
    )

    op.drop_index(
        "ix_agent_resource_bindings_tenant_type",
        table_name="agent_resource_bindings",
    )
    for column in reversed(
        (
            "tenant_key",
            "resource_type",
            "resource_id",
            "is_global_template",
            "created_by",
            "created_at",
        )
    ):
        op.drop_index(
            f"ix_agent_resource_bindings_{column}",
            table_name="agent_resource_bindings",
        )
    op.drop_table("agent_resource_bindings")
