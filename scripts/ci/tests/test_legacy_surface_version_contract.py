from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MODULE_PATH = ROOT / "scripts" / "ci" / "check_legacy_surface_registry.py"
REGISTRY_PATH = ROOT / "config" / "governance" / "legacy-surface-domains.v1.json"

spec = importlib.util.spec_from_file_location("legacy_surface_registry", MODULE_PATH)
assert spec and spec.loader
legacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(legacy)


class LegacySurfaceVersionContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.registry = legacy.load_registry(REGISTRY_PATH)

    def test_multi_digit_versioned_contracts_are_owned_recursively(self) -> None:
        paths = [
            "config/example.v10.json",
            "config/governance/example.v10.json",
            "webapp/design/example.v12.json",
            "webapp/design/runtime/example.v12.json",
            "backend/app/config/example.v123.json",
            "backend/app/config/runtime/example.v123.json",
            "backend/evals/example.v42.json",
            "backend/evals/runtime/example.v42.json",
        ]

        result = legacy.scan_registry(self.registry, paths, read_text=lambda _path: "")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["classification_complete"], result)
        self.assertEqual(result["unowned_count"], 0, result)
        self.assertEqual(result["overlap_count"], 0, result)
        self.assertEqual(result["owner_issue_match_counts"].get("650"), len(paths), result)

    def test_versioned_contract_outside_allowed_roots_fails_closed(self) -> None:
        path = "outside/example.v10.json"

        result = legacy.scan_registry(self.registry, [path], read_text=lambda _path: "")

        self.assertFalse(result["ok"], result)
        self.assertFalse(result["classification_complete"], result)
        self.assertEqual(result["unowned_count"], 1, result)
        self.assertEqual(result["matched_file_count"], 0, result)
        self.assertEqual(result["findings"][0]["path"], path)
        self.assertIn("legacy_surface_unowned", result["findings"][0]["reason_codes"])

    def test_domain_path_regex_requires_a_full_path_match(self) -> None:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        domain = next(item for item in raw["domains"] if item["id"] == "protected_versioned_contracts")
        domain["selectors"]["path_regexes"] = [r"example\.v[0-9]+\.json$"]
        registry = legacy.validate_registry(raw)

        result = legacy.scan_registry(registry, ["outside/example.v10.json"], read_text=lambda _path: "")

        self.assertFalse(result["ok"], result)
        self.assertEqual(result["unowned_count"], 1, result)
        self.assertEqual(result["matched_file_count"], 0, result)

    def test_non_numeric_version_suffixes_are_not_contract_markers(self) -> None:
        paths = [
            "config/governance/example.v12draft.json",
            "webapp/design/example.v.json",
            "backend/app/config/example.v-1.json",
        ]

        result = legacy.scan_registry(self.registry, paths, read_text=lambda _path: "")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["classification_complete"], result)
        self.assertEqual(result["matched_file_count"], 0, result)
        self.assertEqual(result["finding_count"], 0, result)

    def test_invalid_domain_path_regex_is_rejected(self) -> None:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        domain = next(item for item in raw["domains"] if item["id"] == "protected_versioned_contracts")
        domain["selectors"]["path_regexes"] = ["("]

        with self.assertRaisesRegex(legacy.RegistryValidationError, r"path_regexes\[0\]_invalid"):
            legacy.validate_registry(raw)


if __name__ == "__main__":
    unittest.main()
