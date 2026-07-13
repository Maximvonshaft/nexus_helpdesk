from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

import sqlalchemy as sa

MODULE_PATH = Path(__file__).with_name("tenant_principal_preflight.py")
SPEC = importlib.util.spec_from_file_location("tenant_principal_preflight", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class TenantPrincipalPreflightTests(unittest.TestCase):
    def _manifest(self):
        return {
            "schema_version": "nexus_tenant_backfill_mapping_v1",
            "tenants": [{"tenant_key": "tenant-a", "display_name": "Tenant A"}],
            "market_codes": {"ME": "tenant-a"},
            "team_ids": {},
            "user_ids": {},
            "channel_account_ids": {},
            "ticket_ids": {},
            "customer_ids": {},
        }

    def _write(self, payload) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "mapping.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_valid_manifest_normalizes_tenant_key(self) -> None:
        payload = self._manifest()
        payload["tenants"][0]["tenant_key"] = "tenant-A"
        loaded = MODULE._load_manifest(self._write(payload))
        self.assertEqual(loaded["tenants"][0]["tenant_key"], "tenant-a")
        self.assertEqual(loaded["tenant_keys"], {"tenant-a"})

    def test_default_tenant_is_forbidden(self) -> None:
        payload = self._manifest()
        payload["tenants"][0]["tenant_key"] = "default"
        with self.assertRaisesRegex(MODULE.TenantPreflightError, "tenant_identity"):
            MODULE._load_manifest(self._write(payload))

    def test_mapping_to_unknown_tenant_is_forbidden(self) -> None:
        payload = self._manifest()
        payload["market_codes"]["CH"] = "tenant-unknown"
        with self.assertRaisesRegex(MODULE.TenantPreflightError, "tenant_unknown"):
            MODULE._load_manifest(self._write(payload))

    def test_relation_conflict_cannot_be_explicitly_overridden(self) -> None:
        findings = MODULE.Findings()
        resolved = MODULE._resolve_relation(
            kind="tickets",
            record_id=9,
            relation_candidates={"tenant-a", "tenant-b"},
            explicit="tenant-a",
            findings=findings,
        )
        self.assertIsNone(resolved)
        self.assertEqual(findings.counts["tenant.relation_conflict"], 1)

    def test_explicit_mapping_must_match_one_inferred_relation(self) -> None:
        findings = MODULE.Findings()
        resolved = MODULE._resolve_relation(
            kind="users",
            record_id=3,
            relation_candidates={"tenant-a"},
            explicit="tenant-b",
            findings=findings,
        )
        self.assertIsNone(resolved)
        self.assertEqual(findings.counts["tenant.explicit_relation_conflict"], 1)

    def test_missing_assignment_fails_closed_with_hashed_sample(self) -> None:
        findings = MODULE.Findings()
        resolved = MODULE._resolve_relation(
            kind="customers",
            record_id=17,
            relation_candidates=set(),
            explicit=None,
            findings=findings,
        )
        self.assertIsNone(resolved)
        report = findings.as_dict()
        self.assertEqual(report["counts"]["tenant.assignment_missing"], 1)
        sample = report["samples"]["tenant.assignment_missing"][0]
        self.assertRegex(sample, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("customers:17", sample)

    def test_current_schema_baseline_is_explicit_and_bounded(self) -> None:
        self.assertEqual(MODULE.CURRENT_ALEMBIC_HEAD, "20260713_0059")
        self.assertEqual(len(MODULE.CURRENT_TENANT_COLUMNS), 15)
        self.assertIn("tenants.tenant_key", MODULE.CURRENT_TENANT_COLUMNS)
        self.assertIn("tickets.tenant_id", MODULE.CURRENT_TENANT_COLUMNS)
        self.assertNotIn("default", MODULE.CURRENT_TENANT_COLUMNS)

    def test_relational_tenant_id_resolves_to_manifest_key_and_detects_drift(self) -> None:
        engine = sa.create_engine("sqlite:///:memory:", future=True)
        metadata = sa.MetaData()
        sa.Table(
            "tenants",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_key", sa.String(80), nullable=False),
        )
        sa.Table(
            "markets",
            metadata,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("tenant_id", sa.Integer(), sa.ForeignKey("tenants.id"), nullable=True),
        )
        metadata.create_all(engine)

        class PublicInspector:
            def __init__(self, connection):
                self._inspector = sa.inspect(connection)

            def get_table_names(self, schema=None):
                return self._inspector.get_table_names()

            def get_columns(self, table_name, schema=None):
                return self._inspector.get_columns(table_name)

            def get_foreign_keys(self, table_name, schema=None):
                return self._inspector.get_foreign_keys(table_name)

        with engine.begin() as connection:
            connection.execute(sa.text("INSERT INTO tenants (id, tenant_key) VALUES (7, 'tenant-a')"))
            connection.execute(sa.text("INSERT INTO markets (id, tenant_id) VALUES (3, 7)"))
            inspector = PublicInspector(connection)
            findings = MODULE.Findings()
            principals = MODULE._load_tenant_principals(
                connection, inspector, {"tenant-a"}, findings
            )
            scanned = MODULE._scan_existing_tenant_columns(
                connection,
                inspector,
                {"tenant-a"},
                principals,
                {"markets": {3: "tenant-a"}},
                findings,
            )
            self.assertEqual(principals, {7: "tenant-a"})
            self.assertEqual(scanned["markets.tenant_id"], 1)
            self.assertEqual(findings.as_dict()["issue_count"], 0)

            drift = MODULE.Findings()
            MODULE._scan_existing_tenant_columns(
                connection,
                inspector,
                {"tenant-a"},
                principals,
                {"markets": {3: "tenant-b"}},
                drift,
            )
            self.assertEqual(drift.counts["tenant.relational_assignment_conflict"], 1)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
