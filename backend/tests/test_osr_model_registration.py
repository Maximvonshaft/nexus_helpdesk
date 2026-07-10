from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

from app.db import Base
from app.model_registry import REPRESENTATIVE_TABLES, REQUIRED_MODEL_MODULES, register_all_models

ROOT = Path(__file__).resolve().parents[1]


def _load_drift_module():
    path = ROOT / "scripts" / "check_model_migration_drift.py"
    spec = importlib.util.spec_from_file_location("osr_model_drift_gate", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_required_model_families_register_real_metadata_tables():
    registered = set(register_all_models())
    assert set(REQUIRED_MODEL_MODULES).issubset(registered)
    for module_name in registered:
        representative = REPRESENTATIVE_TABLES[module_name]
        assert representative in Base.metadata.tables, f"{module_name} did not register {representative}"

    case_contexts = Base.metadata.tables["case_contexts"]
    assert "ck_case_context_active_requires_identity" in {
        constraint.name for constraint in case_contexts.constraints if constraint.name
    }
    assert {
        "ix_case_contexts_is_active",
        "uq_case_context_active_conversation_only",
        "uq_case_context_active_ticket_only",
        "uq_case_context_active_conversation_ticket",
    }.issubset({index.name for index in case_contexts.indexes})


def test_alembic_and_drift_script_use_the_shared_registry():
    alembic_source = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
    drift_source = (ROOT / "scripts" / "check_model_migration_drift.py").read_text(encoding="utf-8")
    assert "from app.model_registry import register_all_models" in alembic_source
    assert "REGISTERED_MODEL_MODULES = register_all_models()" in alembic_source
    assert "from app.model_registry import REPRESENTATIVE_TABLES, register_all_models" in drift_source
    assert "REGISTERED_MODEL_MODULES = register_all_models()" in drift_source


def test_registration_drift_detects_a_deliberately_missing_table(monkeypatch):
    module = _load_drift_module()
    monkeypatch.setitem(module.REPRESENTATIVE_TABLES, "app.models_osr", "missing_osr_table_for_test")
    drift = module.metadata_registration_drift()
    assert any(item.kind == "unregistered_model_table" for item in drift)
    assert any(item.name == "app.models_osr" for item in drift)


def test_unique_contract_accepts_equivalent_non_partial_unique_index():
    module = _load_drift_module()

    class Inspector:
        def get_unique_constraints(self, table_name):
            return []

        def get_indexes(self, table_name):
            return [{
                "name": "ux_equivalent",
                "column_names": ["tenant_id", "external_key"],
                "unique": True,
                "dialect_options": {"postgresql_where": None},
            }]

    names, signatures = module._database_unique_contracts(Inspector(), "example")
    assert "ux_equivalent" in names
    assert frozenset({"tenant_id", "external_key"}) in signatures


def test_partial_unique_index_does_not_satisfy_global_unique_constraint():
    module = _load_drift_module()

    class Inspector:
        def get_unique_constraints(self, table_name):
            return []

        def get_indexes(self, table_name):
            return [{
                "name": "ux_active_only",
                "column_names": ["tenant_id", "external_key"],
                "unique": True,
                "dialect_options": {"postgresql_where": "enabled IS TRUE"},
            }]

    names, signatures = module._database_unique_contracts(Inspector(), "example")
    assert not names
    assert not signatures


def test_drift_gate_rejects_non_postgresql_database(monkeypatch, capsys):
    module = _load_drift_module()
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace(is_postgres=False))

    assert module.main() == 2
    assert "must run against PostgreSQL" in capsys.readouterr().err
