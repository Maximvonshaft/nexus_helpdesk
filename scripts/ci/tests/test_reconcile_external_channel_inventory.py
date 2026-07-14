from __future__ import annotations

import unittest

from scripts.ci.check_external_channel_retirement import InventoryError
from scripts.ci.reconcile_external_channel_inventory import (
    canonical_deleted_paths,
    reconcile_inventory_payload,
)


class CanonicalExternalChannelReconciliationTests(unittest.TestCase):
    def test_only_manifest_governed_deleted_paths_are_removed(self) -> None:
        inventory = {
            "inventory_version": "v1",
            "rules": [
                {"path": None, "paths": ["frontend/app.js", "webapp/src/lib/types.ts"], "glob": None},
            ],
        }
        reconciled, removed = reconcile_inventory_payload(
            inventory,
            tracked_paths=["webapp/src/lib/types.ts"],
            deleted_paths=frozenset({"frontend/app.js"}),
        )
        self.assertEqual(removed, ("frontend/app.js",))
        self.assertEqual(reconciled["rules"][0]["paths"], ["webapp/src/lib/types.ts"])

    def test_undeclared_missing_path_still_fails_closed(self) -> None:
        inventory = {
            "inventory_version": "v1",
            "rules": [{"path": "frontend/app.js", "paths": None, "glob": None}],
        }
        with self.assertRaisesRegex(InventoryError, "inventory_exact_path_not_tracked"):
            reconcile_inventory_payload(
                inventory,
                tracked_paths=[],
                deleted_paths=frozenset(),
            )

    def test_manifest_collects_deleted_surfaces_and_retired_transport_sources(self) -> None:
        manifest = {
            "schema": "nexus.operator-console-consolidation.v1",
            "implementation_surfaces": [
                {
                    "id": "legacy_static_admin",
                    "disposition": "SUPERSEDED_DELETE",
                    "deleted": True,
                    "deleted_paths": ["frontend/app.js"],
                },
                {
                    "id": "canonical_workspace",
                    "disposition": "CANONICAL",
                    "path": "webapp/src/features/operator-workspace",
                },
            ],
            "transport_authority": {
                "target": "webapp/src/lib/apiClient.ts",
                "current_duplicates": [],
                "retired_sources": ["webapp/src/lib/api.ts"],
            },
        }
        self.assertEqual(
            canonical_deleted_paths(manifest),
            frozenset({"frontend/app.js", "webapp/src/lib/api.ts"}),
        )

    def test_deleted_true_with_non_delete_disposition_fails_closed(self) -> None:
        manifest = {
            "schema": "nexus.operator-console-consolidation.v1",
            "implementation_surfaces": [
                {"id": "bad", "disposition": "CANONICAL", "deleted": True, "paths": ["x.py"]},
            ],
            "transport_authority": {
                "target": "webapp/src/lib/apiClient.ts",
                "current_duplicates": [],
                "retired_sources": [],
            },
        }
        with self.assertRaisesRegex(InventoryError, "canonical_console_deleted_disposition_invalid"):
            canonical_deleted_paths(manifest)

    def test_transport_duplicates_or_retired_target_fail_closed(self) -> None:
        duplicate_manifest = {
            "schema": "nexus.operator-console-consolidation.v1",
            "implementation_surfaces": [],
            "transport_authority": {
                "target": "webapp/src/lib/apiClient.ts",
                "current_duplicates": ["webapp/src/lib/supportApi.ts"],
                "retired_sources": [],
            },
        }
        with self.assertRaisesRegex(InventoryError, "canonical_transport_duplicates_remain"):
            canonical_deleted_paths(duplicate_manifest)

        target_retired_manifest = {
            "schema": "nexus.operator-console-consolidation.v1",
            "implementation_surfaces": [],
            "transport_authority": {
                "target": "webapp/src/lib/apiClient.ts",
                "current_duplicates": [],
                "retired_sources": ["webapp/src/lib/apiClient.ts"],
            },
        }
        with self.assertRaisesRegex(InventoryError, "canonical_transport_target_retired"):
            canonical_deleted_paths(target_retired_manifest)


if __name__ == "__main__":
    unittest.main()
