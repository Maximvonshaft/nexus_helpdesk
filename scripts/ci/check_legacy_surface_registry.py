#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

REGISTRY_SCHEMA = "nexus.legacy-surface.registry.v1"
RESULT_SCHEMA = "nexus.legacy-surface.scan-result.v1"
SUPPORTED_ENFORCEMENT = {"inventory_only", "fail_closed"}
ALLOWED_DISPOSITIONS = {
    "active_authority",
    "active_compatibility",
    "parallel_implementation",
    "data_migration_dependency",
    "historical_evidence",
    "safe_to_remove",
    "generated_or_vendor",
    "protected_history",
    "unknown_fail_closed",
}
PROTECTED_DISPOSITIONS = {"active_authority", "data_migration_dependency", "protected_history"}
REQUIRED_TOP_LEVEL = {
    "schema",
    "registry_version",
    "audited_main_sha",
    "coverage",
    "enforcement",
    "finding_limit",
    "max_text_bytes",
    "allowed_dispositions",
    "domains",
    "discovery_rules",
}
DOMAIN_KEYS = {
    "id",
    "owner_issue",
    "disposition",
    "deletion_authorized",
    "rationale",
    "prerequisites",
    "selectors",
    "authoritative_refs",
}
SELECTOR_KEYS = {"paths", "globs", "content_rules"}
CONTENT_RULE_KEYS = {"markers", "path_globs"}
DISCOVERY_KEYS = {
    "id",
    "path_regex",
    "path_globs",
    "content_markers",
    "content_path_globs",
    "allowed_domain_ids",
    "allow_multiple_domains",
}
HEX_40_RE = re.compile(r"^[0-9a-f]{40}$")


class RegistryValidationError(ValueError):
    """Raised when the legacy-surface registry is malformed."""


