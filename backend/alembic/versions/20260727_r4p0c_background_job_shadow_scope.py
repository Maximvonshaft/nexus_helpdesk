"""Represent the isolated legacy-shadow BackgroundJob execution domain.

Revision ID: 20260727_r4p0c
Revises: 20260727_r4p0b
Create Date: 2026-07-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260727_r4p0c"
down_revision = "20260727_r4p0b"
branch_labels = None
depends_on = None


def _replace_scope_type_constraint(*, include_shadow: bool) -> None:
    dialect = op.get_bind().dialect.name
    expression = (
        "scope_type IN ('tenant','shadow','platform','unresolved')"
        if include_shadow
        else "scope_type IN ('tenant','platform','unresolved')"
    )
    if dialect == "sqlite":
        with op.batch_alter_table("background_job_scopes") as batch:
            batch.drop_constraint(
                "ck_background_job_scope_type",
                type_="check",
            )
            batch.create_check_constraint(
                "ck_background_job_scope_type",
                expression,
            )
    else:
        op.drop_constraint(
            "ck_background_job_scope_type",
            "background_job_scopes",
            type_="check",
        )
        op.create_check_constraint(
            "ck_background_job_scope_type",
            "background_job_scopes",
            expression,
        )


def upgrade() -> None:
    _replace_scope_type_constraint(include_shadow=True)
    # Earlier revisions conservatively classified every tenantless known Job as
    # unresolved. Preserve those rows in the explicit shadow domain. Runtime
    # execution still requires TENANT_RUNTIME_AUTHORITY_MODE=shadow; production
    # enforce mode never claims them. Unknown purposes remain unresolved.
    op.execute(
        sa.text(
            "UPDATE background_job_scopes "
            "SET scope_type = 'shadow', updated_at = CURRENT_TIMESTAMP "
            "WHERE scope_type = 'unresolved' "
            "AND tenant_id IS NULL "
            "AND purpose <> 'unclassified'"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE background_job_scopes "
            "SET scope_type = 'unresolved', updated_at = CURRENT_TIMESTAMP "
            "WHERE scope_type = 'shadow'"
        )
    )
    _replace_scope_type_constraint(include_shadow=False)
