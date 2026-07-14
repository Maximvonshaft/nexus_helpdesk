from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

BASE_SCHEMA = "nexus.external-channel-retirement.inventory.v1"
REMOVED_SCHEMA = "nexus.external-channel-retirement.removed-assets.v1"
RETIREMENT_CONTROL_PATHS = (
    "config/governance/external-channel-removed-assets.v1.json",
    "scripts/ci/render_external_channel_active_inventory.py",
)


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON object: {path}")
    return payload


def _normalize_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("invalid removed path")
    path = PurePosixPath(value)
    if any(part in {"", ".", ".."} for part in path.parts) or path.as_posix() != value:
        raise ValueError("invalid removed path")
    return value


def _normalize_glob(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise ValueError("invalid retired glob")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("invalid retired glob")
    return value


def _tracked_paths(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return {value for value in result.stdout.decode("utf-8").split("\0") if value}


def _exact_rule_paths(rules: list[dict[str, Any]]) -> set[str]:
    paths: set[str] = set()
    for rule in rules:
        if rule.get("path") is not None:
            paths.add(_normalize_path(rule["path"]))
        elif rule.get("paths") is not None:
            values = rule["paths"]
            if not isinstance(values, list):
                raise ValueError("inventory paths selector is invalid")
            paths.update(_normalize_path(value) for value in values)
    return paths


def render_active_inventory(
    repo_root: Path,
    base_manifest: Path,
    removed_manifest: Path,
    output_path: Path,
) -> dict[str, Any]:
    base = _load(base_manifest)
    removed = _load(removed_manifest)
    if base.get("schema") != BASE_SCHEMA:
        raise ValueError("unsupported base inventory schema")
    if removed.get("schema") != REMOVED_SCHEMA:
        raise ValueError("unsupported removed-assets schema")

    raw_removed = removed.get("removed_paths")
    if not isinstance(raw_removed, list) or not raw_removed:
        raise ValueError("removed_paths must be a non-empty list")
    removed_paths = tuple(_normalize_path(value) for value in raw_removed)
    if len(removed_paths) != len(set(removed_paths)):
        raise ValueError("removed_paths contains duplicates")

    raw_retired_globs = removed.get("retired_globs", [])
    if not isinstance(raw_retired_globs, list):
        raise ValueError("retired_globs must be a list")
    retired_globs = tuple(_normalize_glob(value) for value in raw_retired_globs)
    if len(retired_globs) != len(set(retired_globs)):
        raise ValueError("retired_globs contains duplicates")

    tracked = _tracked_paths(repo_root)
    reintroduced = sorted(set(removed_paths) & tracked)
    if reintroduced:
        raise ValueError(f"removed asset was reintroduced: {reintroduced[0]}")

    rules = base.get("rules")
    if not isinstance(rules, list):
        raise ValueError("base inventory rules are invalid")

    active_rules: list[dict[str, Any]] = []
    referenced_removed: set[str] = set()
    referenced_retired_globs: set[str] = set()
    for raw_rule in rules:
        if not isinstance(raw_rule, dict):
            raise ValueError("base inventory rule is invalid")
        rule = dict(raw_rule)
        if rule.get("glob") is not None:
            glob = _normalize_glob(rule["glob"])
            if glob in retired_globs:
                referenced_retired_globs.add(glob)
                continue
        if rule.get("path") is not None:
            path = _normalize_path(rule["path"])
            if path in removed_paths:
                referenced_removed.add(path)
                continue
        elif rule.get("paths") is not None:
            paths = rule["paths"]
            if not isinstance(paths, list):
                raise ValueError("base inventory paths selector is invalid")
            retained = []
            for value in paths:
                path = _normalize_path(value)
                if path in removed_paths:
                    referenced_removed.add(path)
                else:
                    retained.append(path)
            if not retained:
                continue
            rule["paths"] = retained
        active_rules.append(rule)

    missing_from_base = sorted(set(removed_paths) - referenced_removed)
    if missing_from_base:
        raise ValueError(f"removed asset is not present in base inventory: {missing_from_base[0]}")
    missing_globs_from_base = sorted(set(retired_globs) - referenced_retired_globs)
    if missing_globs_from_base:
        raise ValueError(f"retired glob is not present in base inventory: {missing_globs_from_base[0]}")

    missing_controls = sorted(set(RETIREMENT_CONTROL_PATHS) - _exact_rule_paths(active_rules))
    missing_tracked_controls = sorted(set(missing_controls) - tracked)
    if missing_tracked_controls:
        raise ValueError(f"retirement control is not tracked: {missing_tracked_controls[0]}")
    if missing_controls:
        active_rules.append({
            "path": None,
            "paths": missing_controls,
            "glob": None,
            "asset_type": "retirement_control",
            "disposition": "retirement_control",
            "owner": "release-governance",
            "rationale": "Removal overlays and active-inventory rendering preserve audit history while preventing retired asset reintroduction.",
            "write_surface": False,
            "stop_new_writes_required": False,
            "prerequisites": [],
        })

    active = dict(base)
    active["inventory_version"] = f"{base['inventory_version']}.removed-{removed['version']}"
    active["rules"] = active_rules
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(active, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "ok": True,
        "removed_path_count": len(removed_paths),
        "retired_glob_count": len(retired_globs),
        "retirement_control_count": len(missing_controls),
        "active_rule_count": len(active_rules),
        "output": output_path.as_posix(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render the current ExternalChannel inventory after verified asset removals.")
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base", type=Path, default=Path("config/governance/external-channel-assets.v1.json"))
    parser.add_argument("--removed", type=Path, default=Path("config/governance/external-channel-removed-assets.v1.json"))
    parser.add_argument("--output", type=Path, default=Path(".tmp/external-channel-active-assets.v1.json"))
    return parser


def main() -> int:
    args = _parser().parse_args()
    repo_root = args.repo_root.resolve()
    resolve_from_root = lambda path: path if path.is_absolute() else repo_root / path
    try:
        summary = render_active_inventory(
            repo_root,
            resolve_from_root(args.base),
            resolve_from_root(args.removed),
            resolve_from_root(args.output),
        )
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
