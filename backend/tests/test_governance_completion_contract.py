from __future__ import annotations

from pathlib import Path

from app.api.governance import router
from app.model_registry import REQUIRED_MODEL_MODULES, REPRESENTATIVE_TABLES
from app.models_governance import (
    AgentDeploymentRevision,
    CountryCatalog,
    KnowledgeImportBatch,
    KnowledgeImportDocument,
    MarketCountry,
    MarketGovernanceProfile,
    MarketLanguage,
    RoleTemplate,
    RoleTemplateAssignment,
    RoleTemplateVersion,
)


def test_governance_models_are_registered_in_the_canonical_registry() -> None:
    assert "app.models_governance" in REQUIRED_MODEL_MODULES
    assert REPRESENTATIVE_TABLES["app.models_governance"] == "role_templates"


def test_governance_schema_uses_existing_authorities() -> None:
    assert CountryCatalog.__tablename__ == "country_catalog"
    assert MarketGovernanceProfile.__tablename__ == "market_governance_profiles"
    assert MarketCountry.__tablename__ == "market_countries"
    assert MarketLanguage.__tablename__ == "market_languages"
    assert RoleTemplate.__tablename__ == "role_templates"
    assert RoleTemplateVersion.__tablename__ == "role_template_versions"
    assert RoleTemplateAssignment.__tablename__ == "role_template_assignments"
    assert KnowledgeImportBatch.__tablename__ == "knowledge_import_batches"
    assert KnowledgeImportDocument.__tablename__ == "knowledge_import_documents"
    assert AgentDeploymentRevision.__tablename__ == "agent_deployment_revisions"


def test_governance_router_is_one_bounded_control_plane() -> None:
    paths = {route.path for route in router.routes}
    required = {
        "/api/governance/capabilities",
        "/api/governance/role-templates",
        "/api/governance/role-templates/{template_id}/publish",
        "/api/governance/role-templates/{template_id}/apply/{user_id}",
        "/api/governance/countries",
        "/api/governance/markets",
        "/api/governance/knowledge-imports",
        "/api/governance/deployments/{deployment_id}/delivery",
        "/api/governance/deployments/{deployment_id}/trial/start",
        "/api/governance/deployments/{deployment_id}/trial/adjust",
        "/api/governance/deployments/{deployment_id}/trial/pause",
        "/api/governance/deployments/{deployment_id}/trial/promote",
    }
    assert required <= paths
    assert all(path.startswith("/api/governance/") for path in paths)


def test_governance_delivery_does_not_add_parallel_runtime_authorities() -> None:
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(
        (root / relative).read_text(encoding="utf-8")
        for relative in (
            "app/api/governance.py",
            "app/services/governance_service.py",
            "app/models_governance.py",
        )
    )
    for forbidden in (
        "class AgentRuntime",
        "class ToolRegistry",
        "class ProviderRouter",
        "subprocess.",
        "os.system(",
        "shell=True",
    ):
        assert forbidden not in source
    assert "activate_deployment(" in source
    assert "KnowledgeItem" in source
    assert "UserCapabilityOverride" not in source or "_apply_user_capability_overrides" in source
