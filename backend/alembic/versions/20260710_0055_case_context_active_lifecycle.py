"""enforce active Case Context lifecycle and exact identity uniqueness

Revision ID: 20260710_0055
Revises: 20260709_0054
Create Date: 2026-07-10

Operator remediation
--------------------
Upgrade preserves every Case Context row. Before unique indexes are created it
marks already closed, archived, or expired rows inactive, then fails closed if
multiple active rows remain for any tenant/exact-identity combination. The error
contains the identity and row IDs. Resolve duplicates manually by selecting the
one current case row and setting ``is_active = false`` on the historical rows;
do not delete or merge records. Re-run the migration after remediation.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260710_0055"
down_revision = "20260709_0054"
branch_labels = None
depends_on = None

_TABLE = "case_contexts"
_OLD_UNIQUE = "uq_case_context_conversation_ticket"
_ACTIVE_INDEXES = (
    "uq_case_context_active_conversation_only",
    "uq_case_context_active_ticket_only",
    "uq_case_context_active_conversation_ticket",
)


def _inspector(bind):
    return sa.inspect(bind)


def _column_names(bind) -> set[str]:
    if _TABLE not in _inspector(bind).get_table_names():
        return set()
    return {item["name"] for item in _inspector(bind).get_columns(_TABLE)}


def _index_names(bind) -> set[str]:
    if _TABLE not in _inspector(bind).get_table_names():
        return set()
    return {item["name"] for item in _inspector(bind).get_indexes(_TABLE)}


def _unique_names(bind) -> set[str]:
    if _TABLE not in _inspector(bind).get_table_names():
        return set()
    return {item.get("name") for item in _inspector(bind).get_unique_constraints(_TABLE) if item.get("name")}


def _drop_old_unique(bind) -> None:
    if _OLD_UNIQUE not in _unique_names(bind):
        return
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table(_TABLE, recreate="always") as batch:
            batch.drop_constraint(_OLD_UNIQUE, type_="unique")
    else:
        op.drop_constraint(_OLD_UNIQUE, _TABLE, type_="unique")


def _duplicate_groups(bind, *, mode: str) -> list[dict[str, object]]:
    if mode == "conversation_only":
        group_columns = "tenant_id, conversation_id"
        predicate = "conversation_id IS NOT NULL AND ticket_id IS NULL"
        identity_columns = ("tenant_id", "conversation_id")
    elif mode == "ticket_only":
        group_columns = "tenant_id, ticket_id"
        predicate = "conversation_id IS NULL AND ticket_id IS NOT NULL"
        identity_columns = ("tenant_id", "ticket_id")
    else:
        group_columns = "tenant_id, conversation_id, ticket_id"
        predicate = "conversation_id IS NOT NULL AND ticket_id IS NOT NULL"
        identity_columns = ("tenant_id", "conversation_id", "ticket_id")

    groups = bind.execute(sa.text(
        f"SELECT {group_columns}, COUNT(*) AS duplicate_count "
        f"FROM {_TABLE} WHERE is_active = true AND {predicate} "
        f"GROUP BY {group_columns} HAVING COUNT(*) > 1"
    )).mappings().all()
    results: list[dict[str, object]] = []
    for group in groups:
        clauses = ["is_active = true"]
        params: dict[str, object] = {}
        for index, column in enumerate(identity_columns):
            key = f"value_{index}"
            clauses.append(f"{column} = :{key}")
            params[key] = group[column]
        row_ids = bind.execute(
            sa.text(f"SELECT id FROM {_TABLE} WHERE {' AND '.join(clauses)} ORDER BY id"),
            params,
        ).scalars().all()
        results.append({
            "mode": mode,
            "identity": {column: group[column] for column in identity_columns},
            "row_ids": list(row_ids),
        })
    return results


def _preflight_duplicates(bind) -> None:
    duplicates: list[dict[str, object]] = []
    for mode in ("conversation_only", "ticket_only", "conversation_ticket"):
        duplicates.extend(_duplicate_groups(bind, mode=mode))
    if duplicates:
        rendered = "; ".join(
            f"{item['mode']} identity={item['identity']} row_ids={item['row_ids']}"
            for item in duplicates
        )
        raise RuntimeError(
            "case_context_active_identity_duplicates_detected: "
            + rendered
            + ". Set is_active=false on historical rows after operator review; do not delete data."
        )


def _preflight_legacy_unique_downgrade(bind) -> None:
    duplicates = bind.execute(sa.text(
        f"SELECT conversation_id, ticket_id, COUNT(*) AS duplicate_count "
        f"FROM {_TABLE} "
        "WHERE conversation_id IS NOT NULL AND ticket_id IS NOT NULL "
        "GROUP BY conversation_id, ticket_id HAVING COUNT(*) > 1"
    )).mappings().all()
    if not duplicates:
        return
    rendered: list[str] = []
    for group in duplicates:
        row_ids = bind.execute(
            sa.text(
                f"SELECT id FROM {_TABLE} "
                "WHERE conversation_id = :conversation_id AND ticket_id = :ticket_id "
                "ORDER BY id"
            ),
            {
                "conversation_id": group["conversation_id"],
                "ticket_id": group["ticket_id"],
            },
        ).scalars().all()
        rendered.append(
            f"identity={{'conversation_id': {group['conversation_id']}, 'ticket_id': {group['ticket_id']}}} "
            f"row_ids={list(row_ids)}"
        )
    raise RuntimeError(
        "case_context_legacy_unique_downgrade_blocked: "
        + "; ".join(rendered)
        + ". The old schema cannot represent preserved historical rows. Keep 0055 applied or "
        "archive/export the history through an explicitly approved data migration; do not delete rows."
    )


def _create_active_indexes(bind) -> None:
    existing = _index_names(bind)
    definitions = (
        (
            "uq_case_context_active_conversation_only",
            ["tenant_id", "conversation_id"],
            "is_active = 1 AND conversation_id IS NOT NULL AND ticket_id IS NULL",
            "is_active IS TRUE AND conversation_id IS NOT NULL AND ticket_id IS NULL",
        ),
        (
            "uq_case_context_active_ticket_only",
            ["tenant_id", "ticket_id"],
            "is_active = 1 AND conversation_id IS NULL AND ticket_id IS NOT NULL",
            "is_active IS TRUE AND conversation_id IS NULL AND ticket_id IS NOT NULL",
        ),
        (
            "uq_case_context_active_conversation_ticket",
            ["tenant_id", "conversation_id", "ticket_id"],
            "is_active = 1 AND conversation_id IS NOT NULL AND ticket_id IS NOT NULL",
            "is_active IS TRUE AND conversation_id IS NOT NULL AND ticket_id IS NOT NULL",
        ),
    )
    for name, columns, sqlite_predicate, postgres_predicate in definitions:
        if name in existing:
            continue
        op.create_index(
            name,
            _TABLE,
            columns,
            unique=True,
            sqlite_where=sa.text(sqlite_predicate),
            postgresql_where=sa.text(postgres_predicate),
        )


def upgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _inspector(bind).get_table_names():
        return
    if "is_active" not in _column_names(bind):
        op.add_column(
            _TABLE,
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    bind.execute(sa.text(
        f"UPDATE {_TABLE} SET is_active = false "
        "WHERE closed_at IS NOT NULL "
        "OR status IN ('closed', 'archived') "
        "OR (expires_at IS NOT NULL AND expires_at <= CURRENT_TIMESTAMP)"
    ))
    _preflight_duplicates(bind)
    _drop_old_unique(bind)
    if "ix_case_contexts_is_active" not in _index_names(bind):
        op.create_index("ix_case_contexts_is_active", _TABLE, ["is_active"], unique=False)
    _create_active_indexes(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if _TABLE not in _inspector(bind).get_table_names():
        return
    _preflight_legacy_unique_downgrade(bind)
    existing_indexes = _index_names(bind)
    for name in _ACTIVE_INDEXES:
        if name in existing_indexes:
            op.drop_index(name, table_name=_TABLE)
    if "ix_case_contexts_is_active" in _index_names(bind):
        op.drop_index("ix_case_contexts_is_active", table_name=_TABLE)

    if _OLD_UNIQUE not in _unique_names(bind):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(_TABLE, recreate="always") as batch:
                batch.create_unique_constraint(_OLD_UNIQUE, ["conversation_id", "ticket_id"])
                if "is_active" in _column_names(bind):
                    batch.drop_column("is_active")
        else:
            op.create_unique_constraint(_OLD_UNIQUE, _TABLE, ["conversation_id", "ticket_id"])
            if "is_active" in _column_names(bind):
                op.drop_column(_TABLE, "is_active")
    elif "is_active" in _column_names(bind):
        if bind.dialect.name == "sqlite":
            with op.batch_alter_table(_TABLE, recreate="always") as batch:
                batch.drop_column("is_active")
        else:
            op.drop_column(_TABLE, "is_active")
