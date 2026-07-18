#!/usr/bin/env python3
"""Validate the one-authority/one-private-core service architecture.

This is a read-only qualification tool. It consumes the canonical manifest and
rejects independently callable private implementations, business-bearing shims,
and import-time mutation of another module.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"
MANIFEST = ROOT / "config" / "architecture" / "service-authority.v1.json"


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT / "backend").with_suffix("")
    return ".".join(relative.parts)


def _resolve_from(current: str, *, level: int, module: str | None) -> str:
    package = current.rsplit(".", 1)[0]
    parts = package.split(".")
    if level:
        keep = max(0, len(parts) - level + 1)
        parts = parts[:keep]
    if module:
        parts.extend(module.split("."))
    return ".".join(parts)


def _imported_modules(path: Path) -> tuple[set[str], dict[str, str]]:
    current = _module_name(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    module_aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                module_aliases[alias.asname or alias.name.split(".")[0]] = alias.name
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_from(current, level=node.level, module=node.module)
            if node.module:
                modules.add(base)
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                # ``from . import module as alias`` imports a module. For normal
                # symbol imports the candidate will not match a manifest module
                # and is therefore harmless.
                modules.add(candidate)
                module_aliases[alias.asname or alias.name] = candidate
    return modules, module_aliases


def _root_name(node: ast.AST) -> str | None:
    current = node
    while isinstance(current, ast.Attribute):
        current = current.value
    return current.id if isinstance(current, ast.Name) else None


def _module_mutations(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    _, aliases = _imported_modules(path)
    findings: list[str] = []

    def inspect_target(target: ast.AST, line: int) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                inspect_target(item, line)
            return
        if not isinstance(target, ast.Attribute):
            return
        root = _root_name(target)
        if root and root in aliases:
            findings.append(f"{path.relative_to(ROOT)}:{line}:mutates_imported_module:{root}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                inspect_target(target, node.lineno)
        elif isinstance(node, ast.AnnAssign):
            inspect_target(node.target, node.lineno)
        elif isinstance(node, ast.AugAssign):
            inspect_target(node.target, node.lineno)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "setattr":
            if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id in aliases:
                findings.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}:setattr_on_imported_module:{node.args[0].id}"
                )
    return findings


def _shim_findings(path: Path, expected_authority: str) -> list[str]:
    findings: list[str] = []
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    expected_module = _module_name(ROOT / expected_authority)
    imported, _ = _imported_modules(path)
    if expected_module not in imported:
        findings.append(f"shim_does_not_import_authority:{path.relative_to(ROOT)}:{expected_authority}")
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "__getattr__":
            findings.append(f"shim_owns_function:{path.relative_to(ROOT)}:{node.name}")
        elif isinstance(node, ast.ClassDef):
            findings.append(f"shim_owns_class:{path.relative_to(ROOT)}:{node.name}")
    if len(source.splitlines()) > 24:
        findings.append(f"shim_unbounded:{path.relative_to(ROOT)}:{len(source.splitlines())}")
    findings.extend(_module_mutations(path))
    return findings


def qualification_payload() -> dict[str, Any]:
    findings: list[str] = []
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "schema": "nexus.service-authority-qualification.v1",
            "status": "fail",
            "findings": [f"manifest_unavailable:{type(exc).__name__}"],
        }

    if manifest.get("schema") != "nexus.service-authority.v1":
        findings.append(f"manifest_schema_invalid:{manifest.get('schema')}")
    authorities = manifest.get("authorities")
    if not isinstance(authorities, list) or not authorities:
        findings.append("manifest_authorities_missing")
        authorities = []

    responsibilities: set[str] = set()
    public_paths: set[str] = set()
    private_paths: set[str] = set()
    shim_paths: set[str] = set()
    private_to_public: dict[str, str] = {}

    for record in authorities:
        if not isinstance(record, dict):
            findings.append("authority_record_invalid")
            continue
        responsibility = str(record.get("responsibility") or "")
        public = str(record.get("public_authority") or "")
        private = record.get("private_implementation")
        shims = record.get("compatibility_shims") or []
        if not responsibility or responsibility in responsibilities:
            findings.append(f"responsibility_missing_or_duplicate:{responsibility}")
        responsibilities.add(responsibility)
        if not public or public in public_paths:
            findings.append(f"public_authority_missing_or_duplicate:{public}")
        public_paths.add(public)
        public_file = ROOT / public
        if not public_file.is_file():
            findings.append(f"public_authority_missing:{public}")
        elif _module_mutations(public_file):
            findings.extend(_module_mutations(public_file))

        if private is not None:
            private = str(private)
            if private in private_paths or private in public_paths:
                findings.append(f"private_implementation_duplicate:{private}")
            private_paths.add(private)
            private_to_public[_module_name(ROOT / private)] = public
            if not (ROOT / private).is_file():
                findings.append(f"private_implementation_missing:{private}")

        if not isinstance(shims, list):
            findings.append(f"compatibility_shims_invalid:{responsibility}")
            shims = []
        for shim in map(str, shims):
            if shim in shim_paths or shim in public_paths or shim in private_paths:
                findings.append(f"shim_duplicate_or_colliding:{shim}")
            shim_paths.add(shim)
            shim_file = ROOT / shim
            if not shim_file.is_file():
                findings.append(f"shim_missing:{shim}")
            elif public_file.is_file():
                findings.extend(_shim_findings(shim_file, public))

    production_files = [
        path for path in APP.rglob("*.py") if "__pycache__" not in path.parts
    ]
    for path in production_files:
        imported, _ = _imported_modules(path)
        importer = str(path.relative_to(ROOT))
        for private_module, allowed_public in private_to_public.items():
            if private_module in imported and importer != allowed_public:
                findings.append(
                    f"private_implementation_imported_outside_authority:{private_module}:{importer}:{allowed_public}"
                )

    constraints = manifest.get("constraints") or {}
    for required in (
        "private_implementation_imported_only_by_public_authority",
        "compatibility_shim_contains_business_logic",
        "runtime_module_monkey_patch",
        "role_name_authorization_outside_permissions",
    ):
        if required not in constraints:
            findings.append(f"manifest_constraint_missing:{required}")

    return {
        "schema": "nexus.service-authority-qualification.v1",
        "status": "pass" if not findings else "fail",
        "authority_count": len(authorities),
        "findings": sorted(set(findings)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    payload = qualification_payload()
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
