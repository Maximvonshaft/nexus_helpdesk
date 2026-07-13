from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, inspect, text

MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_MAPPING_ENTRIES = 200_000
MAX_ISSUE_SAMPLES = 12
CURRENT_ALEMBIC_HEAD = "20260713_0059"
CURRENT_TENANT_COLUMNS = frozenset({
    "case_contexts.tenant_id",
    "channel_accounts.tenant_id",
    "customers.tenant_id",
    "knowledge_chunks.tenant_id",
    "knowledge_items.tenant_id",
    "markets.tenant_id",
    "operations_dispatch_outbox.tenant_key",
    "operator_queue_scope_grants.tenant_key",
    "runtime_decision_audits.tenant_id",
    "teams.tenant_id",
    "tenants.tenant_key",
    "tickets.tenant_id",
    "users.tenant_id",
    "webchat_conversations.tenant_key",
    "webchat_public_origin_bindings.tenant_key",
})
_TENANT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,79}$")
_MARKET_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,31}$")
_MAPPING_SECTIONS = (
    "market_codes",
    "team_ids",
    "user_ids",
    "channel_account_ids",
    "ticket_ids",
    "customer_ids",
)
_CORE_TABLE_COLUMNS = {
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


class TenantPreflightError(ValueError):
    pass


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise TenantPreflightError("mapping_manifest_invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TenantPreflightError("mapping_manifest_invalid") from exc
    if not isinstance(payload, dict):
        raise TenantPreflightError("mapping_manifest_not_object")
    expected = {"schema_version", "tenants", *_MAPPING_SECTIONS}
    if set(payload) != expected:
        raise TenantPreflightError("mapping_manifest_keys_invalid")
    if payload.get("schema_version") != "nexus_tenant_backfill_mapping_v1":
        raise TenantPreflightError("mapping_manifest_schema_invalid")
    tenants = payload.get("tenants")
    if not isinstance(tenants, list) or not tenants or len(tenants) > 1000:
        raise TenantPreflightError("mapping_manifest_tenants_invalid")
    tenant_keys: set[str] = set()
    for entry in tenants:
        if not isinstance(entry, dict) or set(entry) != {"tenant_key", "display_name"}:
            raise TenantPreflightError("mapping_manifest_tenant_entry_invalid")
        key = str(entry.get("tenant_key") or "").strip().lower()
        display = " ".join(str(entry.get("display_name") or "").strip().split())
        if not _TENANT_KEY_RE.fullmatch(key) or key == "default" or not 2 <= len(display) <= 160:
            raise TenantPreflightError("mapping_manifest_tenant_identity_invalid")
        if key in tenant_keys:
            raise TenantPreflightError("mapping_manifest_tenant_duplicate")
        tenant_keys.add(key)
        entry["tenant_key"] = key
        entry["display_name"] = display

    for section in _MAPPING_SECTIONS:
        raw = payload.get(section)
        if not isinstance(raw, dict) or len(raw) > MAX_MAPPING_ENTRIES:
            raise TenantPreflightError(f"mapping_manifest_{section}_invalid")
        normalized: dict[str, str] = {}
        for raw_key, raw_tenant in raw.items():
            key = str(raw_key or "").strip()
            tenant = str(raw_tenant or "").strip().lower()
            if section == "market_codes":
                if not _MARKET_CODE_RE.fullmatch(key):
                    raise TenantPreflightError("mapping_manifest_market_code_invalid")
            elif not key.isdigit() or int(key) <= 0:
                raise TenantPreflightError(f"mapping_manifest_{section}_key_invalid")
            if tenant not in tenant_keys:
                raise TenantPreflightError(f"mapping_manifest_{section}_tenant_unknown")
            if key in normalized:
                raise TenantPreflightError(f"mapping_manifest_{section}_duplicate")
            normalized[key] = tenant
        payload[section] = normalized
    payload["tenant_keys"] = tenant_keys
    return payload


def _fingerprint(kind: str, record_id: object) -> str:
    return "sha256:" + hashlib.sha256(f"{kind}:{record_id}".encode("utf-8")).hexdigest()


class Findings:
    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.samples: dict[str, list[str]] = defaultdict(list)

    def add(self, reason: str, *, kind: str, record_id: object) -> None:
        self.counts[reason] += 1
        samples = self.samples[reason]
        if len(samples) < MAX_ISSUE_SAMPLES:
            samples.append(_fingerprint(kind, record_id))

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_count": sum(self.counts.values()),
            "counts": dict(sorted(self.counts.items())),
            "samples": {key: values for key, values in sorted(self.samples.items())},
        }


def _fetch_rows(connection, inspector, table_name: str, columns: Iterable[str]) -> list[dict[str, Any]]:
    if table_name not in inspector.get_table_names(schema="public"):
        return []
    available = {item["name"] for item in inspector.get_columns(table_name, schema="public")}
    requested = tuple(columns)
    if not set(requested).issubset(available):
        return []
    preparer = connection.dialect.identifier_preparer
    selected = ", ".join(preparer.quote(name) for name in requested)
    table = preparer.quote(table_name)
    return [dict(row._mapping) for row in connection.execute(text(f"SELECT {selected} FROM {table}"))]


def _resolve_relation(
    *,
    kind: str,
    record_id: int,
    relation_candidates: set[str],
    explicit: str | None,
    findings: Findings,
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


def _load_tenant_principals(connection, inspector, tenant_keys: set[str], findings: Findings) -> dict[int, str]:
    if "tenants" not in inspector.get_table_names(schema="public"):
        findings.add("tenant.principal_table_missing", kind="schema", record_id="tenants")
        return {}
    rows = _fetch_rows(connection, inspector, "tenants", ("id", "tenant_key"))
    principals: dict[int, str] = {}
    observed_keys: set[str] = set()
    for row in rows:
        principal_id = int(row["id"])
        tenant_key = str(row["tenant_key"] or "").strip().lower()
        if not tenant_key:
            findings.add("tenant.principal_key_missing", kind="tenants", record_id=principal_id)
            continue
        if tenant_key == "default":
            findings.add("tenant.existing_default_forbidden", kind="tenants", record_id=principal_id)
            continue
        if tenant_key not in tenant_keys:
            findings.add("tenant.principal_key_unknown", kind="tenants", record_id=principal_id)
            continue
        principals[principal_id] = tenant_key
        observed_keys.add(tenant_key)
    for tenant_key in sorted(tenant_keys - observed_keys):
        findings.add("tenant.principal_missing", kind="tenants", record_id=tenant_key)
    return principals


def _relational_tenant_id_columns(inspector, table_name: str) -> set[str]:
    columns: set[str] = set()
    for foreign_key in inspector.get_foreign_keys(table_name, schema="public"):
        if foreign_key.get("referred_table") != "tenants":
            continue
        if foreign_key.get("referred_columns") != ["id"]:
            continue
        constrained = foreign_key.get("constrained_columns") or []
        if len(constrained) == 1:
            columns.add(str(constrained[0]))
    return columns


def _add_counted_finding(
    findings: Findings,
    reason: str,
    *,
    kind: str,
    record_id: object,
    count: int = 1,
) -> None:
    for index in range(min(count, MAX_ISSUE_SAMPLES)):
        findings.add(reason, kind=kind, record_id=f"{record_id}:{index}")
    if count > MAX_ISSUE_SAMPLES:
        findings.counts[reason] += count - MAX_ISSUE_SAMPLES


def _scan_existing_tenant_columns(
    connection,
    inspector,
    tenant_keys: set[str],
    principal_keys: dict[int, str],
    assignments: dict[str, dict[int, str]],
    findings: Findings,
) -> dict[str, int]:
    scanned: dict[str, int] = {}
    preparer = connection.dialect.identifier_preparer
    for table_name in sorted(inspector.get_table_names(schema="public")):
        reflected = inspector.get_columns(table_name, schema="public")
        columns = {item["name"] for item in reflected}
        relational_columns = _relational_tenant_id_columns(inspector, table_name)
        for column_name in sorted(columns & {"tenant_id", "tenant_key"}):
            kind = f"{table_name}.{column_name}"
            table = preparer.quote(table_name)
            column = preparer.quote(column_name)
            if column_name in relational_columns:
                if "id" in columns:
                    id_column = preparer.quote("id")
                    rows = connection.execute(
                        text(f"SELECT {id_column} AS record_id, {column} AS value FROM {table}")
                    ).all()
                    scanned[kind] = len(rows)
                    expected_assignments = assignments.get(table_name, {})
                    for row in rows:
                        record_id = int(row.record_id)
                        if row.value is None:
                            findings.add("tenant.existing_value_missing", kind=kind, record_id=record_id)
                            continue
                        try:
                            principal_id = int(row.value)
                        except (TypeError, ValueError):
                            findings.add("tenant.existing_principal_invalid", kind=kind, record_id=record_id)
                            continue
                        tenant_key = principal_keys.get(principal_id)
                        if tenant_key is None:
                            findings.add("tenant.existing_principal_unknown", kind=kind, record_id=record_id)
                            continue
                        expected = expected_assignments.get(record_id)
                        if expected and tenant_key != expected:
                            findings.add("tenant.relational_assignment_conflict", kind=kind, record_id=record_id)
                    continue

                rows = connection.execute(
                    text(f"SELECT {column} AS value, count(*) AS count FROM {table} GROUP BY {column}")
                ).all()
                scanned[kind] = sum(int(row.count) for row in rows)
                for row in rows:
                    count = int(row.count)
                    if row.value is None:
                        _add_counted_finding(
                            findings,
                            "tenant.existing_value_missing",
                            kind=kind,
                            record_id="null",
                            count=count,
                        )
                        continue
                    try:
                        principal_id = int(row.value)
                    except (TypeError, ValueError):
                        _add_counted_finding(
                            findings,
                            "tenant.existing_principal_invalid",
                            kind=kind,
                            record_id=row.value,
                            count=count,
                        )
                        continue
                    if principal_id not in principal_keys:
                        _add_counted_finding(
                            findings,
                            "tenant.existing_principal_unknown",
                            kind=kind,
                            record_id=principal_id,
                            count=count,
                        )
                continue

            rows = connection.execute(
                text(
                    f"SELECT CAST({column} AS TEXT) AS value, count(*) AS count "
                    f"FROM {table} GROUP BY CAST({column} AS TEXT)"
                )
            ).all()
            scanned[kind] = sum(int(row.count) for row in rows)
            for row in rows:
                value = str(row.value or "").strip().lower()
                count = int(row.count)
                if not value:
                    _add_counted_finding(
                        findings,
                        "tenant.existing_value_missing",
                        kind=kind,
                        record_id="empty",
                        count=count,
                    )
                elif value == "default":
                    _add_counted_finding(
                        findings,
                        "tenant.existing_default_forbidden",
                        kind=kind,
                        record_id="default",
                        count=count,
                    )
                elif value not in tenant_keys:
                    _add_counted_finding(
                        findings,
                        "tenant.existing_value_unknown",
                        kind=kind,
                        record_id=value,
                        count=count,
                    )
    return scanned


def run_preflight(database_url: str, manifest_path: Path, output_path: Path) -> int:
    manifest = _load_manifest(manifest_path)
    tenant_keys: set[str] = manifest["tenant_keys"]
    findings = Findings()
    used: dict[str, set[str]] = {section: set() for section in _MAPPING_SECTIONS}
    assignments: dict[str, dict[int, str]] = {kind: {} for kind in _CORE_TABLE_COLUMNS}
    record_counts: dict[str, int] = {}

    engine = create_engine(database_url, future=True)
    try:
        inspector = inspect(engine)
        public_tables = set(inspector.get_table_names(schema="public"))
        with engine.connect() as connection:
            for table_name, columns in _CORE_TABLE_COLUMNS.items():
                if table_name not in public_tables:
                    findings.add("tenant.core_table_missing", kind=table_name, record_id=0)
                    record_counts[table_name] = 0
                    continue
                rows = _fetch_rows(connection, inspector, table_name, columns)
                record_counts[table_name] = len(rows)
                if rows or set(columns).issubset({item["name"] for item in inspector.get_columns(table_name)}):
                    continue
                findings.add("tenant.core_columns_missing", kind=table_name, record_id=0)

            market_rows = _fetch_rows(connection, inspector, "markets", _CORE_TABLE_COLUMNS["markets"])
            for row in market_rows:
                record_id = int(row["id"])
                code = str(row["code"] or "").strip()
                tenant = manifest["market_codes"].get(code)
                if tenant:
                    assignments["markets"][record_id] = tenant
                    used["market_codes"].add(code)
                else:
                    findings.add("tenant.market_mapping_missing", kind="markets", record_id=record_id)

            team_rows = _fetch_rows(connection, inspector, "teams", _CORE_TABLE_COLUMNS["teams"])
            for row in team_rows:
                record_id = int(row["id"])
                relation = {assignments["markets"][int(row["market_id"])]} if row.get("market_id") in assignments["markets"] else set()
                explicit = manifest["team_ids"].get(str(record_id))
                if explicit:
                    used["team_ids"].add(str(record_id))
                tenant = _resolve_relation(kind="teams", record_id=record_id, relation_candidates=relation, explicit=explicit, findings=findings)
                if tenant:
                    assignments["teams"][record_id] = tenant

            user_rows = _fetch_rows(connection, inspector, "users", _CORE_TABLE_COLUMNS["users"])
            for row in user_rows:
                record_id = int(row["id"])
                relation = {assignments["teams"][int(row["team_id"])]} if row.get("team_id") in assignments["teams"] else set()
                explicit = manifest["user_ids"].get(str(record_id))
                if explicit:
                    used["user_ids"].add(str(record_id))
                tenant = _resolve_relation(kind="users", record_id=record_id, relation_candidates=relation, explicit=explicit, findings=findings)
                if tenant:
                    assignments["users"][record_id] = tenant

            channel_rows = _fetch_rows(connection, inspector, "channel_accounts", _CORE_TABLE_COLUMNS["channel_accounts"])
            for row in channel_rows:
                record_id = int(row["id"])
                relation = {assignments["markets"][int(row["market_id"])]} if row.get("market_id") in assignments["markets"] else set()
                explicit = manifest["channel_account_ids"].get(str(record_id))
                if explicit:
                    used["channel_account_ids"].add(str(record_id))
                tenant = _resolve_relation(kind="channel_accounts", record_id=record_id, relation_candidates=relation, explicit=explicit, findings=findings)
                if tenant:
                    assignments["channel_accounts"][record_id] = tenant

            customer_candidates: dict[int, set[str]] = defaultdict(set)
            ticket_rows = _fetch_rows(connection, inspector, "tickets", _CORE_TABLE_COLUMNS["tickets"])
            for row in ticket_rows:
                record_id = int(row["id"])
                relations: set[str] = set()
                links = (
                    ("markets", row.get("market_id")),
                    ("teams", row.get("team_id")),
                    ("channel_accounts", row.get("channel_account_id")),
                    ("users", row.get("assignee_id")),
                    ("users", row.get("created_by")),
                )
                for target, raw_id in links:
                    if raw_id is not None and int(raw_id) in assignments[target]:
                        relations.add(assignments[target][int(raw_id)])
                explicit = manifest["ticket_ids"].get(str(record_id))
                if explicit:
                    used["ticket_ids"].add(str(record_id))
                tenant = _resolve_relation(kind="tickets", record_id=record_id, relation_candidates=relations, explicit=explicit, findings=findings)
                if tenant:
                    assignments["tickets"][record_id] = tenant
                    if row.get("customer_id") is not None:
                        customer_candidates[int(row["customer_id"])].add(tenant)

            customer_rows = _fetch_rows(connection, inspector, "customers", _CORE_TABLE_COLUMNS["customers"])
            for row in customer_rows:
                record_id = int(row["id"])
                relations = customer_candidates.get(record_id, set())
                explicit = manifest["customer_ids"].get(str(record_id))
                if explicit:
                    used["customer_ids"].add(str(record_id))
                if len(relations) > 1:
                    findings.add("tenant.customer_cross_tenant_conflict", kind="customers", record_id=record_id)
                    continue
                tenant = _resolve_relation(kind="customers", record_id=record_id, relation_candidates=set(relations), explicit=explicit, findings=findings)
                if tenant:
                    assignments["customers"][record_id] = tenant

            principal_keys = _load_tenant_principals(connection, inspector, tenant_keys, findings)
            scanned_tenant_columns = _scan_existing_tenant_columns(
                connection,
                inspector,
                tenant_keys,
                principal_keys,
                assignments,
                findings,
            )
            observed_columns = set(scanned_tenant_columns)
            for missing_column in sorted(CURRENT_TENANT_COLUMNS - observed_columns):
                findings.add(
                    "tenant.current_schema_column_missing",
                    kind="schema",
                    record_id=missing_column,
                )

        for section in _MAPPING_SECTIONS:
            for key in sorted(set(manifest[section]) - used[section]):
                findings.add("tenant.mapping_unused", kind=section, record_id=key)

        issue_data = findings.as_dict()
        payload = {
            "schema_version": "nexus_tenant_principal_preflight_v3",
            "schema_baseline": {
                "alembic_head": CURRENT_ALEMBIC_HEAD,
                "expected_existing_tenant_columns": sorted(CURRENT_TENANT_COLUMNS),
                "observed_existing_tenant_columns": sorted(scanned_tenant_columns),
            },
            "status": "pass" if issue_data["issue_count"] == 0 else "fail",
            "tenant_count": len(tenant_keys),
            "record_counts": record_counts,
            "assignment_counts": {kind: len(values) for kind, values in assignments.items()},
            "scanned_tenant_columns": scanned_tenant_columns,
            "issues": issue_data,
            "implicit_default_backfill_allowed": False,
            "production_mutation_performed": False,
        }
        encoded = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if len(encoded.encode("utf-8")) > 512 * 1024:
            raise TenantPreflightError("preflight_output_excessive")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(encoded, encoding="utf-8")
        return 0 if payload["status"] == "pass" else 1
    finally:
        engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        return run_preflight(args.database_url, args.mapping, args.output)
    except (TenantPreflightError, OSError, ValueError) as exc:
        print(f"tenant_preflight_error:{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
