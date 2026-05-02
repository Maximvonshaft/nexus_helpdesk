from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/check_model_migration_drift.py"


def test_migration_drift_script_exists_and_imports_metadata():
    assert SCRIPT.exists()
    content = SCRIPT.read_text(encoding="utf-8")
    assert "Base.metadata" in content
    assert "inspect(engine)" in content
    assert "missing_table" in content
    assert "missing_column" in content


def test_migration_drift_script_requires_postgres():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "must run against PostgreSQL" in content
    assert "settings.is_postgres" in content


def test_ignore_lists_require_reasons():
    content = SCRIPT.read_text(encoding="utf-8")
    assert "IGNORED_TABLES_WITH_REASON" in content
    assert "IGNORED_UNIQUE_CONSTRAINTS_WITH_REASON" in content
    assert "Covered by" in content or "reason" in content.lower()
