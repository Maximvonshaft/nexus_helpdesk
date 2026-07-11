from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from app.db import Base
import app.model_registry as model_registry
from app.model_registry import (
    MODEL_PLUGINS,
    REPRESENTATIVE_TABLES,
    REQUIRED_MODEL_MODULES,
    ModelPlugin,
    ModelRegistryError,
    declared_model_modules,
    register_all_models,
)

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
    assert registered == set(declared_model_modules())
    assert set(REQUIRED_MODEL_MODULES).issubset(registered)
    assert "app.models_operations_dispatch" in REQUIRED_MODEL_MODULES
    assert MODEL_PLUGINS == ()
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
    assert "declared_model_modules" in drift_source
    assert "def _register_model_metadata" in drift_source
    assert "registered = tuple(register_all_models())" in drift_source
    assert "_register_model_metadata()" in drift_source


def test_missing_required_module_fails_closed(monkeypatch):
    real_import = model_registry.import_module

    def import_with_missing_dispatch(module_name: str):
        if module_name == "app.models_operations_dispatch":
            error = ModuleNotFoundError(module_name)
            error.name = module_name
            raise error
        return real_import(module_name)

    monkeypatch.setattr(model_registry, "import_module", import_with_missing_dispatch)
    with pytest.raises(ModelRegistryError, match="app.models_operations_dispatch"):
        register_all_models()


def test_registry_rejects_missing_representative_table_declaration(monkeypatch):
    monkeypatch.delitem(REPRESENTATIVE_TABLES, "app.models_operations_dispatch")
    with pytest.raises(ModelRegistryError, match="missing representative tables"):
        register_all_models()


def test_registry_rejects_stale_representative_table_declaration(monkeypatch):
    monkeypatch.setitem(REPRESENTATIVE_TABLES, "app.models_removed", "removed_table")
    with pytest.raises(ModelRegistryError, match="inactive modules"):
        register_all_models()


def test_disabled_plugin_is_explicit_and_not_loaded(monkeypatch):
    plugin = ModelPlugin(
        capability="disabled-test-plugin",
        module_name="app.models_disabled_test_plugin",
        representative_table="disabled_test_table",
        enabled=False,
    )
    monkeypatch.setattr(model_registry, "MODEL_PLUGINS", (plugin,))
    imported: list[str] = []
    real_import = model_registry.import_module

    def record_import(module_name: str):
        imported.append(module_name)
        return real_import(module_name)

    monkeypatch.setattr(model_registry, "import_module", record_import)
    assert "app.models_disabled_test_plugin" not in register_all_models()
    assert "app.models_disabled_test_plugin" not in imported


def test_enabled_plugin_is_mandatory_and_fail_closed(monkeypatch):
    plugin = ModelPlugin(
        capability="enabled-test-plugin",
        module_name="app.models_enabled_test_plugin",
        representative_table="enabled_test_table",
        enabled=True,
    )
    monkeypatch.setattr(model_registry, "MODEL_PLUGINS", (plugin,))
    monkeypatch.setitem(
        REPRESENTATIVE_TABLES,
        plugin.module_name,
        plugin.representative_table,
    )
    real_import = model_registry.import_module

    def import_with_missing_plugin(module_name: str):
        if module_name == plugin.module_name:
            error = ModuleNotFoundError(module_name)
            error.name = module_name
            raise error
        return real_import(module_name)

    monkeypatch.setattr(model_registry, "import_module", import_with_missing_plugin)
    with pytest.raises(ModelRegistryError, match=plugin.module_name):
        register_all_models()


def test_drift_module_import_does_not_register_models_eagerly():
    module = _load_drift_module()
    assert module.REGISTERED_MODEL_MODULES == ()
    assert "app.models_operations_dispatch" in module.DECLARED_MODEL_MODULES


def test_registration_drift_detects_a_deliberately_missing_module(monkeypatch):
    module = _load_drift_module()
    module._register_model_metadata()
    monkeypatch.setattr(
        module,
        "REGISTERED_MODEL_MODULES",
        tuple(
            name
            for name in module.REGISTERED_MODEL_MODULES
            if name != "app.models_operations_dispatch"
        ),
    )
    drift = module.metadata_registration_drift()
    assert any(item.kind == "missing_registered_model_module" for item in drift)
    assert any(item.name == "app.models_operations_dispatch" for item in drift)


def test_registration_drift_detects_a_deliberately_missing_table(monkeypatch):
    module = _load_drift_module()
    module._register_model_metadata()
    monkeypatch.setitem(module.REPRESENTATIVE_TABLES, "app.models_osr", "missing_osr_table_for_test")
    drift = module.metadata_registration_drift()
    assert any(item.kind == "unregistered_model_table" for item in drift)
    assert any(item.name == "app.models_osr" for item in drift)


def test_registry_failure_writes_structured_report(monkeypatch, tmp_path: Path, capsys):
    module = _load_drift_module()
    report_path = tmp_path / "model-drift.json"

    def fail_registration():
        raise ModelRegistryError("missing required test module")

    monkeypatch.setattr(module, "register_all_models", fail_registration)

    assert module.main(["--report-json", str(report_path)]) == 3
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "model_registry_error"
    assert payload["error"] == "ModelRegistryError"
    assert payload["registered_model_modules"] == []
    assert "app.models_operations_dispatch" in payload["declared_model_modules"]
    assert "required model registration failed" in capsys.readouterr().err


def test_unexpected_registry_failure_is_structured_and_attempt_local(
    monkeypatch,
    tmp_path: Path,
    capsys,
):
    module = _load_drift_module()
    report_path = tmp_path / "unexpected-model-drift.json"
    module.REGISTERED_MODEL_MODULES = ("app.models_stale_from_prior_attempt",)

    def fail_registration():
        raise RuntimeError("synthetic SQLAlchemy mapping failure")

    monkeypatch.setattr(module, "register_all_models", fail_registration)

    assert module.main(["--report-json", str(report_path)]) == 3
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["status"] == "model_registry_error"
    assert payload["error"] == "RuntimeError"
    assert payload["registered_model_modules"] == []
    assert "app.models_operations_dispatch" in payload["declared_model_modules"]
    assert "required model registration failed" in capsys.readouterr().err


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
