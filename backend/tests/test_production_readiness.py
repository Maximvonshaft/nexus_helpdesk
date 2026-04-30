import os
import sys
from pathlib import Path

os.environ.setdefault('APP_ENV', 'development')
os.environ.setdefault('DATABASE_URL', 'sqlite:////tmp/nexusdesk_readiness.db')

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.readiness_service import evaluate_production_readiness  # noqa: E402


def test_readiness_payload_has_required_keys():
    payload = evaluate_production_readiness(db=None)
    assert payload['status'] in {'ready', 'not_ready'}
    assert 'checks' in payload
    assert 'warnings' in payload
    assert 'failures' in payload
    for key in [
        'postgres_configured',
        'secret_key_configured',
        'allowed_origins_configured',
        'webchat_origins_configured',
        'legacy_webchat_token_disabled',
        'cli_fallback_disabled',
        'openclaw_mode_valid',
    ]:
        assert key in payload['checks']


def test_development_sqlite_is_warning_not_exception():
    payload = evaluate_production_readiness(db=None)
    assert isinstance(payload['warnings'], list)
    assert isinstance(payload['failures'], list)
