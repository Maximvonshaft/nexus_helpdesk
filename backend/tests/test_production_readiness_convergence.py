from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_controlled_templates_use_current_telephony_migration_head() -> None:
    for relative in (
        "deploy/.env.controlled.example",
        "deploy/.env.controlled.local-postgres.example",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "EXPECTED_MIGRATION_HEAD=20260723_tel6" in text


def test_release_authorization_is_derived_and_evidence_gated() -> None:
    service = (ROOT / "backend/app/services/release_readiness.py").read_text(
        encoding="utf-8"
    )
    assert "nexus.release-readiness.v2" in service
    assert "production_authorized = status ==" in service
    assert "PRODUCTION_E2E_EVIDENCE_URL" in service
    assert "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL" in service


def test_storage_and_activation_use_one_readiness_authority() -> None:
    script = (ROOT / "backend/scripts/validate_production_readiness.py").read_text(
        encoding="utf-8"
    )
    assert "check_storage_readiness(settings)" in script
    assert "evaluate_release_readiness(db, profile=profile)" in script
    assert "STORAGE_BACKEND is local" not in script

    router = (ROOT / "backend/app/api/release_readiness.py").read_text(
        encoding="utf-8"
    )
    registry = (ROOT / "backend/app/bootstrap/routers.py").read_text(
        encoding="utf-8"
    )
    assert "/api/admin/release-readiness" in router
    assert "release_readiness_router" in registry
    assert "/api/admin/production-readiness" in registry
    assert "/api/admin/signoff-checklist" in registry
    assert "_retire_legacy_admin_readiness_routes()" in registry


def test_production_activation_overlay_requires_evidence() -> None:
    env = (ROOT / "deploy/.env.production-activation.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy/docker-compose.production-activation.yml").read_text(
        encoding="utf-8"
    )
    assert "PRODUCTION_PROFILE=full" in env
    assert "PRODUCTION_E2E_EVIDENCE_URL=https://" in env
    assert (
        "PRODUCTION_E2E_EVIDENCE_URL: "
        "${PRODUCTION_E2E_EVIDENCE_URL:?set full production E2E evidence URL}"
    ) in compose
    assert "WEBCHAT_AI_PRODUCTION_E2E_EVIDENCE_URL" in compose
