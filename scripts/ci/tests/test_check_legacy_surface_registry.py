from __future__ import annotations

import copy
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "ci" / "check_legacy_surface_registry.py"
REGISTRY_PATH = ROOT / "config" / "governance" / "legacy-surface-domains.v2.json"

spec = importlib.util.spec_from_file_location("legacy_surface_registry", MODULE_PATH)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacySurfaceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = legacy.load_registry(REGISTRY_PATH)

    def raw_registry(self):
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_registry_is_current_state_only(self):
        self.assertEqual(self.registry["schema"], legacy.REGISTRY_SCHEMA)
        self.assertEqual(self.registry["registry_version"], "2026-07-19.1")
        self.assertEqual(self.registry["enforcement"], "fail_closed")
        self.assertNotIn("audited_main_sha", self.registry)
        self.assertNotIn(
            "legacy_static_frontend",
            {item["id"] for item in self.registry["domains"]},
        )
        self.assertNotIn(
            "historical_round_artifacts",
            {item["id"] for item in self.registry["domains"]},
        )

    def test_duplicate_domain_id_is_rejected(self):
        raw = self.raw_registry()
        raw["domains"].append(copy.deepcopy(raw["domains"][0]))
        with self.assertRaisesRegex(legacy.RegistryValidationError, "domain_id_duplicate"):
            legacy.validate_registry(raw)

    def test_safe_to_remove_never_authorizes_deletion(self):
        raw = self.raw_registry()
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

    def test_external_channel_alembic_overlap_is_explicit_and_protected(self):
        path = "backend/alembic/versions/20260410_0005_round8_external_channel_markets.py"
        result = legacy.scan_registry(self.registry, [path], read_text=lambda _: "")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["overlap_count"], 0)
        self.assertEqual(result["owner_issue_match_counts"]["532"], 1)
        self.assertEqual(result["owner_issue_match_counts"]["572"], 1)

    def test_unknown_external_channel_surface_fails_closed(self):
        raw = self.raw_registry()
        raw["domains"][0]["selectors"] = {
            "paths": [],
            "globs": [],
            "path_regexes": ["^known/external_channel.py$"],
            "content_rules": [],
        }
        registry = legacy.validate_registry(raw)
        result = legacy.scan_registry(
            registry,
            ["unknown/external_channel.py"],
            read_text=lambda _: "secret-looking-content-must-not-appear",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["unowned_count"], 1)
        self.assertNotIn("secret-looking-content", json.dumps(result))

    def test_source_sha_is_runtime_input_not_registry_state(self):
        result = legacy.scan_registry(
            self.registry,
            ["config/example.v10.json"],
            read_text=lambda _: "",
            source_sha="a" * 40,
        )
        self.assertEqual(result["source_sha"], "a" * 40)
        with self.assertRaisesRegex(legacy.RegistryValidationError, "source_sha_invalid"):
            legacy.scan_registry(
                self.registry,
                [],
                read_text=lambda _: "",
                source_sha="stale",
            )

    def test_bounded_reader_never_requests_more_than_limit_plus_one(self):
        sizes = []

        class RecordingBytesIO(io.BytesIO):
            def read(self, size=-1):
                sizes.append(size)
                return super().read(size)

        stream = RecordingBytesIO(b"x" * 128)
        with mock.patch.object(Path, "open", return_value=stream):
            result = legacy._read_text_bounded(Path("/repo"), "large.txt", max_bytes=16)
        self.assertIsNone(result)
        self.assertEqual(sizes, [17])

    def test_git_index_scan_excludes_symlinks_and_is_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "tracked.txt").write_text("ok", encoding="utf-8")
            (root / "link.txt").symlink_to("tracked.txt")
            subprocess.run(
                ["git", "-C", str(root), "add", "tracked.txt", "link.txt"],
                check=True,
            )
            self.assertEqual(legacy.collect_tracked_files(root), ["tracked.txt"])


if __name__ == "__main__":
    unittest.main()
