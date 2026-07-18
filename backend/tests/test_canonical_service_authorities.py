from __future__ import annotations

import ast
import json
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
APP = BACKEND / "app"
MANIFEST_PATH = PROJECT / "config" / "architecture" / "service-authority.v1.json"


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _project_path(relative: str) -> Path:
    return PROJECT / relative


def _source(relative: str) -> str:
    return _project_path(relative).read_text(encoding="utf-8")


def _tree(relative: str) -> ast.Module:
    return ast.parse(_source(relative), filename=relative)


def _imports_private_module(tree: ast.Module, module_stem: str) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.rsplit(".", 1)[-1] == module_stem for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").rsplit(".", 1)[-1] == module_stem:
                return True
            if any(alias.name == module_stem for alias in node.names):
                return True
    return False


def _module_aliases(tree: ast.Module) -> set[str]:
    aliases: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                aliases.add(alias.asname or alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name != "*":
                    aliases.add(alias.asname or alias.name)
    return aliases


def _attribute_assignment_target(node: ast.AST) -> tuple[str, str] | None:
    target: ast.AST | None = None
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for candidate in targets:
            if isinstance(candidate, ast.Attribute) and isinstance(candidate.value, ast.Name):
                return candidate.value.id, candidate.attr
    return target


def test_service_authority_manifest_is_complete_and_paths_exist() -> None:
    manifest = _manifest()
    assert manifest["schema"] == "nexus.service-authority.v1"
    assert manifest["authorities"]
    responsibilities = [row["responsibility"] for row in manifest["authorities"]]
    assert len(responsibilities) == len(set(responsibilities))

    public_paths = [row["public_authority"] for row in manifest["authorities"]]
    assert len(public_paths) == len(set(public_paths))
    for row in manifest["authorities"]:
        assert _project_path(row["public_authority"]).is_file(), row
        private = row.get("private_implementation")
        if private:
            assert _project_path(private).is_file(), row
        for shim in row.get("compatibility_shims", []):
            assert _project_path(shim).is_file(), row


def test_compatibility_shims_are_thin_and_logic_free() -> None:
    for row in _manifest()["authorities"]:
        public_stem = Path(row["public_authority"]).stem
        for shim in row.get("compatibility_shims", []):
            source = _source(shim)
            tree = ast.parse(source, filename=shim)
            assert public_stem in source, shim
            assert "UserRole" not in source, shim
            assert len(source.splitlines()) <= 24, shim
            functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert functions <= {"__getattr__"}, (shim, functions)
            assert not any(isinstance(node, (ast.ClassDef, ast.Lambda)) for node in tree.body), shim


def test_private_implementations_are_imported_only_by_public_authority() -> None:
    production_files = [path for path in APP.rglob("*.py") if "__pycache__" not in path.parts]
    for row in _manifest()["authorities"]:
        private = row.get("private_implementation")
        if not private:
            continue
        private_path = _project_path(private).resolve()
        public_path = _project_path(row["public_authority"]).resolve()
        stem = private_path.stem
        assert _imports_private_module(_tree(row["public_authority"]), stem), row
        offenders: list[str] = []
        for path in production_files:
            if path.resolve() in {private_path, public_path}:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            if _imports_private_module(tree, stem):
                offenders.append(str(path.relative_to(PROJECT)))
        assert offenders == [], f"{stem} imported outside {row['public_authority']}: {offenders}"


def test_authority_modules_do_not_monkey_patch_imported_modules() -> None:
    offenders: list[str] = []
    for path in APP.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_aliases = _module_aliases(tree)
        for node in tree.body:
            target = _attribute_assignment_target(node)
            if target and target[0] in imported_aliases:
                offenders.append(
                    f"{path.relative_to(PROJECT)}:{getattr(node, 'lineno', '?')} {target[0]}.{target[1]}"
                )
            if (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "setattr"
                and node.value.args
                and isinstance(node.value.args[0], ast.Name)
                and node.value.args[0].id in imported_aliases
            ):
                offenders.append(
                    f"{path.relative_to(PROJECT)}:{getattr(node, 'lineno', '?')} setattr({node.value.args[0].id}, ...)"
                )
    assert offenders == [], offenders


def test_manifest_authorities_use_capabilities_not_role_name_branches() -> None:
    for row in _manifest()["authorities"]:
        for relative in filter(None, [row["public_authority"], row.get("private_implementation")]):
            assert "UserRole" not in _source(relative), relative

    permissions = (APP / "services" / "permissions.py").read_text(encoding="utf-8")
    assert "ROLE_CAPABILITIES" in permissions
    assert "has_global_case_visibility" in permissions
    assert "ensure_ticket_visible(user, ticket, db)" in permissions


def test_production_entrypoints_use_canonical_authorities() -> None:
    tickets = (APP / "api" / "tickets.py").read_text(encoding="utf-8")
    lite = (APP / "api" / "lite.py").read_text(encoding="utf-8")
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "services.canonical_ticket_service" in tickets
    assert "services.canonical_control_tower_service" in lite
    assert "services.canonical_qa_training_service" in lite
    assert "._validate_password_length =" not in main
    assert "setattr(" not in main
