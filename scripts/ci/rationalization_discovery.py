#!/usr/bin/env python3
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Sequence

import yaml
from yaml.resolver import BaseResolver
from yaml.tokens import AliasToken, AnchorToken, TagToken

from scripts.ci.check_legacy_surface_registry import (
    _domain_matches,
    _read_text_bounded,
    collect_tracked_files,
    load_registry,
)

RESULT_SCHEMA = "nexus.osr.rationalization-discovery.v1"
LEDGER_SCHEMA = "nexus.osr.codebase-rationalization-inventory.v1"
MAX_FINDINGS = 200
MAX_TEXT_BYTES = 1_000_000
MAX_LEDGER_BYTES = 512_000
TEXT_EXTENSIONS = {
    ".css",
    ".html",
    ".ini",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
ROOT_DOCUMENT_ALLOWLIST = {
    "CHANGELOG.md",
    "CODE_OF_CONDUCT.md",
    "CONTRIBUTING.md",
    "LICENSE.md",
    "README.md",
    "SECURITY.md",
}
ROOT_EXECUTABLE_EXTENSIONS = {".bat", ".cmd", ".js", ".mjs", ".ps1", ".py", ".sh", ".ts"}
SUSPICIOUS_NAME_TOKEN = re.compile(
    r"(?:^|[._-])(backup|bak|copy|old|obsolete|deprecated|tmp|temp|scratch|unused|duplicate|round[0-9a-z_-]*)(?:[._-]|$)",
    re.IGNORECASE,
)
IMPORT_SPEC_RE = re.compile(
    r"(?:^|[;\n])\s*(?:import|export)\s+(?:type\s+)?(?:[^\"']*?\s+from\s+)?[\"']([^\"']+)[\"']",
    re.MULTILINE,
)
DYNAMIC_IMPORT_RE = re.compile(r"(?:import|require)\(\s*[\"']([^\"']+)[\"']\s*\)")
IMPORT_MODULE_RE = re.compile(r"import_module\(\s*[\"'](app(?:\.[A-Za-z_][A-Za-z0-9_]*)+)[\"']\s*\)")

SELF_PATHS = {
    "scripts/ci/rationalization_discovery.py",
    "scripts/ci/tests/test_agent_coordination_rationalization_discovery.py",
    "docs/engineering/codebase-rationalization-discovery.md",
}

ALLOWED_CLASSIFICATION_DISPOSITIONS = {
    "CANONICAL",
    "DUPLICATE_DELETE",
    "DEAD_DELETE",
    "SUPERSEDED_DELETE",
    "LEGACY_ACTIVE_MIGRATE_THEN_DELETE",
    "COMPATIBILITY_WITH_DEADLINE",
    "GENERATED_OR_VENDOR_MANAGED",
    "UNKNOWN_BLOCK_DELETE",
}
DELETE_DISPOSITIONS = {"DUPLICATE_DELETE", "DEAD_DELETE", "SUPERSEDED_DELETE"}


class DiscoveryError(ValueError):
    """Bounded fail-closed rationalization discovery error."""


class _StrictSafeLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate keys and merge indirection."""


def _construct_strict_mapping(
    loader: _StrictSafeLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            raise DiscoveryError("ledger_yaml_merge_forbidden")
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise DiscoveryError("ledger_mapping_key_invalid") from exc
        if duplicate:
            raise DiscoveryError("ledger_duplicate_key")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictSafeLoader.add_constructor(BaseResolver.DEFAULT_MAPPING_TAG, _construct_strict_mapping)


class Finding(dict[str, Any]):
    pass


def _stable_finding_id(signal: str, paths: Sequence[str], *, content_hash: str | None = None) -> str:
    if content_hash:
        return f"{signal}:{content_hash[:16]}"
    if len(paths) != 1:
        digest = hashlib.sha256("\0".join(sorted(paths)).encode("utf-8")).hexdigest()[:16]
        return f"{signal}:{digest}"
    return f"{signal}:{paths[0]}"


def _path_fingerprint(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def _finding(signal: str, paths: Sequence[str], *, content_hash: str | None = None) -> Finding:
    normalized = sorted(set(paths))
    if not normalized:
        raise DiscoveryError("finding_paths_empty")
    return Finding(
        finding_id=_stable_finding_id(signal, normalized, content_hash=content_hash),
        signal=signal,
        paths=normalized,
        path_fingerprints=[_path_fingerprint(path) for path in normalized],
    )


def _root_findings(tracked: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(set(tracked)):
        pure = PurePosixPath(path)
        if len(pure.parts) != 1 or path in SELF_PATHS:
            continue
        suffix = pure.suffix.casefold()
        if suffix in {".md", ".txt", ".log", ".patch", ".diff"} and pure.name not in ROOT_DOCUMENT_ALLOWLIST:
            findings.append(_finding("root_document", [path]))
        if suffix in ROOT_EXECUTABLE_EXTENSIONS:
            findings.append(_finding("root_executable", [path]))
    return findings


def _suspicious_name_findings(tracked: Iterable[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(set(tracked)):
        if path in SELF_PATHS:
            continue
        pure = PurePosixPath(path)
        if any(SUSPICIOUS_NAME_TOKEN.search(part) for part in pure.parts):
            findings.append(_finding("suspicious_name", [path]))
    return findings


def _read_bytes_bounded(repo_root: Path, path: str, max_bytes: int = MAX_TEXT_BYTES) -> bytes | None:
    try:
        with (repo_root / path).open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes or b"\0" in data:
        return None
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return data


def _duplicate_findings(repo_root: Path, tracked: Iterable[str]) -> list[Finding]:
    groups: dict[str, list[str]] = defaultdict(list)
    for path in sorted(set(tracked)):
        pure = PurePosixPath(path)
        if path in SELF_PATHS or pure.suffix.casefold() not in TEXT_EXTENSIONS:
            continue
        if path.startswith("vendor/") or pure.name in {"package-lock.json", "pnpm-lock.yaml", "yarn.lock"}:
            continue
        data = _read_bytes_bounded(repo_root, path)
        if data is None or len(data.strip()) < 40:
            continue
        digest = hashlib.sha256(data).hexdigest()
        groups[digest].append(path)
    return [
        _finding("exact_duplicate_text", paths, content_hash=digest)
        for digest, paths in sorted(groups.items())
        if len(paths) > 1
    ]


def _read_source(repo_root: Path, path: str) -> str:
    data = _read_bytes_bounded(repo_root, path)
    if data is None:
        raise DiscoveryError(f"source_unavailable:{path}")
    return data.decode("utf-8")


def _resolve_frontend_spec(current_path: str, spec: str, candidates: set[str]) -> str | None:
    if spec.startswith("@/"):
        base = PurePosixPath("webapp/src") / spec[2:]
    elif spec.startswith("src/"):
        base = PurePosixPath("webapp") / spec
    elif spec.startswith("."):
        base = PurePosixPath(current_path).parent / spec
    else:
        return None
    normalized = PurePosixPath(*[part for part in base.parts if part not in {"."}])
    stack: list[str] = []
    for part in normalized.parts:
        if part == "..":
            if not stack:
                return None
            stack.pop()
        else:
            stack.append(part)
    raw = "/".join(stack)
    variants = [raw]
    if PurePosixPath(raw).suffix == "":
        variants.extend(f"{raw}{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx"))
        variants.extend(f"{raw}/index{suffix}" for suffix in (".ts", ".tsx", ".js", ".jsx"))
    for variant in variants:
        if variant in candidates:
            return variant
    return None


def _frontend_findings(repo_root: Path, tracked: Iterable[str]) -> list[Finding]:
    candidates = {
        path
        for path in tracked
        if path.startswith("webapp/src/")
        and PurePosixPath(path).suffix.casefold() in {".ts", ".tsx", ".js", ".jsx"}
        and not re.search(r"(?:^|/)(?:__tests__|test|tests)(?:/|$)", path)
        and not re.search(r"\.(?:test|spec|stories)\.[^.]+$", path)
        and not path.endswith(".d.ts")
    }
    if not candidates:
        return []
    graph: dict[str, set[str]] = {path: set() for path in candidates}
    for path in sorted(candidates):
        text = _read_source(repo_root, path)
        specs = IMPORT_SPEC_RE.findall(text) + DYNAMIC_IMPORT_RE.findall(text)
        for spec in specs:
            target = _resolve_frontend_spec(path, spec, candidates)
            if target is not None:
                graph[path].add(target)
    roots = {
        path
        for path in candidates
        if path in {"webapp/src/main.ts", "webapp/src/main.tsx", "webapp/src/index.ts", "webapp/src/index.tsx"}
    }
    reachable: set[str] = set()
    queue = deque(sorted(roots))
    while queue:
        path = queue.popleft()
        if path in reachable:
            continue
        reachable.add(path)
        queue.extend(sorted(graph.get(path, set()) - reachable))
    unreachable = sorted(
        path
        for path in candidates - reachable
        if PurePosixPath(path).name not in {"vite-env.d.ts"}
    )
    return [_finding("unreachable_webapp_module", [path]) for path in unreachable]


def _python_module_for_path(path: str) -> str | None:
    pure = PurePosixPath(path)
    if not path.startswith("backend/app/") or pure.suffix != ".py":
        return None
    rel = pure.relative_to("backend")
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _python_imports(
    text: str,
    current_module: str | None,
    module_names: set[str],
    *,
    current_is_package: bool = False,
) -> set[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise DiscoveryError("python_source_invalid") from exc
    result: set[str] = set()
    current_parts = current_module.split(".") if current_module else []
    current_package = current_parts if current_is_package else current_parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in module_names:
                    result.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                if not current_package or node.level > len(current_package) + 1:
                    continue
                base_parts = current_package[: len(current_package) - node.level + 1]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            else:
                base = node.module or ""
            if base in module_names:
                result.add(base)
            for alias in node.names:
                child = f"{base}.{alias.name}" if base else alias.name
                if child in module_names:
                    result.add(child)
    for match in IMPORT_MODULE_RE.findall(text):
        if match in module_names:
            result.add(match)
    return result


def _backend_findings(repo_root: Path, tracked: Iterable[str]) -> list[Finding]:
    module_to_path = {
        module: path
        for path in tracked
        if (module := _python_module_for_path(path)) is not None
    }
    if not module_to_path:
        return []
    module_names = set(module_to_path)
    graph: dict[str, set[str]] = {}
    for module, path in module_to_path.items():
        graph[module] = _python_imports(
            _read_source(repo_root, path),
            module,
            module_names,
            current_is_package=path.endswith("/__init__.py"),
         )

    roots: set[str] = {module for module in ("app.main",) if module in module_names}
    external_entry_paths = [
        path
        for path in tracked
        if path == "backend/alembic/env.py"
        or (path.startswith("backend/scripts/") and path.endswith(".py"))
        or (path.startswith("backend/evals/") and path.endswith(".py"))
    ]
    for path in external_entry_paths:
        roots.update(_python_imports(_read_source(repo_root, path), None, module_names))

    reachable: set[str] = set()
    queue = deque(sorted(roots))
    while queue:
        module = queue.popleft()
        if module in reachable:
            continue
        reachable.add(module)
        queue.extend(sorted(graph.get(module, set()) - reachable))

    unreachable = sorted(
        path
        for module, path in module_to_path.items()
        if module not in reachable and not path.endswith("/__init__.py")
    )
    return [_finding("unreachable_backend_module", [path]) for path in unreachable]


def _load_ledger_document(path: Path) -> Any:
    try:
        with path.open("rb") as handle:
            data = handle.read(MAX_LEDGER_BYTES + 1)
    except OSError as exc:
        raise DiscoveryError("ledger_read_or_yaml_error") from exc
    if len(data) > MAX_LEDGER_BYTES or b"\0" in data:
        raise DiscoveryError("ledger_size_or_binary_invalid")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DiscoveryError("ledger_read_or_yaml_error") from exc
    try:
        for token in yaml.scan(text):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise DiscoveryError("ledger_yaml_indirection_forbidden")
        return yaml.load(text, Loader=_StrictSafeLoader)
    except DiscoveryError:
        raise
    except yaml.YAMLError as exc:
        raise DiscoveryError("ledger_read_or_yaml_error") from exc


def _load_ledger_classifications(path: Path) -> dict[str, dict[str, Any]]:
    raw = _load_ledger_document(path)
    if not isinstance(raw, dict) or raw.get("schema") != LEDGER_SCHEMA:
        raise DiscoveryError("ledger_schema_invalid")
    gate = raw.get("discovery_gate")
    if gate is None:
        return {}
    if not isinstance(gate, dict) or gate.get("contract") != RESULT_SCHEMA:
        raise DiscoveryError("discovery_gate_contract_invalid")
    rows = gate.get("classifications")
    if not isinstance(rows, list):
        raise DiscoveryError("discovery_gate_classifications_invalid")
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise DiscoveryError("discovery_classification_invalid")
        required = {"finding_id", "disposition", "owner_issue", "rationale", "next_action"}
        if set(row) != required:
            raise DiscoveryError("discovery_classification_fields_invalid")
        finding_id = row["finding_id"]
        disposition = row["disposition"]
        owner_issue = row["owner_issue"]
        rationale = row["rationale"]
        next_action = row["next_action"]
        if not isinstance(finding_id, str) or not finding_id:
            raise DiscoveryError("discovery_finding_id_invalid")
        if finding_id in result:
            raise DiscoveryError("discovery_finding_id_duplicate")
        if disposition not in ALLOWED_CLASSIFICATION_DISPOSITIONS:
            raise DiscoveryError("discovery_disposition_invalid")
        if isinstance(owner_issue, bool) or not isinstance(owner_issue, int) or owner_issue <= 0:
            raise DiscoveryError("discovery_owner_issue_invalid")
        if not isinstance(rationale, str) or len(rationale.strip()) < 20:
            raise DiscoveryError("discovery_rationale_invalid")
        if not isinstance(next_action, str) or len(next_action.strip()) < 10:
            raise DiscoveryError("discovery_next_action_invalid")
        result[finding_id] = dict(row)
    return result


def _legacy_domains_for_paths(
    repo_root: Path,
    registry: Mapping[str, Any],
    paths: Sequence[str],
    *,
    cache: dict[str, str | None],
) -> tuple[list[str], list[int], bool]:
    path_matches: list[set[str]] = []
    owner_issues: set[int] = set()

    def read_text(path: str) -> str | None:
        if path not in cache:
            cache[path] = _read_text_bounded(repo_root, path, max_bytes=registry["max_text_bytes"])
        return cache[path]

    for path in paths:
        matched = {
            domain["id"]
            for domain in registry["domains"]
            if _domain_matches(domain, path, read_text=read_text)
        }
        path_matches.append(matched)
        for domain in registry["domains"]:
            if domain["id"] in matched:
                owner_issues.add(domain["owner_issue"])
    all_routed = bool(path_matches) and all(bool(item) for item in path_matches)
    return sorted(set().union(*path_matches) if path_matches else set()), sorted(owner_issues), all_routed


def scan_repository(repo_root: Path, registry_path: Path, ledger_path: Path) -> dict[str, Any]:
    registry = load_registry(registry_path)
    classifications = _load_ledger_classifications(ledger_path)
    tracked = collect_tracked_files(repo_root)
    findings = (
        _root_findings(tracked)
        + _suspicious_name_findings(tracked)
        + _duplicate_findings(repo_root, tracked)
        + _frontend_findings(repo_root, tracked)
        + _backend_findings(repo_root, tracked)
    )
    unique: dict[str, Finding] = {}
    for finding in findings:
        unique.setdefault(finding["finding_id"], finding)

    text_cache: dict[str, str | None] = {}
    reported: list[dict[str, Any]] = []
    unclassified: list[str] = []
    actionable_delete: list[str] = []
    unknown: list[str] = []
    routed_count = 0
    classified_count = 0

    for finding_id, finding in sorted(unique.items()):
        domains, owner_issues, all_routed = _legacy_domains_for_paths(
            repo_root, registry, finding["paths"], cache=text_cache
        )
        classification = classifications.get(finding_id)
        status = "unclassified"
        if all_routed:
            status = "routed_to_legacy_registry"
            routed_count += 1
        elif classification is not None:
            status = "classified"
            classified_count += 1
            disposition = classification["disposition"]
            if disposition in DELETE_DISPOSITIONS:
                status = "classified_delete_still_present"
                actionable_delete.append(finding_id)
            elif disposition == "UNKNOWN_BLOCK_DELETE":
                unknown.append(finding_id)
        else:
            unclassified.append(finding_id)

        if len(reported) < MAX_FINDINGS:
            row = dict(finding)
            row.update(
                status=status,
                legacy_domain_ids=domains,
                legacy_owner_issues=owner_issues,
            )
            if classification is not None:
                row["classification"] = classification
            reported.append(row)

    stale_classifications = sorted(set(classifications) - set(unique))
    ok = not unclassified and not actionable_delete and not stale_classifications
    return {
        "schema": RESULT_SCHEMA,
        "ok": ok,
        "tracked_file_count": len(tracked),
        "finding_count": len(unique),
        "reported_finding_count": len(reported),
        "findings_truncated": len(unique) > len(reported),
        "routed_to_legacy_registry_count": routed_count,
        "explicitly_classified_count": classified_count,
        "unclassified_count": len(unclassified),
        "actionable_delete_count": len(actionable_delete),
        "unknown_block_delete_count": len(unknown),
        "stale_classification_count": len(stale_classifications),
        "unclassified_finding_ids": unclassified[:MAX_FINDINGS],
        "actionable_delete_finding_ids": actionable_delete[:MAX_FINDINGS],
        "unknown_block_delete_finding_ids": unknown[:MAX_FINDINGS],
        "stale_classification_ids": stale_classifications[:MAX_FINDINGS],
        "findings": reported,
    }


def run(repo_root: Path, registry_path: Path, ledger_path: Path) -> tuple[int, dict[str, Any]]:
    try:
        result = scan_repository(repo_root, registry_path, ledger_path)
        return (0 if result["ok"] else 1), result
    except (DiscoveryError, RuntimeError, ValueError) as exc:
        return 2, {"schema": RESULT_SCHEMA, "ok": False, "error": str(exc)[:240]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and classify suspicious Nexus repository assets.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--registry", type=Path, default=Path("config/governance/legacy-surface-domains.v1.json")
    )
    parser.add_argument(
        "--ledger", type=Path, default=Path("docs/ai/codebase-rationalization-inventory.v1.yaml")
    )
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    registry = args.registry if args.registry.is_absolute() else root / args.registry
    ledger = args.ledger if args.ledger.is_absolute() else root / args.ledger
    status, result = run(root, registry, ledger)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    sys.exit(main())
