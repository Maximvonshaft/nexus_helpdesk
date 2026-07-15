from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION = Path(__file__).resolve().parents[1] / "alembic/versions/20260715_0060_webchat_country_authority.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("webchat_country_authority_migration", MIGRATION)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_country_authority_migration_is_linear_and_reversible():
    module = _load_module()
    assert module.revision == "20260715_0060"
    assert module.down_revision == "20260713_0059"
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_country_authority_migration_does_not_guess_historical_country():
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'sa.Column("country_code", sa.String(length=8), nullable=True)' in source
    assert "UPDATE webchat_public_origin_bindings" not in source
    assert '["tenant_key", "country_code", "channel_key", "is_active"]' in source
    assert '["tenant_key", "channel_key", "is_active"]' in source
