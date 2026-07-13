from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Protocol

from sqlalchemy import text

MAPPING_SECTIONS = (
    "market_codes",
    "team_ids",
    "user_ids",
    "channel_account_ids",
    "ticket_ids",
    "customer_ids",
)
CORE_ORDER = (
    "markets",
    "teams",
    "users",
    "channel_accounts",
    "customers",
    "tickets",
)
CORE_RELATION_COLUMNS = {
    "markets": ("id", "code", "tenant_id"),
    "teams": ("id", "market_id", "tenant_id"),
    "users": ("id", "team_id", "tenant_id"),
    "channel_accounts": ("id", "market_id", "tenant_id"),
    "customers": ("id", "tenant_id"),
    "tickets": (
        "id",
        "customer_id",
        "market_id",
        "team_id",
        "channel_account_id",
        "assignee_id",
        "created_by",
        "tenant_id",
    ),
}


class FindingsProtocol(Protocol):
    def add(self, reason: str, *, kind: str, record_id: object) -> None: ...


def _dialect_name(inspector) -> str:
    bind = getattr(inspector, "bind", None)
    if bind is None and hasattr(inspector, "_inspector"):
        bind = inspector._inspector.bind
    if bind is None:
        raise ValueError("tenant_resolution_inspector_bind_missing")
    return str(bind.dialect.name)


def table_names(inspector) -> set[str]:
    if _dialect_name(inspector) == "sqlite":
        return set(inspector.get_table_names())
    return set(inspector.get_table_names(schema="public"))


def columns(inspector, table_name: str) -> list[dict[str, Any]]:
    if _dialect_name(inspector) == "sqlite":
        return inspector.get_columns(table_name)
    return inspector.get_columns(table_name, schema="public")


def foreign_keys(inspector, table_name: str) -> list[dict[str, Any]]:
    if _dialect_name(inspector) == "sqlite":
        return inspector.get_foreign_keys(table_name)
    return inspector.get_foreign_keys(table_name, schema="public")


def fetch_rows(
    connection,
    inspector,
    table_name: str,
    requested_columns: Iterable[str],
    *,
    lock_rows: bool = False,
) -> list[dict[str, Any]]:
    if table_name not in table_names(inspector):
        return []
    available = {item["name"] for item in columns(inspector, table_name)}
    requested = tuple(requested_columns)
    if not set(requested).issubset(available):
        return []
    preparer = connection.dialect.identifier_preparer
    selected = ", ".join(preparer.quote(name) for name in requested)
    table = preparer.quote(table_name)
    order = f" ORDER BY {preparer.quote('id')}" if "id" in requested else ""
    suffix = " FOR UPDATE" if lock_rows and connection.dialect.name == "postgresql" else ""
    return [
        dict(row._mapping)
        for row in connection.execute(
            text(f"SELECT {selected} FROM {table}{order}{suffix}")
        )
    ]


def resolve_relation(
    *,
    kind: str,
    record_id: int,
    relation_candidates: set[str],
    explicit: str | None,
    findings: FindingsProtocol,
) -> str | None:
    candidates = set(relation_candidates)
    if explicit:
        candidates.add(explicit)
    if len(relation_candidates) > 1:
        findings.add("tenant.relation_conflict", kind=kind, record_id=record_id)
        return None
    if explicit and relation_candidates and explicit not in relation_candidates:
        findings.add("tenant.explicit_relation_conflict", kind=kind, record_id=record_id)
        return None
    if not candidates:
        findings.add("tenant.assignment_missing", kind=kind, record_id=record_id)
        return None
    return next(iter(candidates))


