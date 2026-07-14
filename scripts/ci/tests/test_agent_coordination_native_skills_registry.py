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
    assert isinstance(selection, dict)
    assert selection["maximum_roles_per_task"] == 4
    assert selection["allowed_roles"] == [
        "process",
        "primary_domain",
        "security",
        "verification",
    ]

    profiles = registry["task_profiles"]
    assert isinstance(profiles, dict)
    assert set(profiles) == {
        "codebase_rationalization_audit",
        "codebase_rationalization_slice",
    }

    skills = registry["skills"]
    assert isinstance(skills, dict)
    skill = skills["nexus_codebase_rationalization"]
    assert isinstance(skill, dict)
    assert skill["enabled"] is True
    assert skill["role"] == "primary_domain"
    assert skill["execution_mode"] == "instructions_only"

    entrypoint = skill["entrypoint"]
    assert isinstance(entrypoint, str)
    skill_path = ROOT / entrypoint
    assert skill_path.is_file()


def test_native_registry_preserves_fail_closed_destructive_authority() -> None:
    registry = _load_registry()
    authority = registry["authority_order"]
    assert authority[:3] == [
        "explicit_scope_specific_user_authorization",
        "cross_cutting_legacy_registry_and_domain_owner",
        "current_nexus_main_and_issue_489",
    ]

    skill = registry["skills"]["nexus_codebase_rationalization"]
    constraints = set(skill["constraints"])
    assert {
        "no_direct_main_writes",
        "no_blind_repository_wide_delete",
        "broad_cleanup_request_does_not_override_fail_closed_domain_blockers",
        "owning_work_item_controls_blocked_runtime_scopes",
        "exact_head_evidence_required",
        "source_deletion_does_not_authorize_production_or_data_mutation",
    }.issubset(constraints)


def test_native_skill_front_matter_matches_registry() -> None:
    registry = _load_registry()
    skill = registry["skills"]["nexus_codebase_rationalization"]
    skill_path = ROOT / skill["entrypoint"]
    text = skill_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    _, front_matter, body = text.split("---\n", 2)
    metadata = yaml.safe_load(front_matter)
    assert metadata == {
        "name": "nexus-codebase-rationalization",
        "description": metadata["description"],
        "version": "1.0.0",
        "owner": "nexus_osr_engineering_governance",
        "work_item": 744,
    }
    assert isinstance(metadata["description"], str)
    assert len(metadata["description"]) >= 40
    assert "config/governance/legacy-surface-domains.v1.json" in body
    assert "A broad cleanup request does not by itself override" in body
