from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"

PUBLIC_FACADES = {
    "services/ticket_service.py": "canonical_ticket_service",
    "services/control_tower_service.py": "canonical_control_tower_service",
    "services/qa_training_service.py": "canonical_qa_training_service",
    "services/operator_work_queue.py": "canonical_operator_work_queue",
    "services/webchat_handoff_service.py": "canonical_webchat_handoff_service",
    "api/osr_admin.py": "canonical_osr_admin",
    "api/integration.py": "canonical_integration",
}

CANONICAL_TO_CORE = {
    "services/canonical_ticket_service.py": "ticket_service_core",
    "api/canonical_osr_admin.py": "osr_admin_core",
    "api/canonical_integration.py": "integration_core",
    "services/canonical_qa_training_service.py": "qa_training_service_core",
    "services/canonical_operator_work_queue.py": "operator_work_queue_core",
    "services/canonical_webchat_handoff_service.py": "webchat_handoff_service_core",
}


def read(relative: str) -> str:
    return (APP / relative).read_text(encoding="utf-8")


def test_public_compatibility_paths_cannot_own_business_logic() -> None:
    for relative, canonical in PUBLIC_FACADES.items():
        content = read(relative)
        assert canonical in content, relative
        assert "UserRole" not in content, relative
        assert len(content.splitlines()) <= 20, relative
        function_names = re.findall(r"^def\s+(\w+)", content, re.MULTILINE)
        assert set(function_names) <= {"__getattr__"}, relative


def test_each_large_service_has_one_canonical_public_authority() -> None:
    for relative, core_name in CANONICAL_TO_CORE.items():
        content = read(relative)
        assert core_name in content, relative
        assert "UserRole" not in content, relative
        assert (APP / f"{relative.rsplit('/', 1)[0]}/{core_name}.py").is_file(), relative


def test_private_cores_are_imported_only_by_their_canonical_authority() -> None:
    production_files = [path for path in APP.rglob("*.py") if "__pycache__" not in path.parts]
    for canonical_relative, core_name in CANONICAL_TO_CORE.items():
        canonical_path = (APP / canonical_relative).resolve()
        offenders: list[str] = []
        for path in production_files:
            if path.resolve() == canonical_path or path.name == f"{core_name}.py":
                continue
            if core_name in path.read_text(encoding="utf-8"):
                offenders.append(str(path.relative_to(ROOT)))
        assert offenders == [], f"{core_name} imported outside {canonical_relative}: {offenders}"


def test_runtime_authorization_does_not_branch_on_role_names() -> None:
    permissions = read("services/permissions.py")
    assert "ROLE_CAPABILITIES" in permissions
    assert not re.search(r"\bif\s+[^\n]*\.role\b", permissions)
    assert not re.search(r"\bif\s+[^\n]*UserRole\.", permissions)
    assert "has_global_case_visibility" in permissions
    assert "ensure_ticket_visible(user, ticket, db)" in permissions


def test_production_entrypoints_use_canonical_authorities() -> None:
    tickets = read("api/tickets.py")
    lite = read("api/lite.py")
    assert "services.canonical_ticket_service" in tickets
    assert "services.canonical_control_tower_service" in lite
    assert "services.canonical_qa_training_service" in lite
