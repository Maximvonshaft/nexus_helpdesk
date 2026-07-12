from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

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
        self.assertNotIn("17", sample)


if __name__ == "__main__":
    unittest.main()
