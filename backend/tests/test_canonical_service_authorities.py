from __future__ import annotations

import ast
import asyncio
import json
from datetime import date
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
APP = BACKEND / "app"
MANIFEST_PATH = PROJECT / "config" / "architecture" / "service-authority.v1.json"
LIFECYCLE_PATH = PROJECT / "config" / "architecture" / "compatibility-lifecycle.v1.json"
IGNORED_GENERATED_ROOTS = {".git", ".venv", "node_modules", "vendor"}


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _lifecycle() -> dict:
    return json.loads(LIFECYCLE_PATH.read_text(encoding="utf-8"))


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
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for candidate in targets:
            if isinstance(candidate, ast.Attribute) and isinstance(candidate.value, ast.Name):
                return candidate.value.id, candidate.attr
    return None


def _json_request(path: str, payload: dict) -> Request:
    body = json.dumps(payload).encode("utf-8")
    delivered = False

    async def receive() -> dict:
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("testclient", 123),
        "server": ("testserver", 80),
    }
    return Request(scope, receive)


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


def test_compatibility_lifecycle_assets_are_bounded() -> None:
    lifecycle = _lifecycle()
    assert lifecycle["schema"] == "nexus.compatibility-lifecycle.v1"
    paths = [row["path"] for row in lifecycle["assets"]]
    assert len(paths) == len(set(paths))
    for row in lifecycle["assets"]:
        assert row["owner"], row
        assert _project_path(row["path"]).exists(), row
        if row["kind"] in {"compose-alias", "environment-tombstone"}:
            assert row["replacement"], row
            assert row["remove_after"], row
            assert date.fromisoformat(row["remove_after"]) > date(2026, 7, 18), row

    for relative in ("deploy/docker-compose.server.yml", "deploy/docker-compose.candidate.yml"):
        source = _source(relative)
        assert "services:" not in source, relative
        assert "include:" in source, relative

    for relative in (
        "deploy/.env.prod.example",
        "deploy/.env.prod.local-postgres.example",
        "deploy/.env.prod.external-postgres.example",
    ):
        source = _source(relative)
        assert "RETIRED COMPATIBILITY PATH" in source, relative
        assert "NEXUS_ENV_TEMPLATE_RETIRED=true" in source, relative
        assert "SECRET_KEY=" not in source, relative
        assert "DATABASE_URL=" not in source, relative


def test_admin_password_policy_is_bound_without_runtime_mutation() -> None:
    from app.api.admin_password_policy import enforce_admin_password_request_policy

    main = (APP / "main.py").read_text(encoding="utf-8")
    guard = (APP / "api" / "admin_password_policy.py").read_text(encoding="utf-8")
    auth = (APP / "auth_service.py").read_text(encoding="utf-8")
    assert "dependencies=[Depends(enforce_admin_password_request_policy)]" in main
    assert "validate_admin_password_policy" in guard
    assert "validate_admin_password_policy" not in auth
    assert "._validate_password_length =" not in main

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            enforce_admin_password_request_policy(
                _json_request("/api/admin/users", {"password": "pass123"})
            )
        )
    assert exc_info.value.status_code == 400

    asyncio.run(
        enforce_admin_password_request_policy(
            _json_request(
                "/api/admin/users/12/reset-password",
                {"password": "NexusAdmin-2026-Strong!"},
            )
        )
    )


def test_fastapi_method_and_normalized_path_are_unique() -> None:
    from app.main import app
    from scripts.qualification.route_authority import qualification_payload

    payload = qualification_payload(app)
    assert payload["duplicates"] == [], payload["duplicates"]
    assert payload["status"] == "pass"


def test_alembic_is_the_only_executable_schema_mutation_authority() -> None:
    sql_files = [
        path
        for path in PROJECT.rglob("*.sql")
        if not any(part in IGNORED_GENERATED_ROOTS for part in path.parts)
    ]
    assert sql_files == [], [str(path.relative_to(PROJECT)) for path in sql_files]
    history = PROJECT / "docs/history/migrations/20260505-webchat-ai-turn-runtime.md"
    assert history.is_file()
    assert "Alembic" in history.read_text(encoding="utf-8")


def test_production_entrypoints_use_canonical_authorities() -> None:
    tickets = (APP / "api" / "tickets.py").read_text(encoding="utf-8")
    lite = (APP / "api" / "lite.py").read_text(encoding="utf-8")
    main = (APP / "main.py").read_text(encoding="utf-8")
    assert "services.canonical_ticket_service" in tickets
    assert "services.canonical_control_tower_service" in lite
    assert "services.canonical_qa_training_service" in lite
    assert "._validate_password_length =" not in main
    assert "setattr(" not in main
