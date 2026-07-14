from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.ci.check_external_channel_retirement import (
    InventoryError,
    _reject_duplicate_keys,
    check_repository,
    list_tracked_files,
)

ALLOWED_DELETED_DISPOSITIONS = {"SUPERSEDED_DELETE"}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except InventoryError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError("canonical_reconciliation_json_invalid", path.as_posix()) from exc
    if not isinstance(value, dict):
        raise InventoryError("canonical_reconciliation_root_invalid", path.as_posix())
    return value


def _string_list(value: Any, reason: str, detail: object | None = None) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise InventoryError(reason, detail)
    return list(value)


def canonical_deleted_paths(manifest: Mapping[str, Any]) -> frozenset[str]:
    if manifest.get("schema") != "nexus.operator-console-consolidation.v1":
        raise InventoryError("canonical_console_manifest_invalid")
    surfaces = manifest.get("implementation_surfaces")
    if not isinstance(surfaces, list):
        raise InventoryError("canonical_console_surfaces_invalid")

    deleted: set[str] = set()
    for row in surfaces:
        if not isinstance(row, dict):
            raise InventoryError("canonical_console_surface_invalid")
        if row.get("deleted") is not True:
            continue
        if row.get("disposition") not in ALLOWED_DELETED_DISPOSITIONS:
            raise InventoryError("canonical_console_deleted_disposition_invalid", row.get("id"))
        for field in ("paths", "deleted_paths", "deleted_legacy_paths"):
            values = row.get(field, [])
            if values is None:
                continue
            deleted.update(_string_list(values, "canonical_console_deleted_paths_invalid", row.get("id")))

    transport = manifest.get("transport_authority")
    if not isinstance(transport, dict):
        raise InventoryError("canonical_transport_authority_invalid")
    target = transport.get("target")
    if not isinstance(target, str) or not target:
        raise InventoryError("canonical_transport_target_invalid")
    duplicates = _string_list(
        transport.get("current_duplicates"),
        "canonical_transport_duplicates_invalid",
    )
    if duplicates:
        raise InventoryError("canonical_transport_duplicates_remain", duplicates)
    retired_sources = _string_list(
        transport.get("retired_sources"),
        "canonical_transport_retired_sources_invalid",
    )
    if target in retired_sources:
        raise InventoryError("canonical_transport_target_retired", target)
    deleted.update(retired_sources)

    return frozenset(deleted)


def reconcile_inventory_payload(
    inventory: Mapping[str, Any],
    *,
    tracked_paths: Sequence[str],
    deleted_paths: frozenset[str],
) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = deepcopy(dict(inventory))
    rules = payload.get("rules")
    if not isinstance(rules, list):
        raise InventoryError("inventory_rules_invalid")
    tracked = set(tracked_paths)
    removed: list[str] = []
    reconciled_rules: list[dict[str, Any]] = []

    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            raise InventoryError("inventory_rule_invalid")
        rule = deepcopy(raw_rule)
        if rule.get("path") is not None:
            path = rule["path"]
            if not isinstance(path, str):
                raise InventoryError("inventory_rule_path_invalid")
            if path not in tracked:
                if path not in deleted_paths:
                    raise InventoryError("inventory_exact_path_not_tracked", path)
                removed.append(path)
                continue
        elif rule.get("paths") is not None:
            paths = rule["paths"]
            if not isinstance(paths, list):
                raise InventoryError("inventory_rule_paths_invalid")
            retained: list[str] = []
            for path in paths:
                if not isinstance(path, str):
                    raise InventoryError("inventory_rule_path_invalid")
                if path in tracked:
                    retained.append(path)
                elif path in deleted_paths:
                    removed.append(path)
                else:
                    raise InventoryError("inventory_exact_path_not_tracked", path)
            if not retained:
                continue
            rule["paths"] = retained
        reconciled_rules.append(rule)

    payload["rules"] = reconciled_rules
    payload["inventory_version"] = f"{payload.get('inventory_version', 'unknown')}.canonical"
    return payload, tuple(sorted(removed))


def check_reconciled_repository(repo_root: Path, inventory_path: Path, console_manifest_path: Path) -> dict[str, object]:
    inventory = _load_json(inventory_path)
    console_manifest = _load_json(console_manifest_path)
    tracked = list_tracked_files(repo_root)
    reconciled, removed = reconcile_inventory_payload(
        inventory,
        tracked_paths=tracked,
        deleted_paths=canonical_deleted_paths(console_manifest),
    )
    with tempfile.TemporaryDirectory(prefix="nexus-external-channel-") as tmp:
        path = Path(tmp) / "reconciled-inventory.json"
        path.write_text(json.dumps(reconciled, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        result = check_repository(repo_root, path)
    result["canonical_deleted_exact_rule_count"] = len(removed)
    result["canonical_deleted_exact_rule_fingerprints"] = [
        hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        for path in removed
    ]
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile ExternalChannel inventory with governed Canonical Console deletions.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--inventory", type=Path, default=Path("config/governance/external-channel-assets.v1.json"))
    parser.add_argument("--console-manifest", type=Path, default=Path("webapp/design/operator-console-consolidation.v1.json"))
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    manifest = args.console_manifest if args.console_manifest.is_absolute() else root / args.console_manifest
    try:
        result = check_reconciled_repository(root, inventory, manifest)
    except InventoryError as exc:
        result = {"ok": False, "reason": exc.reason, "detail": exc.detail}
        print(json.dumps(result, sort_keys=True, separators=(",", ":")))
        return 1
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
