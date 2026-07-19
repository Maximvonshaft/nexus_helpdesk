from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
REGISTRY_PATH = ROOT / "docs" / "ai" / "native-skills-registry.yaml"


def _load_registry() -> dict[str, object]:
    payload = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_native_rationalization_skill_is_registered_and_resolvable() -> None:
    registry = _load_registry()
    assert registry["schema"] == "nexus.osr.native-skills-registry.v1"
    assert registry["status"] == "active"
    selection = registry["selection_policy"]
    assert selection["maximum_roles_per_task"] == 4
    assert selection["allowed_roles"] == [
        "process",
        "primary_domain",
        "security",
        "verification",
    ]
    profiles = registry["task_profiles"]
    assert set(profiles) == {
        "codebase_rationalization_audit",
        "codebase_rationalization_slice",
    }
    skill = registry["skills"]["nexus_codebase_rationalization"]
    assert skill["enabled"] is True
    assert skill["role"] == "primary_domain"
    assert skill["execution_mode"] == "instructions_only"
    assert (ROOT / skill["entrypoint"]).is_file()


def test_native_registry_uses_current_authority_and_preserves_destructive_boundary() -> None:
    registry = _load_registry()
    assert registry["authority_order"][:4] == [
        "explicit_scope_specific_user_authorization",
        "compatibility_lifecycle_authority",
        "current_tree_discovery_registry",
        "current_github_objects_and_exact_commit_evidence",
    ]
    skill = registry["skills"]["nexus_codebase_rationalization"]
    constraints = set(skill["constraints"])
    assert {
        "no_direct_main_writes",
        "no_blind_repository_wide_delete",
        "broad_cleanup_request_does_not_authorize_destructive_production_data_change",
        "current_tree_and_runtime_consumers_required",
        "exact_head_evidence_required",
        "source_deletion_does_not_authorize_deployment_or_provider_enablement",
        "mutable_delivery_status_forbidden_in_long_lived_authority_files",
    }.issubset(constraints)


def test_native_skill_front_matter_matches_registry() -> None:
    registry = _load_registry()
    skill = registry["skills"]["nexus_codebase_rationalization"]
    text = (ROOT / skill["entrypoint"]).read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, front_matter, body = text.split("---\n", 2)
    metadata = yaml.safe_load(front_matter)
    assert metadata == {
        "name": "nexus-codebase-rationalization",
        "description": metadata["description"],
        "version": "2.0.0",
        "owner": "nexus_osr_engineering_governance",
    }
    assert isinstance(metadata["description"], str)
    assert len(metadata["description"]) >= 40
    assert "config/governance/legacy-surface-domains.v2.json" in body
    assert "A broad cleanup request does not by itself authorize" in body
