from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_controlled_templates_use_current_telephony_migration_head() -> None:
    for relative in (
        "deploy/.env.controlled.example",
        "deploy/.env.controlled.local-postgres.example",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "EXPECTED_MIGRATION_HEAD=20260723_tel6" in text


def test_release_authorization_is_derived_by_one_evidence_policy() -> None:
    collector = (ROOT / "backend/app/services/release_readiness.py").read_text(
        encoding="utf-8"
    )
    policy = (
        ROOT / "backend/app/services/activation_evidence_policy.py"
    ).read_text(encoding="utf-8")

    assert "without granting activation authority" in collector
    assert '"production_authorized": False' in collector
    assert "PRODUCTION_E2E_EVIDENCE_URL" not in collector
    assert "production_authorized = status ==" in policy
    assert "PRODUCTION_E2E_EVIDENCE_URL" in policy
    assert "TELEPHONY_PRODUCTION_E2E_EVIDENCE_URL" in policy
    assert "ACTIVATION_EVIDENCE_SOURCE_SHA" in policy
    assert "ACTIVATION_EVIDENCE_IMAGE_DIGEST" in policy


def test_storage_and_activation_use_one_readiness_authority() -> None:
    script = (ROOT / "backend/scripts/validate_production_readiness.py").read_text(
        encoding="utf-8"
    )
    router = (ROOT / "backend/app/api/release_readiness.py").read_text(
        encoding="utf-8"
    )
    activation_preflight = (
        ROOT / "scripts/deploy/validate_production_activation.py"
    ).read_text(encoding="utf-8")
    registry = (ROOT / "backend/app/bootstrap/routers.py").read_text(
        encoding="utf-8"
    )

    assert "check_storage_readiness(settings)" in script
    assert "collect_release_readiness(db, profile=profile)" in script
    assert "finalize_release_readiness(collected)" in script
    assert "STORAGE_BACKEND is local" not in script
    assert "/api/admin/release-readiness" in router
    assert "collect_release_readiness(db, profile=profile)" in router
    assert "finalize_release_readiness(collected)" in router
    assert "activation_evidence_policy" in activation_preflight
    assert "release_readiness_router" in registry
    assert "/api/admin/production-readiness" in registry
    assert "/api/admin/signoff-checklist" in registry
    assert "_retire_legacy_admin_readiness_routes()" in registry


def test_production_activation_overlay_is_profile_specific_candidate_bound_and_enforced() -> None:
    env = (ROOT / "deploy/.env.production-activation.example").read_text(
        encoding="utf-8"
    )
    compose = (ROOT / "deploy/docker-compose.production-activation.yml").read_text(
        encoding="utf-8"
    )
    assert "PRODUCTION_PROFILE=full" in env
    assert "ACTIVATION_EVIDENCE_SOURCE_SHA=" in env
    assert "ACTIVATION_EVIDENCE_IMAGE_DIGEST=sha256:" in env
    assert "PRODUCTION_E2E_EVIDENCE_URL=https://" in env
    assert "production-activation-preflight:" in compose
    assert "network_mode: none" in compose
    assert "/app/scripts/deploy/validate_production_activation.py" in compose
    assert "- --environment" in compose
    assert (
        "ACTIVATION_EVIDENCE_SOURCE_SHA: "
        "${ACTIVATION_EVIDENCE_SOURCE_SHA:?set evidence source SHA equal to GIT_SHA}"
    ) in compose
    assert (
        "ACTIVATION_EVIDENCE_IMAGE_DIGEST: "
        "${ACTIVATION_EVIDENCE_IMAGE_DIGEST:?set evidence image digest equal to CONTROLLED_IMAGE digest}"
    ) in compose
    assert "PRODUCTION_E2E_EVIDENCE_URL: ${PRODUCTION_E2E_EVIDENCE_URL:-}" in compose
    assert "PROVIDER_CANARY_E2E_EVIDENCE_URL: ${PROVIDER_CANARY_E2E_EVIDENCE_URL:-}" in compose
    assert "production-activation-preflight:\n        condition: service_completed_successfully" in compose
