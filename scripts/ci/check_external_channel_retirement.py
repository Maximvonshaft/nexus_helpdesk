from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

SCHEMA = "nexus.external-channel-retirement.inventory.v1"
EXPECTED_DISCOVERY_TOKENS = (
    "ExternalChannel",
    "external_channel",
    "EXTERNAL_CHANNEL",
)
ALLOWED_DISPOSITIONS = frozenset(
    {
        "active_compatibility",
        "historical_evidence",
        "data_migration_dependency",
        "safe_to_remove",
        "retirement_control",
    }
)
TOP_LEVEL_FIELDS = frozenset(
    {
        "schema",
        "inventory_version",
        "audited_main_sha",
        "discovery_tokens",
        "production_roots",
        "allowed_historical_glob_roots",
        "rules",
    }
)
RULE_FIELDS = frozenset(
    {
        "path",
        "paths",
        "glob",
        "asset_type",
        "disposition",
        "owner",
        "rationale",
        "write_surface",
        "stop_new_writes_required",
        "prerequisites",
    }
)
IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REGULAR_GIT_MODES = frozenset({"100644", "100755"})
NON_FILE_GIT_MODES = frozenset({"120000", "160000"})
MAX_RULES = 500
MAX_LIST_ITEMS = 100
MAX_TEXT_LENGTH = 500


class InventoryError(ValueError):
    """Bounded fail-closed inventory validation error."""

    def __init__(self, reason: str, detail: str | None = None):
        super().__init__(reason if detail is None else f"{reason}: {detail}")
        self.reason = reason
        self.detail = _bounded_detail(detail)


@dataclass(frozen=True)
class InventoryRule:
    path: str | None
    glob: str | None
    asset_type: str
    disposition: str
    owner: str
    rationale: str
    write_surface: bool
    stop_new_writes_required: bool
    prerequisites: tuple[str, ...]

    @property
    def identity(self) -> tuple[str, str]:
        return ("path", self.path) if self.path is not None else ("glob", self.glob or "")

    def matches(self, path: str) -> bool:
        if self.path is not None:
            return path == self.path
        return fnmatch.fnmatchcase(path, self.glob or "")


@dataclass(frozen=True)
class Inventory:
    schema: str
    inventory_version: str
    audited_main_sha: str
    discovery_tokens: tuple[str, ...]
    production_roots: tuple[str, ...]
    allowed_historical_glob_roots: tuple[str, ...]
    rules: tuple[InventoryRule, ...]
    inventory_sha256: str


@dataclass(frozen=True)
class InventoryEvaluation:
    tracked_file_count: int
    reference_file_count: int
    exact_rule_count: int
    glob_rule_count: int
    write_surface_count: int
    disposition_counts: Mapping[str, int]


