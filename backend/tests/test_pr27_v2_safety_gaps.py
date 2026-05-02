from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")

def test_deploy_contract_files_exist():
    for path in [
        "deploy/docker-compose.server.local-postgres.yml",
        "deploy/docker-compose.server.external-postgres.yml",
        "deploy/.env.prod.local-postgres.example",
        "deploy/.env.prod.external-postgres.example",
        "scripts/deploy/check_deploy_contract.sh",
    ]:
        assert (ROOT / path).exists(), path

def test_outbound_semantics_reexport_only():
    content = read("backend/app/services/outbound_message_semantics.py")
    assert "Compatibility re-export only" in content
    assert "from .outbound_semantics import *" in content
    assert "EXTERNAL_OUTBOUND_CHANNELS =" not in content

def test_webchat_rate_limit_uses_conversation_tenant():
    content = read("backend/app/api/webchat.py")
    assert "tenant_key=conversation.tenant_key" in content
    assert 'tenant_key="default", conversation_id=conversation_id' not in content

def test_webchat_token_expiry_exists():
    assert "visitor_token_expires_at" in read("backend/app/webchat_models.py")
    assert "WEBCHAT_VISITOR_TOKEN_TTL_DAYS = 7" in read("backend/app/services/webchat_service.py")
    assert "visitor token expired" in read("backend/app/services/webchat_service.py")
    assert "visitor_token_expires_at" in read("backend/alembic/versions/20260503_0016_webchat_token_expiry.py")
    assert "def _ensure_aware_utc" in read("backend/app/services/webchat_service.py")