def resolve_assignments(
    connection,
    inspector,
    manifest: dict[str, Any],
    findings: FindingsProtocol,
    *,
    lock_rows: bool = False,
) -> tuple[dict[str, dict[int, str]], dict[str, int], dict[str, set[str]]]:
    used: dict[str, set[str]] = {section: set() for section in MAPPING_SECTIONS}
    assignments: dict[str, dict[int, str]] = {kind: {} for kind in CORE_ORDER}
    record_counts: dict[str, int] = {}
    available_tables = table_names(inspector)

    for table_name, requested in CORE_RELATION_COLUMNS.items():
        rows = fetch_rows(
            connection,
            inspector,
            table_name,
            requested,
            lock_rows=lock_rows,
        )
        record_counts[table_name] = len(rows)
        if table_name not in available_tables:
            findings.add("tenant.core_table_missing", kind=table_name, record_id=0)
            continue
        available = {item["name"] for item in columns(inspector, table_name)}
        if not set(requested).issubset(available):
            findings.add("tenant.core_columns_missing", kind=table_name, record_id=0)

    market_rows = fetch_rows(
        connection,
        inspector,
        "markets",
        CORE_RELATION_COLUMNS["markets"],
        lock_rows=lock_rows,
    )
    for row in market_rows:
        record_id = int(row["id"])
        code = str(row["code"] or "").strip()
        tenant = manifest["market_codes"].get(code)
        if tenant:
            assignments["markets"][record_id] = tenant
            used["market_codes"].add(code)
        else:
            findings.add("tenant.market_mapping_missing", kind="markets", record_id=record_id)

    team_rows = fetch_rows(
        connection,
        inspector,
        "teams",
        CORE_RELATION_COLUMNS["teams"],
        lock_rows=lock_rows,
    )
    for row in team_rows:
        record_id = int(row["id"])
        market_id = row.get("market_id")
        relation = (
            {assignments["markets"][int(market_id)]}
            if market_id is not None and int(market_id) in assignments["markets"]
            else set()
        )
        explicit = manifest["team_ids"].get(str(record_id))
        if explicit:
            used["team_ids"].add(str(record_id))
        tenant = resolve_relation(
            kind="teams",
            record_id=record_id,
            relation_candidates=relation,
            explicit=explicit,
            findings=findings,
        )
        if tenant:
            assignments["teams"][record_id] = tenant

    user_rows = fetch_rows(
        connection,
        inspector,
        "users",
        CORE_RELATION_COLUMNS["users"],
        lock_rows=lock_rows,
    )
    for row in user_rows:
        record_id = int(row["id"])
        team_id = row.get("team_id")
        relation = (
            {assignments["teams"][int(team_id)]}
            if team_id is not None and int(team_id) in assignments["teams"]
            else set()
        )
        explicit = manifest["user_ids"].get(str(record_id))
        if explicit:
            used["user_ids"].add(str(record_id))
        tenant = resolve_relation(
            kind="users",
            record_id=record_id,
            relation_candidates=relation,
            explicit=explicit,
            findings=findings,
        )
        if tenant:
            assignments["users"][record_id] = tenant

    channel_rows = fetch_rows(
        connection,
        inspector,
        "channel_accounts",
        CORE_RELATION_COLUMNS["channel_accounts"],
        lock_rows=lock_rows,
    )
    for row in channel_rows:
        record_id = int(row["id"])
        market_id = row.get("market_id")
        relation = (
            {assignments["markets"][int(market_id)]}
            if market_id is not None and int(market_id) in assignments["markets"]
            else set()
        )
        explicit = manifest["channel_account_ids"].get(str(record_id))
        if explicit:
            used["channel_account_ids"].add(str(record_id))
        tenant = resolve_relation(
            kind="channel_accounts",
            record_id=record_id,
            relation_candidates=relation,
            explicit=explicit,
            findings=findings,
        )
        if tenant:
            assignments["channel_accounts"][record_id] = tenant

    customer_candidates: dict[int, set[str]] = defaultdict(set)
    ticket_rows = fetch_rows(
        connection,
        inspector,
        "tickets",
        CORE_RELATION_COLUMNS["tickets"],
        lock_rows=lock_rows,
    )
    for row in ticket_rows:
        record_id = int(row["id"])
        relations: set[str] = set()
        for target, raw_id in (
            ("markets", row.get("market_id")),
            ("teams", row.get("team_id")),
            ("channel_accounts", row.get("channel_account_id")),
            ("users", row.get("assignee_id")),
            ("users", row.get("created_by")),
        ):
            if raw_id is not None and int(raw_id) in assignments[target]:
                relations.add(assignments[target][int(raw_id)])
        explicit = manifest["ticket_ids"].get(str(record_id))
        if explicit:
            used["ticket_ids"].add(str(record_id))
        tenant = resolve_relation(
            kind="tickets",
            record_id=record_id,
            relation_candidates=relations,
            explicit=explicit,
            findings=findings,
        )
        if tenant:
            assignments["tickets"][record_id] = tenant
            if row.get("customer_id") is not None:
                customer_candidates[int(row["customer_id"])].add(tenant)

    customer_rows = fetch_rows(
        connection,
        inspector,
        "customers",
        CORE_RELATION_COLUMNS["customers"],
        lock_rows=lock_rows,
    )
    for row in customer_rows:
        record_id = int(row["id"])
        relations = customer_candidates.get(record_id, set())
        explicit = manifest["customer_ids"].get(str(record_id))
        if explicit:
            used["customer_ids"].add(str(record_id))
        if len(relations) > 1:
            findings.add(
                "tenant.customer_cross_tenant_conflict",
                kind="customers",
                record_id=record_id,
            )
            continue
        tenant = resolve_relation(
            kind="customers",
            record_id=record_id,
            relation_candidates=set(relations),
            explicit=explicit,
            findings=findings,
        )
        if tenant:
            assignments["customers"][record_id] = tenant

    for section in MAPPING_SECTIONS:
        for key in sorted(set(manifest[section]) - used[section]):
            findings.add("tenant.mapping_unused", kind=section, record_id=key)

    return assignments, record_counts, used
