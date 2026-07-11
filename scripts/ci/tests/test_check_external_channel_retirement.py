from __future__ import annotations

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
CHECKER_PATH = ROOT / "scripts" / "ci" / "check_external_channel_retirement.py"


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
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _rule(
    *,
    path: str | None = None,
    glob: str | None = None,
    disposition: str = "safe_to_remove",
    write_surface: bool = False,
    stop_new_writes_required: bool = False,
) -> dict[str, object]:
    return {
        "path": path,
        "glob": glob,
        "asset_type": "service" if path else "documentation",
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
        ],
        "production_roots": [
            ".github/workflows/",
            "backend/app/",
            "backend/scripts/",
            "deploy/",
            "frontend/",
            "scripts/",
            "webapp/src/",
        ],
        "allowed_historical_glob_roots": [
            "backend/alembic/versions/",
            "backend/tests/",
            "docs/",
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

    def test_uncovered_reference_fails_closed(self) -> None:
        inventory = checker.parse_inventory(
            _payload(_rule(path="backend/app/legacy.py"))
        )

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_reference_uncovered",
        ):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/legacy.py", "backend/app/new.py"),
                token_paths=("backend/app/legacy.py", "backend/app/new.py"),
            )

    def test_overlapping_rules_fail_closed(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(
                    path="docs/history.md",
                    disposition="historical_evidence",
                ),
                _rule(
                    glob="docs/*.md",
                    disposition="historical_evidence",
                ),
            )
        )

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_reference_ambiguous",
        ):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("docs/history.md",),
                token_paths=("docs/history.md",),
            )

    def test_production_glob_is_forbidden(self) -> None:
        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_production_glob_forbidden",
        ):
            checker.parse_inventory(
                _payload(_rule(glob="backend/app/*.py"))
            )

    def test_unknown_top_level_field_is_rejected(self) -> None:
        payload = _payload(_rule(path="backend/app/legacy.py"))
        payload["unexpected"] = True

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_fields_invalid",
        ):
            checker.parse_inventory(payload)

    def test_unknown_rule_field_is_rejected(self) -> None:
        rule = _rule(path="backend/app/legacy.py")
        rule["unexpected"] = True

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_rule_fields_invalid",
        ):
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
        inventory = checker.parse_inventory(
            _payload(_rule(path="backend/app/removed.py"))
        )

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_exact_path_not_tracked",
        ):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/current.py",),
                token_paths=("backend/app/current.py",),
            )

    def test_exact_rule_without_token_is_rejected(self) -> None:
        inventory = checker.parse_inventory(
            _payload(_rule(path="backend/app/legacy.py"))
        )

        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_exact_path_without_marker",
        ):
            checker.evaluate_inventory(
                inventory,
                tracked_paths=("backend/app/legacy.py",),
                token_paths=(),
            )

    def test_duplicate_rules_are_rejected(self) -> None:
        duplicate = _rule(path="backend/app/legacy.py")
        with self.assertRaisesRegex(
            checker.InventoryError,
            "inventory_rule_duplicate",
        ):
            checker.parse_inventory(
                _payload(duplicate, copy.deepcopy(duplicate))
            )

    def test_duplicate_json_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inventory.json"
            path.write_text(
                '{"schema":"nexus.external-channel-retirement.inventory.v1",'
                '"schema":"duplicate"}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                checker.InventoryError,
                "inventory_duplicate_json_key",
            ):
                checker.load_inventory(path)

    def test_safe_summary_is_deterministic_and_contains_no_paths(self) -> None:
        inventory = checker.parse_inventory(
            _payload(
                _rule(path="backend/app/legacy.py"),
                _rule(
                    glob="docs/*.md",
                    disposition="historical_evidence",
                ),
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
