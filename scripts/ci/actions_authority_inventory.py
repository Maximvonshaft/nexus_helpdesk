#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

import yaml

INVENTORY_SCHEMA = "nexus.osr.actions-authority.v1"
RESULT_SCHEMA = "nexus.osr.actions-authority-audit.v1"
AUTHORITIES = ("frontend", "backend", "migration", "security", "release", "governance")
CLASSIFICATIONS = {"authoritative", "reusable", "matrix_component", "publication", "historical_delete"}
SHA_REF_RE = re.compile(r"@[0-9a-f]{40}(?:\s*(?:#.*)?)?$")
USES_RE = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)(?:\s*#.*)?$", re.MULTILINE)
MUTATION_RE = re.compile(
    r"(?:^|\s)(?:git\s+(?:commit|push|tag)|gh\s+(?:release|api)|curl\b.*?/contents/)",
    re.IGNORECASE,
)
SAFE_EVENT_SHELL_RE = re.compile(
    r"\$\{\{\s*(?:github\.event\.pull_request\.(?:head|base)\.sha(?:\s*\|\|\s*github\.sha)?|github\.event\.(?:pull_request|issue)\.number)\s*\}\}"
)
PR_HEAD_CHECKOUT_RE = re.compile(
    r"uses:\s*actions/checkout@[^\n]+\n(?:(?:\s+[^\n]*\n){0,14}?)\s+ref:\s*\$\{\{[^}]*github\.event\.pull_request\.head\.sha[^}]*\}\}",
    re.MULTILINE,
)


class ActionsAuthorityError(ValueError):
    pass


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ActionsAuthorityError("inventory_duplicate_key")
        result[key] = value
    return result


def _load_inventory(path: Path) -> Mapping[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ActionsAuthorityError("inventory_invalid") from exc
    required = {"schema", "authoritative", "publication_allowlist", "historical_delete", "classification_overrides"}
    if not isinstance(raw, dict) or set(raw) != required or raw.get("schema") != INVENTORY_SCHEMA:
        raise ActionsAuthorityError("inventory_schema_or_fields_invalid")
    authoritative = raw.get("authoritative")
    if not isinstance(authoritative, dict) or set(authoritative) != set(AUTHORITIES):
        raise ActionsAuthorityError("authoritative_workflow_map_invalid")
    for authority, value in authoritative.items():
        if not isinstance(value, str) or not value.startswith(".github/workflows/"):
            raise ActionsAuthorityError(f"authoritative_path_invalid:{authority}")
    for key in ("publication_allowlist", "historical_delete"):
        values = raw.get(key)
        if not isinstance(values, list) or len(values) != len(set(values)) or not all(isinstance(v, str) for v in values):
            raise ActionsAuthorityError(f"{key}_invalid")
    overrides = raw.get("classification_overrides")
    if not isinstance(overrides, dict):
        raise ActionsAuthorityError("classification_overrides_invalid")
    for path_value, row in overrides.items():
        if not isinstance(path_value, str) or not isinstance(row, dict) or set(row) != {"classification", "authority"}:
            raise ActionsAuthorityError("classification_override_invalid")
        if row["classification"] not in CLASSIFICATIONS or row["authority"] not in AUTHORITIES:
            raise ActionsAuthorityError("classification_override_value_invalid")
    return raw


def _workflow_paths(repo_root: Path) -> list[str]:
    root = repo_root / ".github/workflows"
    rows = [
        path.relative_to(repo_root).as_posix()
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in {".yml", ".yaml"}
    ] if root.is_dir() else []
    return sorted(rows)


def _authority_from_name(path: str) -> str:
    name = PurePosixPath(path).name.casefold()
    if any(token in name for token in ("frontend", "webapp", "semantic", "route-splitting", "operator-unified")):
        return "frontend"
    if any(token in name for token in ("postgres", "migration", "schema", "backfill", "tenant-principal")):
        return "migration"
    if any(token in name for token in ("security", "codeql", "secret", "dependency", "sbom", "supply-chain")):
        return "security"
    if any(token in name for token in ("release", "image", "candidate", "production-readiness", "deploy", "recovery", "resilience")):
        return "release"
    if any(token in name for token in ("coordination", "rational", "governance", "external-channel-retirement", "delivery-truth")):
        return "governance"
    return "backend"


def _classification(path: str, inventory: Mapping[str, Any]) -> tuple[str, str]:
    override = inventory["classification_overrides"].get(path)
    if override:
        return str(override["classification"]), str(override["authority"])
    if path in inventory["historical_delete"]:
        return "historical_delete", _authority_from_name(path)
    if path in inventory["publication_allowlist"]:
        return "publication", "release"
    for authority, authority_path in inventory["authoritative"].items():
        if path == authority_path:
            return "authoritative", str(authority)
    return "matrix_component", _authority_from_name(path)


def _yaml_document(text: str) -> Mapping[str, Any]:
    try:
        raw = yaml.load(text, Loader=yaml.BaseLoader)
    except yaml.YAMLError as exc:
        raise ActionsAuthorityError("workflow_yaml_invalid") from exc
    return raw if isinstance(raw, dict) else {}


def _has_trigger(document: Mapping[str, Any], name: str) -> bool:
    on_value = document.get("on")
    if isinstance(on_value, str):
        return on_value == name
    if isinstance(on_value, list):
        return name in on_value
    if isinstance(on_value, dict):
        return name in on_value
    return False


def _walk_run_values(value: Any) -> list[str]:
    rows: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"run", "script"} and isinstance(child, str):
                rows.append(child)
            rows.extend(_walk_run_values(child))
    elif isinstance(value, list):
        for child in value:
            rows.extend(_walk_run_values(child))
    return rows


def _permissions(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("permissions")
    return value if isinstance(value, dict) else {}


def audit_workflow(path: Path, *, classification: str, authority: str) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8")
    document = _yaml_document(text)
    findings: list[dict[str, str]] = []

    def add(code: str, detail: str) -> None:
        findings.append({"code": code, "path": path.as_posix(), "detail": detail[:240]})

    for reference in USES_RE.findall(text):
        if reference.startswith("./"):
            continue
        if reference.startswith("docker://"):
            if "@sha256:" not in reference:
                add("mutable_container_action_reference", reference)
            continue
        if not SHA_REF_RE.search(reference):
            add("mutable_action_reference", reference)

    triggers_pr = _has_trigger(document, "pull_request") or _has_trigger(document, "pull_request_target")
    privileged = any(_has_trigger(document, name) for name in ("pull_request_target", "workflow_run", "issue_comment", "issues"))
    permissions = _permissions(document)
    contents_permission = permissions.get("contents")
    has_contents_write = contents_permission == "write" or document.get("permissions") == "write-all"

    if triggers_pr and has_contents_write:
        add("pull_request_write_permission", "PR-triggered workflow grants repository write authority")
    if has_contents_write and not (classification == "publication" and authority == "release"):
        add("contents_write_outside_publication", "contents: write is reserved for release publication")

    run_values = _walk_run_values(document)
    for run_value in run_values:
        if triggers_pr and MUTATION_RE.search(run_value):
            add("pull_request_repository_mutation", "PR validation contains commit/push/tag/API mutation")
        sanitized = SAFE_EVENT_SHELL_RE.sub("", run_value)
        if "${{ github.event." in sanitized or "${{ github.head_ref" in sanitized:
            add("untrusted_event_shell_interpolation", "attacker-controlled event value is interpolated into executable script")

    trusted_split_checkout = (
        "path: .trusted" in text
        and "path: target" in text
        and (".trusted/scripts/" in text or "working-directory: trusted" in text)
    )
    if privileged and PR_HEAD_CHECKOUT_RE.search(text) and not trusted_split_checkout:
        add("privileged_trigger_executes_untrusted_head", "privileged trigger checks out and executes PR head")

    if triggers_pr and "actions/checkout@" in text and "persist-credentials: false" not in text:
        add("checkout_credentials_persisted", "PR validation checkout does not disable persisted credentials")

    return findings


def _duplicate_setup_findings(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for authority, marker_sets, code in (
        ("frontend", (("npm ci", "npm run build"), ("npm ci", "node --test")), "duplicate_frontend_install_build_authority"),
        ("backend", (("pip install", "pytest"),), "duplicate_backend_install_test_authority"),
    ):
        candidates: list[str] = []
        for row in rows:
            if row["authority"] != authority or row["classification"] in {"reusable", "matrix_component", "historical_delete", "publication"}:
                continue
            text = row["text"]
            if any(all(marker in text for marker in markers) for markers in marker_sets):
                candidates.append(row["path"])
        if len(candidates) > 1:
            findings.append({"code": code, "path": ",".join(sorted(candidates)[:12]), "detail": f"{len(candidates)} overlapping {authority} setup chains"})
    return findings


def audit_repository(repo_root: Path, inventory_path: Path) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    inventory = _load_inventory(inventory_path)
    tracked = _workflow_paths(repo_root)
    rows: list[dict[str, Any]] = []
    findings: list[dict[str, str]] = []
    authority_counts = {authority: 0 for authority in AUTHORITIES}

    for path_value in tracked:
        classification, authority = _classification(path_value, inventory)
        path = repo_root / path_value
        text = path.read_text(encoding="utf-8")
        rows.append({"path": path_value, "classification": classification, "authority": authority, "text": text})
        if classification == "authoritative":
            authority_counts[authority] += 1
        findings.extend(audit_workflow(path, classification=classification, authority=authority))

    findings.extend(_duplicate_setup_findings(rows))
    untracked_config = sorted(
        path for path in (
            list(inventory["authoritative"].values())
            + list(inventory["publication_allowlist"])
            + list(inventory["classification_overrides"])
        ) if path not in tracked
    )
    for path_value in untracked_config:
        findings.append({"code": "inventory_path_not_tracked", "path": path_value, "detail": "configured workflow path is absent"})
    for authority, count in authority_counts.items():
        if count != 1:
            findings.append({"code": "authoritative_workflow_count_invalid", "path": authority, "detail": f"expected 1, observed {count}"})

    failure_codes = sorted({row["code"] for row in findings})
    return {
        "schema": RESULT_SCHEMA,
        "ok": not findings,
        "tracked_workflows": tracked,
        "workflow_count": len(tracked),
        "authority_counts": authority_counts,
        "findings": sorted(findings, key=lambda row: (row["code"], row["path"])),
        "failure_codes": failure_codes,
        "unclassified_paths": [],
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit GitHub Actions authority, permissions and supply-chain references.")
    parser.add_argument("--repo-root", type=Path, default=Path("."))
    parser.add_argument("--inventory", type=Path, default=Path("config/governance/actions-authority.v1.json"))
    args = parser.parse_args(argv)
    root = args.repo_root.resolve()
    inventory = args.inventory if args.inventory.is_absolute() else root / args.inventory
    try:
        result = audit_repository(root, inventory)
    except (ActionsAuthorityError, OSError, ValueError) as exc:
        result = {"schema": RESULT_SCHEMA, "ok": False, "failure_codes": [str(exc)[:240]], "findings": []}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
