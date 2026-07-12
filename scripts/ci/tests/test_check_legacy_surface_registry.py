from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "ci" / "check_legacy_surface_registry.py"
REGISTRY_PATH = ROOT / "config" / "governance" / "legacy-surface-domains.v1.json"

spec = importlib.util.spec_from_file_location("legacy_surface_registry", MODULE_PATH)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacySurfaceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = legacy.load_registry(REGISTRY_PATH)

    def test_registry_contract_is_strict_and_current(self):
        self.assertEqual(self.registry["schema"], legacy.REGISTRY_SCHEMA)
        self.assertEqual(self.registry["enforcement"], "fail_closed")
        self.assertEqual(
            set(self.registry["allowed_dispositions"]),
            legacy.ALLOWED_DISPOSITIONS,
        )
        self.assertTrue(all(domain["deletion_authorized"] is False for domain in self.registry["domains"]))

    def test_duplicate_domain_id_is_rejected(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        raw["domains"].append(copy.deepcopy(raw["domains"][0]))
        with self.assertRaisesRegex(legacy.RegistryValidationError, "domain_id_duplicate"):
            legacy.validate_registry(raw)

    def test_safe_to_remove_requires_prerequisites_and_never_authorizes_deletion(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        candidate = raw["domains"][0]
        candidate["disposition"] = "safe_to_remove"
        candidate["prerequisites"] = []
        with self.assertRaisesRegex(
            legacy.RegistryValidationError,
            "safe_to_remove_requires_prerequisites",
        ):
            legacy.validate_registry(raw)

        candidate["prerequisites"] = ["reference_proof"]
        candidate["deletion_authorized"] = True
        with self.assertRaisesRegex(
            legacy.RegistryValidationError,
            "deletion_authorized_must_be_false",
        ):
            legacy.validate_registry(raw)

    def test_protected_domain_cannot_be_removable(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        protected = next(item for item in raw["domains"] if item["id"] == "protected_alembic_history")
        protected["disposition"] = "safe_to_remove"
        with self.assertRaisesRegex(
            legacy.RegistryValidationError,
            "protected_domain_disposition_invalid",
        ):
            legacy.validate_registry(raw)

    def test_reachable_git_history_does_not_use_a_tracked_file_placeholder(self):
        self.assertNotIn(
            "protected_reachable_git_history",
            {item["id"] for item in self.registry["domains"]},
        )
        result = legacy.scan_registry(
            self.registry,
            [".gitignore"],
            read_text=lambda _path: "",
        )
        self.assertNotIn("protected_history", result["disposition_match_counts"])
        self.assertNotIn("565", result["owner_issue_match_counts"])

    def test_scan_classifies_protected_history_and_active_versioned_contracts(self):
        files = [
            "backend/alembic/versions/20260425_round_b_webchat.py",
            "backend/alembic/versions/20260601_0046_knowledge_runtime_v2.py",
            "webapp/design/frontend-product-foundation.v1.json",
            "backend/app/services/knowledge_runtime_v2/runtime.py",
        ]
        result = legacy.scan_registry(self.registry, files, read_text=lambda _path: "")
        self.assertTrue(result["ok"])
        self.assertTrue(result["classification_complete"])
        self.assertEqual(result["unowned_count"], 0)
        self.assertGreaterEqual(result["disposition_match_counts"]["protected_history"], 2)
        self.assertGreaterEqual(result["disposition_match_counts"]["active_authority"], 2)
        self.assertNotIn("historical_evidence", result["disposition_match_counts"])

    def test_scan_classifies_round_artifacts_and_release_identity_without_raw_content(self):
        contents = {
            "backend/app/main.py": "app = FastAPI(version='20.4.0-round-b')",
            "ROUND25_HARDENING_REPORT.md": "historical evidence",
            "deploy/docker-compose.server.yml": "legacy-worker:\n  profiles: [legacy-worker]\n",
        }
        result = legacy.scan_registry(
            self.registry,
            contents,
            read_text=lambda path: contents.get(path),
        )
        self.assertTrue(result["classification_complete"])
        encoded = json.dumps(result, sort_keys=True)
        self.assertNotIn("historical evidence", encoded)
        self.assertNotIn("profiles:", encoded)
        self.assertEqual(result["findings"], [])

    def test_unowned_discovery_fails_closed_with_bounded_path_only_evidence(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        raw["discovery_rules"].append(
            {
                "id": "synthetic_unowned_marker",
                "path_regex": "^runtime/obsolete/",
                "path_globs": [],
                "content_markers": [],
                "content_path_globs": [],
                "allowed_domain_ids": ["legacy_static_frontend"],
                "allow_multiple_domains": False,
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(
            registry,
            ["runtime/obsolete/handler.py"],
            read_text=lambda _path: "secret-looking-content-must-not-appear",
        )
        self.assertFalse(result["ok"])
        self.assertFalse(result["classification_complete"])
        self.assertEqual(result["unowned_count"], 1)
        self.assertEqual(result["reported_finding_count"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["path"], "runtime/obsolete/handler.py")
        self.assertRegex(finding["path_sha256"], r"^[0-9a-f]{16}$")
        self.assertNotIn("secret-looking-content", json.dumps(result))

    def test_finding_output_is_deterministic_and_bounded(self):
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        raw["finding_limit"] = 2
        raw["discovery_rules"].append(
            {
                "id": "synthetic_bounded_marker",
                "path_regex": "^orphan/",
                "path_globs": [],
                "content_markers": [],
                "content_path_globs": [],
                "allowed_domain_ids": ["legacy_static_frontend"],
                "allow_multiple_domains": False,
            }
        )
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(
            registry,
            ["orphan/c.py", "orphan/a.py", "orphan/b.py"],
            read_text=lambda _path: None,
        )
        self.assertEqual(result["finding_count"], 3)
        self.assertEqual(result["reported_finding_count"], 2)
        self.assertTrue(result["findings_truncated"])
        self.assertEqual(
            [item["path"] for item in result["findings"]],
            ["orphan/a.py", "orphan/b.py"],
        )

    def test_git_index_scan_excludes_symlinks_and_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "tracked.txt").write_text("ok", encoding="utf-8")
            (root / "link.txt").symlink_to("tracked.txt")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt", "link.txt"], check=True)
            self.assertEqual(legacy.collect_tracked_files(root), ["tracked.txt"])


if __name__ == "__main__":
    unittest.main()
