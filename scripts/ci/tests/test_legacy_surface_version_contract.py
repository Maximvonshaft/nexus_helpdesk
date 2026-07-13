from __future__ import annotations

import importlib.util
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
    def test_multi_digit_versioned_contracts_are_owned(self) -> None:
        registry = legacy.load_registry(REGISTRY_PATH)
        paths = [
            "config/governance/example.v10.json",
            "webapp/design/example.v12.json",
            "backend/app/config/example.v123.json",
            "backend/evals/runtime/example.v42.json",
        ]

        result = legacy.scan_registry(registry, paths, read_text=lambda _path: "")

        self.assertTrue(result["ok"], result)
        self.assertTrue(result["classification_complete"], result)
        self.assertEqual(result["unowned_count"], 0, result)
        self.assertEqual(result["overlap_count"], 0, result)
        self.assertEqual(result["owner_issue_match_counts"].get("650"), len(paths), result)


if __name__ == "__main__":
    unittest.main()