def _bounded_detail(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\n", " ").replace("\r", " ").strip()
    return text[:240] if text else None


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InventoryError("inventory_duplicate_json_key", key)
        result[key] = value
    return result


def load_inventory(path: Path) -> Inventory:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InventoryError("inventory_file_unavailable", path.as_posix()) from exc
    if not raw:
        raise InventoryError("inventory_file_empty")
    try:
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
    except InventoryError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InventoryError("inventory_json_invalid") from exc
    return parse_inventory(payload)


def parse_inventory(payload: object) -> Inventory:
    root = _mapping(payload, "inventory_root_invalid")
    _exact_keys(root, TOP_LEVEL_FIELDS, "inventory_fields_invalid")
    if root["schema"] != SCHEMA:
        raise InventoryError("inventory_schema_unsupported")

    inventory_version = _version(root["inventory_version"], "inventory_version_invalid")
    audited_main_sha = _text(root["audited_main_sha"], "inventory_audited_sha_invalid", 40)
    if not SHA_RE.fullmatch(audited_main_sha):
        raise InventoryError("inventory_audited_sha_invalid")

    discovery_tokens = _string_tuple(
        root["discovery_tokens"],
        "inventory_discovery_tokens_invalid",
        max_items=10,
        max_length=80,
    )
    if discovery_tokens != EXPECTED_DISCOVERY_TOKENS:
        raise InventoryError("inventory_discovery_tokens_invalid")

    production_roots = _prefix_tuple(root["production_roots"], "inventory_production_roots_invalid")
    historical_roots = _prefix_tuple(
        root["allowed_historical_glob_roots"],
        "inventory_historical_roots_invalid",
    )
    _validate_root_sets(production_roots, historical_roots)

    raw_rules = _sequence(root["rules"], "inventory_rules_invalid")
    if not raw_rules or len(raw_rules) > MAX_RULES:
        raise InventoryError("inventory_rule_count_invalid")

    expanded: list[InventoryRule] = []
    for raw_rule in raw_rules:
        expanded.extend(
            _parse_rules(
                raw_rule,
                production_roots=production_roots,
                historical_roots=historical_roots,
            )
        )
    if not expanded or len(expanded) > MAX_RULES:
        raise InventoryError("inventory_rule_count_invalid")

    identities: set[tuple[str, str]] = set()
    for rule in expanded:
        if rule.identity in identities:
            raise InventoryError("inventory_rule_duplicate", rule.identity[1])
        identities.add(rule.identity)

    canonical = json.dumps(
        root,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return Inventory(
        schema=SCHEMA,
        inventory_version=inventory_version,
        audited_main_sha=audited_main_sha,
        discovery_tokens=discovery_tokens,
        production_roots=production_roots,
        allowed_historical_glob_roots=historical_roots,
        rules=tuple(expanded),
        inventory_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _validate_root_sets(production_roots: Sequence[str], historical_roots: Sequence[str]) -> None:
    if set(production_roots) & set(historical_roots):
        raise InventoryError("inventory_root_overlap")
    for production in production_roots:
        if any(
            production.startswith(historical) or historical.startswith(production)
            for historical in historical_roots
        ):
            raise InventoryError("inventory_root_overlap", production)


def _parse_rules(
    payload: object,
    *,
    production_roots: Sequence[str],
    historical_roots: Sequence[str],
) -> tuple[InventoryRule, ...]:
    row = _mapping(payload, "inventory_rule_invalid")
    _exact_keys(row, RULE_FIELDS, "inventory_rule_fields_invalid")

    selectors = (row["path"], row["paths"], row["glob"])
    if sum(value is not None for value in selectors) != 1:
        raise InventoryError("inventory_rule_selector_invalid")

    exact_paths: tuple[str, ...] = ()
    glob: str | None = None
    if row["path"] is not None:
        exact_paths = (_relative_path(row["path"], "inventory_rule_path_invalid", allow_glob=False),)
    elif row["paths"] is not None:
        path_rows = _sequence(row["paths"], "inventory_rule_paths_invalid")
        if not path_rows or len(path_rows) > MAX_RULES:
            raise InventoryError("inventory_rule_paths_invalid")
        exact_paths = tuple(
            _relative_path(item, "inventory_rule_path_invalid", allow_glob=False)
            for item in path_rows
        )
        if len(exact_paths) != len(set(exact_paths)):
            raise InventoryError("inventory_rule_paths_invalid")
    else:
        glob = _relative_path(row["glob"], "inventory_rule_glob_invalid", allow_glob=True)
        _validate_historical_glob(glob, production_roots, historical_roots)

    asset_type = _identifier(row["asset_type"], "inventory_asset_type_invalid")
    disposition = _identifier(row["disposition"], "inventory_disposition_invalid")
    if disposition not in ALLOWED_DISPOSITIONS:
        raise InventoryError("inventory_disposition_invalid", disposition)
    owner = _identifier(row["owner"], "inventory_owner_invalid")
    rationale = _text(row["rationale"], "inventory_rationale_invalid", MAX_TEXT_LENGTH)
    if len(rationale) < 20:
        raise InventoryError("inventory_rationale_invalid")
    write_surface = _boolean(row["write_surface"], "inventory_write_surface_invalid")
    stop_new_writes_required = _boolean(
        row["stop_new_writes_required"],
        "inventory_stop_new_writes_invalid",
    )
    prerequisites = _identifier_tuple(
        row["prerequisites"],
        "inventory_prerequisites_invalid",
        allow_empty=True,
    )
    selector_detail = exact_paths[0] if exact_paths else glob
    if write_surface and (
        not exact_paths
        or not stop_new_writes_required
        or "caller_migration" not in prerequisites
    ):
        raise InventoryError("inventory_write_surface_control_invalid", selector_detail)
    if not write_surface and stop_new_writes_required:
        raise InventoryError("inventory_write_surface_control_invalid", selector_detail)

    common = {
        "asset_type": asset_type,
        "disposition": disposition,
        "owner": owner,
        "rationale": rationale,
        "write_surface": write_surface,
        "stop_new_writes_required": stop_new_writes_required,
        "prerequisites": prerequisites,
    }
    if glob is not None:
        return (InventoryRule(path=None, glob=glob, **common),)
    return tuple(InventoryRule(path=path, glob=None, **common) for path in exact_paths)


def _validate_historical_glob(
    pattern: str,
    production_roots: Sequence[str],
    historical_roots: Sequence[str],
) -> None:
    static_prefix = _glob_static_prefix(pattern)
    if not static_prefix:
        raise InventoryError("inventory_historical_glob_root_invalid", pattern)
    if any(
        static_prefix.startswith(root) or root.startswith(static_prefix)
        for root in production_roots
    ):
        raise InventoryError("inventory_production_glob_forbidden", pattern)
    if not any(static_prefix.startswith(root) for root in historical_roots):
        raise InventoryError("inventory_historical_glob_root_invalid", pattern)


def parse_tracked_file_index(raw: bytes) -> tuple[str, ...]:
    """Parse `git ls-files -z --stage`, retaining only regular tracked files."""
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InventoryError("inventory_git_path_encoding_invalid") from exc

    paths: list[str] = []
    for record in decoded.split("\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split("\t", 1)
            mode, object_id, stage = metadata.split(" ")
        except ValueError as exc:
            raise InventoryError("inventory_git_index_invalid") from exc
        if not SHA_RE.fullmatch(object_id) or stage != "0":
            raise InventoryError("inventory_git_index_invalid")
        if mode in NON_FILE_GIT_MODES:
            continue
        if mode not in REGULAR_GIT_MODES:
            raise InventoryError("inventory_git_mode_unsupported", mode)
        paths.append(
            _relative_path(raw_path, "inventory_tracked_path_invalid", allow_glob=False)
        )

    if len(paths) != len(set(paths)):
        raise InventoryError("inventory_tracked_path_duplicate")
    return tuple(sorted(paths))


def list_tracked_files(repo_root: Path) -> tuple[str, ...]:
    root = repo_root.resolve()
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--stage"],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InventoryError("inventory_git_ls_files_failed") from exc
    return parse_tracked_file_index(completed.stdout)


def discover_token_paths(
    repo_root: Path,
    tracked_paths: Sequence[str],
    tokens: Sequence[str],
) -> tuple[str, ...]:
    root = repo_root.resolve()
    token_bytes = tuple(token.encode("utf-8") for token in tokens)
    matches: list[str] = []
    for raw_path in tracked_paths:
        path = _relative_path(raw_path, "inventory_tracked_path_invalid", allow_glob=False)
        candidate = root / path
        try:
            if candidate.is_symlink() or not candidate.is_file():
                raise InventoryError("inventory_tracked_file_type_invalid", path)
            data = candidate.read_bytes()
        except InventoryError:
            raise
        except OSError as exc:
            raise InventoryError("inventory_tracked_file_unavailable", path) from exc
        if b"\x00" in data:
            continue
        if any(token in data for token in token_bytes):
            matches.append(path)
    return tuple(sorted(matches))


def evaluate_inventory(
    inventory: Inventory,
    tracked_paths: Sequence[str],
    token_paths: Sequence[str],
) -> InventoryEvaluation:
    tracked = _normalized_path_set(tracked_paths, "inventory_tracked_path_invalid")
    references = _normalized_path_set(token_paths, "inventory_token_path_invalid")
    if not references.issubset(tracked):
        raise InventoryError("inventory_token_path_not_tracked", sorted(references - tracked)[0])

    exact_rules = tuple(rule for rule in inventory.rules if rule.path is not None)
    glob_rules = tuple(rule for rule in inventory.rules if rule.glob is not None)
    for rule in exact_rules:
        assert rule.path is not None
        if rule.path not in tracked:
            raise InventoryError("inventory_exact_path_not_tracked", rule.path)
        if rule.path not in references:
            raise InventoryError("inventory_exact_path_without_marker", rule.path)

    matched_rules: list[InventoryRule] = []
    glob_match_counts: Counter[tuple[str, str]] = Counter()
    for path in sorted(references):
        matches = [rule for rule in inventory.rules if rule.matches(path)]
        if not matches:
            raise InventoryError("inventory_reference_uncovered", path)
        if len(matches) != 1:
            raise InventoryError("inventory_reference_ambiguous", path)
        matched = matches[0]
        matched_rules.append(matched)
        if matched.glob is not None:
            glob_match_counts[matched.identity] += 1

    for rule in glob_rules:
        if glob_match_counts[rule.identity] == 0:
            raise InventoryError("inventory_glob_without_marker", rule.glob)

    dispositions = Counter(rule.disposition for rule in matched_rules)
    return InventoryEvaluation(
        tracked_file_count=len(tracked),
        reference_file_count=len(references),
        exact_rule_count=len(exact_rules),
        glob_rule_count=len(glob_rules),
        write_surface_count=sum(1 for rule in exact_rules if rule.write_surface),
        disposition_counts=dict(sorted(dispositions.items())),
    )


def build_safe_summary(
    inventory: Inventory,
    evaluation: InventoryEvaluation,
) -> dict[str, object]:
    return {
        "ok": True,
        "schema": inventory.schema,
        "inventory_version": inventory.inventory_version,
        "audited_main_sha": inventory.audited_main_sha,
        "tracked_file_count": evaluation.tracked_file_count,
        "reference_file_count": evaluation.reference_file_count,
        "exact_rule_count": evaluation.exact_rule_count,
        "glob_rule_count": evaluation.glob_rule_count,
        "write_surface_count": evaluation.write_surface_count,
        "disposition_counts": dict(evaluation.disposition_counts),
        "inventory_sha256": inventory.inventory_sha256,
    }


def check_repository(repo_root: Path, manifest_path: Path) -> dict[str, object]:
    inventory = load_inventory(manifest_path)
    tracked = list_tracked_files(repo_root)
    references = discover_token_paths(repo_root, tracked, inventory.discovery_tokens)
    evaluation = evaluate_inventory(inventory, tracked, references)
    return build_safe_summary(inventory, evaluation)


def _mapping(value: object, reason: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InventoryError(reason)
    return value


def _sequence(value: object, reason: str) -> list[Any]:
    if not isinstance(value, list):
        raise InventoryError(reason)
    return value


def _exact_keys(mapping: Mapping[str, Any], expected: frozenset[str], reason: str) -> None:
    if set(mapping) != expected:
        raise InventoryError(reason)


def _text(value: object, reason: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise InventoryError(reason)
    text = value.strip()
    if not text or len(text) > max_length or any(ch in text for ch in "\r\n\x00"):
        raise InventoryError(reason)
    return text


def _version(value: object, reason: str) -> str:
    text = _text(value, reason, 128)
    if not VERSION_RE.fullmatch(text):
        raise InventoryError(reason)
    return text


def _identifier(value: object, reason: str) -> str:
    text = _text(value, reason, 128).lower()
    if not IDENTIFIER_RE.fullmatch(text):
        raise InventoryError(reason)
    return text


def _boolean(value: object, reason: str) -> bool:
    if not isinstance(value, bool):
        raise InventoryError(reason)
    return value


def _string_tuple(
    value: object,
    reason: str,
    *,
    max_items: int,
    max_length: int,
) -> tuple[str, ...]:
    rows = _sequence(value, reason)
    if not rows or len(rows) > max_items:
        raise InventoryError(reason)
    result = tuple(_text(item, reason, max_length) for item in rows)
    if len(result) != len(set(result)):
        raise InventoryError(reason)
    return result


def _identifier_tuple(
    value: object,
    reason: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    rows = _sequence(value, reason)
    if (not rows and not allow_empty) or len(rows) > MAX_LIST_ITEMS:
        raise InventoryError(reason)
    result = tuple(_identifier(item, reason) for item in rows)
    if len(result) != len(set(result)):
        raise InventoryError(reason)
    return result


def _prefix_tuple(value: object, reason: str) -> tuple[str, ...]:
    rows = _sequence(value, reason)
    if not rows or len(rows) > MAX_LIST_ITEMS:
        raise InventoryError(reason)
    result: list[str] = []
    for item in rows:
        raw = _text(item, reason, 300)
        if not raw.endswith("/"):
            raise InventoryError(reason, raw)
        result.append(_relative_path(raw[:-1], reason, allow_glob=False) + "/")
    if len(result) != len(set(result)):
        raise InventoryError(reason)
    return tuple(result)


def _relative_path(value: object, reason: str, *, allow_glob: bool) -> str:
    text = _text(value, reason, 300)
    if "\\" in text or text.startswith("/"):
        raise InventoryError(reason)
    if not allow_glob and any(char in text for char in "*?[]"):
        raise InventoryError(reason)
    if allow_glob and not any(char in text for char in "*?["):
        raise InventoryError(reason)
    path = PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InventoryError(reason)
    normalized = path.as_posix()
    if normalized != text:
        raise InventoryError(reason)
    return normalized


def _glob_static_prefix(pattern: str) -> str:
    positions = [pattern.find(marker) for marker in ("*", "?", "[")]
    wildcard_positions = [position for position in positions if position >= 0]
    if not wildcard_positions:
        return ""
    static = pattern[: min(wildcard_positions)]
    if "/" not in static:
        return ""
    return static[: static.rfind("/") + 1]


def _normalized_path_set(values: Sequence[str], reason: str) -> set[str]:
    normalized = [_relative_path(value, reason, allow_glob=False) for value in values]
    if len(normalized) != len(set(normalized)):
        raise InventoryError(f"{reason}_duplicate")
    return set(normalized)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the retired ExternalChannel repository inventory."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("config/governance/external-channel-assets.v1.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    repo_root = args.repo_root.resolve()
    manifest_path = args.manifest
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    try:
        summary = check_repository(repo_root, manifest_path)
    except InventoryError as exc:
        print(
            json.dumps(
                {"ok": False, "reason": exc.reason, "detail": exc.detail},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
