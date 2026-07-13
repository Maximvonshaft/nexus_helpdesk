from __future__ import annotations

import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = ROOT / "scripts" / "ci" / "check_external_channel_retirement.py"
MANIFEST_PATH = ROOT / "config" / "governance" / "external-channel-assets.v1.json"


def _load_checker():
    if not CHECKER_PATH.is_file():
        raise ImportError(f"checker module is missing: {CHECKER_PATH}")
    spec = importlib.util.spec_from_file_location(
        "check_external_channel_retirement",
        CHECKER_PATH,
    )
    if spec is None or spec.loader is None:
        raise ImportError("checker module could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _rule(
    *,
    path: str | None = None,
    paths: list[str] | None = None,
    glob: str | None = None,
    disposition: str = "safe_to_remove",
    write_surface: bool = False,
    stop_new_writes_required: bool = False,
) -> dict[str, object]:
    return {
        "path": path,
        "paths": paths,
        "glob": glob,
        "asset_type": "service" if path or paths else "documentation",
        "disposition": disposition,
        "owner": "m6-channel-gateway",
        "rationale": "Bounded retirement classification for a known repository asset.",
        "write_surface": write_surface,
        "stop_new_writes_required": stop_new_writes_required,
        "prerequisites": ["caller_migration", "observation_window"]
        if write_surface
        else [],
    }


def _payload(*rules: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "nexus.external-channel-retirement.inventory.v1",
        "inventory_version": "test.1",
        "audited_main_sha": "a" * 40,
        "discovery_tokens": [
            "ExternalChannel",
            "external_channel",
            "EXTERNAL_CHANNEL",
            "externalChannel",
            "external-channel",
        ],
        "production_roots": [
            ".github/workflows/",
            "backend/alembic/versions/",
            "backend/app/",
            "backend/scripts/",
            "deploy/",
            "frontend/",
            "scripts/",
            "webapp/src/",
        ],
        "allowed_historical_glob_roots": [
            "backend/tests/",
            "docs/",
            "webapp/e2e/",
        ],
        "rules": list(rules),
    }


