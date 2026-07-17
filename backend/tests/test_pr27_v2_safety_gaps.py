from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deploy_contract_files_exist():
    for path in [
        "deploy/docker-compose.server.yml",
        "deploy/.env.prod.local-postgres.example",
        "deploy/.env.prod.external-postgres.example",
        "scripts/deploy/check_deploy_contract.sh",
    ]:
        assert (ROOT / path).exists(), path


def test_outbound_semantics_uses_canonical_module_only():
    compatibility = ROOT / "backend/app/services/outbound_message_semantics.py"
    canonical = ROOT / "backend/app/services/outbound_semantics.py"
    assert not compatibility.exists()
    content = canonical.read_text(encoding="utf-8")
    assert "EXTERNAL_OUTBOUND_CHANNELS =" in content
    assert "def count_outbound_semantics" in content


def test_webchat_rate_limit_uses_conversation_tenant():
    content = read("backend/app/api/webchat_public.py")
    assert "tenant_key=conversation.tenant_key" in content
    assert 'tenant_key="default", conversation_id=conversation_id' not in content


def test_webchat_token_expiry_exists():
    assert "visitor_token_expires_at" in read("backend/app/webchat_models.py")
    assert "WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7" in read("backend/app/services/webchat_service.py")
    assert "visitor token expired" in read("backend/app/services/webchat_service.py")
    assert "visitor_token_expires_at" in read("backend/alembic/versions/20260503_0016_webchat_token_expiry.py")
    assert "def _ensure_aware_utc" in read("backend/app/services/webchat_service.py")