def _string_list(value: Any, *, field: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise RegistryValidationError(f"{field}_must_be_string_list")
    if not allow_empty and not value:
        raise RegistryValidationError(f"{field}_must_not_be_empty")
    if len(value) != len(set(value)):
        raise RegistryValidationError(f"{field}_must_be_unique")
    return list(value)


def _exact_keys(obj: Mapping[str, Any], expected: set[str], *, field: str) -> None:
    actual = set(obj)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise RegistryValidationError(
            f"{field}_keys_invalid:missing={','.join(missing) or '-'}:extra={','.join(extra) or '-'}"
        )


def _validate_selectors(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryValidationError(f"{field}_must_be_object")
    _exact_keys(value, SELECTOR_KEYS, field=field)
    paths = _string_list(value["paths"], field=f"{field}.paths")
    globs = _string_list(value["globs"], field=f"{field}.globs")
    content_rules = value["content_rules"]
    if not isinstance(content_rules, list):
        raise RegistryValidationError(f"{field}.content_rules_must_be_list")
    normalized_rules: list[dict[str, list[str]]] = []
    for index, rule in enumerate(content_rules):
        if not isinstance(rule, dict):
            raise RegistryValidationError(f"{field}.content_rules[{index}]_must_be_object")
        _exact_keys(rule, CONTENT_RULE_KEYS, field=f"{field}.content_rules[{index}]")
        markers = _string_list(
            rule["markers"],
            field=f"{field}.content_rules[{index}].markers",
            allow_empty=False,
        )
        path_globs = _string_list(
            rule["path_globs"],
            field=f"{field}.content_rules[{index}].path_globs",
            allow_empty=False,
        )
        normalized_rules.append({"markers": markers, "path_globs": path_globs})
    if not paths and not globs and not normalized_rules:
        raise RegistryValidationError(f"{field}_must_have_selector")
    return {"paths": paths, "globs": globs, "content_rules": normalized_rules}


def validate_registry(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise RegistryValidationError("registry_must_be_object")
    _exact_keys(raw, REQUIRED_TOP_LEVEL, field="registry")
    if raw["schema"] != REGISTRY_SCHEMA:
        raise RegistryValidationError("registry_schema_unsupported")
    if not isinstance(raw["registry_version"], str) or not raw["registry_version"].strip():
        raise RegistryValidationError("registry_version_invalid")
    if not isinstance(raw["audited_main_sha"], str) or not HEX_40_RE.fullmatch(raw["audited_main_sha"]):
        raise RegistryValidationError("audited_main_sha_invalid")
    if not isinstance(raw["coverage"], str) or not raw["coverage"].strip():
        raise RegistryValidationError("coverage_invalid")
    if raw["enforcement"] not in SUPPORTED_ENFORCEMENT:
        raise RegistryValidationError("enforcement_invalid")
    if not isinstance(raw["finding_limit"], int) or isinstance(raw["finding_limit"], bool) or not 1 <= raw["finding_limit"] <= 500:
        raise RegistryValidationError("finding_limit_invalid")
    if not isinstance(raw["max_text_bytes"], int) or isinstance(raw["max_text_bytes"], bool) or not 1024 <= raw["max_text_bytes"] <= 2_000_000:
        raise RegistryValidationError("max_text_bytes_invalid")
    allowed = set(_string_list(raw["allowed_dispositions"], field="allowed_dispositions", allow_empty=False))
    if allowed != ALLOWED_DISPOSITIONS:
        raise RegistryValidationError("allowed_dispositions_contract_mismatch")

    domains_raw = raw["domains"]
    if not isinstance(domains_raw, list) or not domains_raw:
        raise RegistryValidationError("domains_must_be_non_empty_list")
    domains: list[dict[str, Any]] = []
    domain_ids: set[str] = set()
    for index, domain in enumerate(domains_raw):
        field = f"domains[{index}]"
        if not isinstance(domain, dict):
            raise RegistryValidationError(f"{field}_must_be_object")
        _exact_keys(domain, DOMAIN_KEYS, field=field)
        domain_id = domain["id"]
        if not isinstance(domain_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", domain_id):
            raise RegistryValidationError(f"{field}.id_invalid")
        if domain_id in domain_ids:
            raise RegistryValidationError("domain_id_duplicate")
        domain_ids.add(domain_id)
        owner_issue = domain["owner_issue"]
        if not isinstance(owner_issue, int) or isinstance(owner_issue, bool) or owner_issue <= 0:
            raise RegistryValidationError(f"{field}.owner_issue_invalid")
        disposition = domain["disposition"]
        if disposition not in ALLOWED_DISPOSITIONS:
            raise RegistryValidationError(f"{field}.disposition_invalid")
        if domain["deletion_authorized"] is not False:
            raise RegistryValidationError(f"{field}.deletion_authorized_must_be_false")
        if not isinstance(domain["rationale"], str) or not domain["rationale"].strip():
            raise RegistryValidationError(f"{field}.rationale_invalid")
        prerequisites = _string_list(domain["prerequisites"], field=f"{field}.prerequisites")
        authoritative_refs = _string_list(domain["authoritative_refs"], field=f"{field}.authoritative_refs")
        selectors = _validate_selectors(domain["selectors"], field=f"{field}.selectors")
        if disposition == "safe_to_remove" and not prerequisites:
            raise RegistryValidationError(f"{field}.safe_to_remove_requires_prerequisites")
        if domain_id.startswith("protected_") and disposition not in PROTECTED_DISPOSITIONS:
            raise RegistryValidationError(f"{field}.protected_domain_disposition_invalid")
        domains.append(
            {
                **domain,
                "prerequisites": prerequisites,
                "authoritative_refs": authoritative_refs,
                "selectors": selectors,
            }
        )

    rules_raw = raw["discovery_rules"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise RegistryValidationError("discovery_rules_must_be_non_empty_list")
    rule_ids: set[str] = set()
    rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules_raw):
        field = f"discovery_rules[{index}]"
        if not isinstance(rule, dict):
            raise RegistryValidationError(f"{field}_must_be_object")
        _exact_keys(rule, DISCOVERY_KEYS, field=field)
        rule_id = rule["id"]
        if not isinstance(rule_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", rule_id):
            raise RegistryValidationError(f"{field}.id_invalid")
        if rule_id in rule_ids:
            raise RegistryValidationError("discovery_rule_id_duplicate")
        rule_ids.add(rule_id)
        path_regex = rule["path_regex"]
        if path_regex is not None:
            if not isinstance(path_regex, str) or not path_regex:
                raise RegistryValidationError(f"{field}.path_regex_invalid")
            try:
                re.compile(path_regex)
            except re.error as exc:
                raise RegistryValidationError(f"{field}.path_regex_invalid") from exc
        path_globs = _string_list(rule["path_globs"], field=f"{field}.path_globs")
        content_markers = _string_list(rule["content_markers"], field=f"{field}.content_markers")
        content_path_globs = _string_list(rule["content_path_globs"], field=f"{field}.content_path_globs")
        allowed_domain_ids = _string_list(
            rule["allowed_domain_ids"],
            field=f"{field}.allowed_domain_ids",
            allow_empty=False,
        )
        unknown_domains = sorted(set(allowed_domain_ids) - domain_ids)
        if unknown_domains:
            raise RegistryValidationError(f"{field}.allowed_domain_unknown:{','.join(unknown_domains)}")
        if not isinstance(rule["allow_multiple_domains"], bool):
            raise RegistryValidationError(f"{field}.allow_multiple_domains_invalid")
        if not path_regex and not path_globs and not content_markers:
            raise RegistryValidationError(f"{field}.must_have_discovery_signal")
        if content_markers and not content_path_globs:
            raise RegistryValidationError(f"{field}.content_markers_require_path_globs")
        rules.append(
            {
                **rule,
                "path_globs": path_globs,
                "content_markers": content_markers,
                "content_path_globs": content_path_globs,
                "allowed_domain_ids": allowed_domain_ids,
            }
        )

    return {
        **raw,
        "allowed_dispositions": sorted(allowed),
        "domains": domains,
        "discovery_rules": rules,
    }


def load_registry(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryValidationError("registry_read_or_json_error") from exc
    return validate_registry(raw)


def collect_tracked_files(repo_root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "--stage", "-z"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        raise RuntimeError("git_ls_files_failed")
    paths: list[str] = []
    for record in completed.stdout.split(b"\0"):
        if not record:
            continue
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode = metadata.split(b" ", 1)[0].decode("ascii")
            path = raw_path.decode("utf-8")
        except (ValueError, UnicodeError) as exc:
            raise RuntimeError("git_index_record_invalid") from exc
        if mode not in {"100644", "100755"}:
            continue
        paths.append(path)
    return sorted(set(paths))


def _glob_matches(path: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(path.casefold(), pattern.casefold())


def _path_matches(path: str, *, exact: Sequence[str], globs: Sequence[str]) -> bool:
    return path in exact or any(_glob_matches(path, pattern) for pattern in globs)


def _read_text_bounded(repo_root: Path, path: str, *, max_bytes: int) -> str | None:
    file_path = repo_root / path
    try:
        with file_path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
    except OSError:
        return None
    if len(data) > max_bytes or b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _domain_matches(
    domain: Mapping[str, Any],
    path: str,
    *,
    read_text: Callable[[str], str | None],
) -> bool:
    selectors = domain["selectors"]
    if _path_matches(path, exact=selectors["paths"], globs=selectors["globs"]):
        return True
    for rule in selectors["content_rules"]:
        if any(_glob_matches(path, pattern) for pattern in rule["path_globs"]):
            text = read_text(path)
            if text is not None and any(marker in text for marker in rule["markers"]):
                return True
    return False


def _discovery_matches(
    rule: Mapping[str, Any],
    path: str,
    *,
    read_text: Callable[[str], str | None],
) -> bool:
    path_regex = rule["path_regex"]
    if path_regex and re.search(path_regex, path):
        return True
    if any(_glob_matches(path, pattern) for pattern in rule["path_globs"]):
        return True
    if rule["content_markers"] and any(
        _glob_matches(path, pattern) for pattern in rule["content_path_globs"]
    ):
        text = read_text(path)
        if text is not None and any(marker in text for marker in rule["content_markers"]):
            return True
    return False


def _path_fingerprint(path: str) -> str:
    return hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]


def scan_registry(
    registry: Mapping[str, Any],
    tracked_files: Iterable[str],
    *,
    read_text: Callable[[str], str | None],
) -> dict[str, Any]:
    files = sorted(set(tracked_files))
    domains = list(registry["domains"])
    disposition_counts: Counter[str] = Counter()
    owner_issue_counts: Counter[str] = Counter()
    matched_paths: set[str] = set()
    findings: list[dict[str, Any]] = []
    unowned_count = 0
    overlap_count = 0

    for path in files:
        matched_domain_ids = [
            domain["id"]
            for domain in domains
            if _domain_matches(domain, path, read_text=read_text)
        ]
        for domain in domains:
            if domain["id"] in matched_domain_ids:
                disposition_counts[domain["disposition"]] += 1
                owner_issue_counts[str(domain["owner_issue"])] += 1
                matched_paths.add(path)

        for rule in registry["discovery_rules"]:
            if not _discovery_matches(rule, path, read_text=read_text):
                continue
            allowed_matches = sorted(set(matched_domain_ids) & set(rule["allowed_domain_ids"]))
            reason_codes: list[str] = []
            if not allowed_matches:
                unowned_count += 1
                reason_codes.append("legacy_surface_unowned")
            elif len(allowed_matches) > 1 and not rule["allow_multiple_domains"]:
                overlap_count += 1
                reason_codes.append("legacy_surface_owner_overlap")
            if reason_codes and len(findings) < registry["finding_limit"]:
                findings.append(
                    {
                        "path": path,
                        "path_sha256": _path_fingerprint(path),
                        "discovery_rule": rule["id"],
                        "matched_domain_ids": allowed_matches,
                        "reason_codes": reason_codes,
                    }
                )

    classification_complete = unowned_count == 0 and overlap_count == 0
    enforcement = registry["enforcement"]
    ok = classification_complete or enforcement == "inventory_only"
    return {
        "schema": RESULT_SCHEMA,
        "ok": ok,
        "classification_complete": classification_complete,
        "enforcement": enforcement,
        "registry_version": registry["registry_version"],
        "audited_main_sha": registry["audited_main_sha"],
        "coverage": registry["coverage"],
        "tracked_file_count": len(files),
        "matched_file_count": len(matched_paths),
        "finding_count": unowned_count + overlap_count,
        "reported_finding_count": len(findings),
        "findings_truncated": (unowned_count + overlap_count) > len(findings),
        "unowned_count": unowned_count,
        "overlap_count": overlap_count,
        "disposition_match_counts": dict(sorted(disposition_counts.items())),
        "owner_issue_match_counts": dict(sorted(owner_issue_counts.items())),
        "findings": findings,
    }


def run(repo_root: Path, registry_path: Path) -> tuple[int, dict[str, Any]]:
    try:
        registry = load_registry(registry_path)
        tracked = collect_tracked_files(repo_root)
        cache: dict[str, str | None] = {}

        def read_text(path: str) -> str | None:
            if path not in cache:
                cache[path] = _read_text_bounded(
                    repo_root,
                    path,
                    max_bytes=registry["max_text_bytes"],
                )
            return cache[path]

        result = scan_registry(registry, tracked, read_text=read_text)
        return (0 if result["ok"] else 1), result
    except (RegistryValidationError, RuntimeError) as exc:
        return 2, {
            "schema": RESULT_SCHEMA,
            "ok": False,
            "classification_complete": False,
            "error": str(exc),
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate and scan the Nexus legacy-surface registry.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument(
        "--registry",
        type=Path,
        default=Path("config/governance/legacy-surface-domains.v1.json"),
    )
    args = parser.parse_args(argv)
    repo_root = args.repo_root.resolve()
    registry_path = args.registry
    if not registry_path.is_absolute():
        registry_path = repo_root / registry_path
    status, result = run(repo_root, registry_path)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return status


if __name__ == "__main__":
    sys.exit(main())