class ExternalChannelRetirementInventoryTests(unittest.TestCase):
    def test_valid_inventory_covers_exact_production_and_historical_glob(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(
                    path="backend/app/legacy.py",
                    write_surface=True,
                    stop_new_writes_required=True,
                ),
                _rule(
                    glob="docs/*.md",
                    disposition="historical_evidence",
                ),
            )
        )
        result = checker.evaluate_inventory(
            inventory,
            tracked_paths=("backend/app/legacy.py", "docs/history.md"),
            token_paths=("backend/app/legacy.py", "docs/history.md"),
        )
        self.assertEqual(result.reference_file_count, 2)
        self.assertEqual(result.write_surface_count, 1)
        self.assertEqual(result.disposition_counts["safe_to_remove"], 1)
        self.assertEqual(result.disposition_counts["historical_evidence"], 1)

    def test_exact_path_group_expands_to_individual_production_assets(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(
                    paths=["backend/app/one.py", "backend/app/two.py"],
                    write_surface=True,
                    stop_new_writes_required=True,
                )
            )
        )
        result = checker.evaluate_inventory(
            inventory,
            tracked_paths=("backend/app/one.py", "backend/app/two.py"),
            token_paths=("backend/app/one.py", "backend/app/two.py"),
        )
        self.assertEqual(result.exact_rule_count, 2)
        self.assertEqual(result.write_surface_count, 2)

    def test_git_stage_parser_excludes_gitlinks_and_symlinks(self) -> None:
        raw = (
            b"100644 " + b"a" * 40 + b" 0\tbackend/app/legacy.py\0"
            b"100755 " + b"b" * 40 + b" 0\tscripts/check.sh\0"
            b"120000 " + b"c" * 40 + b" 0\tdocs/latest.md\0"
            b"160000 " + b"d" * 40 + b" 0\tvendor/chatwoot\0"
        )
        self.assertEqual(
            checker.parse_tracked_file_index(raw),
            ("backend/app/legacy.py", "scripts/check.sh"),
        )

    def test_git_stage_parser_rejects_malformed_records(self) -> None:
        with self.assertRaisesRegex(checker.InventoryError, "inventory_git_index_invalid"):
            checker.parse_tracked_file_index(b"not-a-stage-record\0")

    def test_discovery_covers_camel_and_hyphen_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "camel.ts").write_text("const externalChannel = 'retired';", encoding="utf-8")
            (root / "hyphen.md").write_text("The external-channel transport is retired.", encoding="utf-8")
            (root / "clean.txt").write_text("current runtime", encoding="utf-8")
            discovered = checker.discover_token_paths(
                root,
                ("camel.ts", "hyphen.md", "clean.txt"),
                checker.EXPECTED_DISCOVERY_TOKENS,
            )
        self.assertEqual(discovered, ("camel.ts", "hyphen.md"))

    def test_discovery_covers_marker_split_across_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "split.txt").write_bytes(b"abcExternalChannelxyz")
            discovered = checker.discover_token_paths(
                root,
                ("split.txt",),
                checker.EXPECTED_DISCOVERY_TOKENS,
                max_file_bytes=128,
                chunk_size=5,
            )
        self.assertEqual(discovered, ("split.txt",))

    def test_binary_nul_ignores_content_even_after_earlier_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "binary.bin").write_bytes(b"ExternalChannel-before-nul" + b"\x00" + b"tail")
            discovered = checker.discover_token_paths(
                root,
                ("binary.bin",),
                checker.EXPECTED_DISCOVERY_TOKENS,
                max_file_bytes=128,
                chunk_size=7,
            )
        self.assertEqual(discovered, ())

    def test_oversized_text_candidate_fails_closed_without_whole_file_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.txt").write_bytes(b"x" * 65)
            with self.assertRaisesRegex(
                checker.InventoryError,
                "inventory_tracked_file_oversized",
            ):
                checker.discover_token_paths(
                    root,
                    ("large.txt",),
                    checker.EXPECTED_DISCOVERY_TOKENS,
                    max_file_bytes=64,
                    chunk_size=16,
                )

    def test_invalid_scan_bounds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "clean.txt").write_text("clean", encoding="utf-8")
            with self.assertRaisesRegex(checker.InventoryError, "inventory_scan_bound_invalid"):
                checker.discover_token_paths(
                    root,
                    ("clean.txt",),
                    checker.EXPECTED_DISCOVERY_TOKENS,
                    max_file_bytes=0,
                )

    def test_discovery_covers_marker_in_path_without_scanning_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "backend" / "app"
            nested.mkdir(parents=True)
            marked_path = "backend/app/external_channel_stub.py"
            (root / marked_path).write_bytes(b"x" * 256)
            discovered = checker.discover_token_paths(
                root,
                (marked_path,),
                checker.EXPECTED_DISCOVERY_TOKENS,
                max_file_bytes=16,
                chunk_size=4,
            )
        self.assertEqual(discovered, (marked_path,))

    def test_uncovered_reference_fails_closed(self) -> None:
        inventory = checker.parse_inventory(_payload(_rule(path="backend/app/legacy.py")))
        with self.assertRaisesRegex(checker.InventoryError, "inventory_reference_uncovered"):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/legacy.py", "backend/app/new.py"),
                token_paths=("backend/app/legacy.py", "backend/app/new.py"),
            )

    def test_future_alembic_migration_requires_exact_classification(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(
                    path="backend/alembic/versions/existing_external_channel.py",
                    disposition="data_migration_dependency",
                )
            )
        )
        with self.assertRaisesRegex(checker.InventoryError, "inventory_reference_uncovered"):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=(
                    "backend/alembic/versions/existing_external_channel.py",
                    "backend/alembic/versions/future_external_channel.py",
                ),
                token_paths=(
                    "backend/alembic/versions/existing_external_channel.py",
                    "backend/alembic/versions/future_external_channel.py",
                ),
            )

    def test_alembic_glob_is_forbidden_as_production_selector(self) -> None:
        with self.assertRaisesRegex(checker.InventoryError, "inventory_production_glob_forbidden"):
            checker.parse_inventory(
                _payload(
                    _rule(
                        glob="backend/alembic/versions/*.py",
                        disposition="data_migration_dependency",
                    )
                )
            )

    def test_repository_manifest_uses_exact_alembic_rules_only(self) -> None:
        if not MANIFEST_PATH.is_file():
            self.skipTest("repository manifest is unavailable in isolated unit test")
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertIn("backend/alembic/versions/", raw["production_roots"])
        self.assertNotIn("backend/alembic/versions/", raw["allowed_historical_glob_roots"])
        migration_rules = [
            rule
            for rule in raw["rules"]
            if rule["asset_type"] == "migration"
        ]
        self.assertEqual(len(migration_rules), 1)
        self.assertIsNone(migration_rules[0]["glob"])
        self.assertTrue(migration_rules[0]["paths"])
        self.assertTrue(
            all(path.startswith("backend/alembic/versions/") for path in migration_rules[0]["paths"])
        )

    def test_overlapping_rules_fail_closed(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(path="docs/history.md", disposition="historical_evidence"),
                _rule(glob="docs/*.md", disposition="historical_evidence"),
            )
        )
        with self.assertRaisesRegex(checker.InventoryError, "inventory_reference_ambiguous"):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("docs/history.md",),
                token_paths=("docs/history.md",),
            )

    def test_production_glob_is_forbidden(self) -> None:
        with self.assertRaisesRegex(checker.InventoryError, "inventory_production_glob_forbidden"):
            checker.parse_inventory(_payload(_rule(glob="backend/app/*.py")))

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = _payload(_rule(path="backend/app/legacy.py"))
        payload["unexpected"] = True
        with self.assertRaisesRegex(checker.InventoryError, "inventory_fields_invalid"):
            checker.parse_inventory(payload)

    def test_unknown_rule_field_is_rejected(self) -> None:
        rule = _rule(path="backend/app/legacy.py")
        rule["unexpected"] = True
        with self.assertRaisesRegex(checker.InventoryError, "inventory_rule_fields_invalid"):
            checker.parse_inventory(_payload(rule))

    def test_write_surface_requires_exact_stop_new_writes_control(self) -> None:
        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_write_surface_control_invalid",
        ):
            checker.parse_inventory(
                _payload(
                    _rule(
                        path="backend/app/legacy.py",
                        write_surface=True,
                        stop_new_writes_required=False,
                    )
                )
            )
        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_write_surface_control_invalid",
        ):
            checker.parse_inventory(
                _payload(
                    _rule(
                        glob="docs/*.md",
                        write_surface=True,
                        stop_new_writes_required=True,
                    )
                )
            )

    def test_stale_exact_rule_is_rejected(self) -> None:
        inventory = checker.parse_inventory(_payload(_rule(path="backend/app/removed.py")))
        with self.assertRaisesRegex(checker.InventoryError, "inventory_exact_path_not_tracked"):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/current.py",),
                token_paths=("backend/app/current.py",),
            )

    def test_exact_rule_without_token_is_rejected(self) -> None:
        inventory = checker.parse_inventory(_payload(_rule(path="backend/app/legacy.py")))
        with self.assertRaisesRegex(checker.InventoryError, "inventory_exact_path_without_marker"):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/legacy.py",),
                token_paths=(),
            )

    def test_duplicate_rules_are_rejected(self) -> None:
        duplicate = _rule(path="backend/app/legacy.py")
        with self.assertRaisesRegex(checker.InventoryError, "inventory_rule_duplicate"):
            checker.parse_inventory(_payload(duplicate, copy.deepcopy(duplicate)))

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(
                '{"schema":"nexus.external-channel-retirement.inventory.v1",'
                '"schema":"duplicate"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(checker.InventoryError, "inventory_duplicate_json_key"):
                checker.load_inventory(path)

    def test_safe_summary_is_deterministic_and_contains_no_paths(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(path="backend/app/legacy.py"),
                _rule(glob="docs/*.md", disposition="historical_evidence"),
            )
        )
        evaluation = checker.evaluate_inventory(
            inventory,
            tracked_paths=("backend/app/legacy.py", "docs/history.md"),
            token_paths=("backend/app/legacy.py", "docs/history.md"),
        )
        first = checker.build_safe_summary(inventory, evaluation)
        second = checker.build_safe_summary(inventory, evaluation)
        rendered = json.dumps(first, sort_keys=True)
        self.assertEqual(first, second)
        self.assertTrue(first["ok"])
        self.assertEqual(len(first["inventory_sha256"]), 64)
        self.assertNotIn("backend/app/legacy.py", rendered)
        self.assertNotIn("docs/history.md", rendered)
        self.assertNotIn("ExternalChannel", rendered)


if __name__ == "__main__":
    unittest.main()
