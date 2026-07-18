from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "backend/app/settings.py"

SPEC = importlib.util.spec_from_file_location(
    "nexus_settings_attribute_contract",
    SETTINGS_PATH,
)
assert SPEC is not None and SPEC.loader is not None
settings_module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(settings_module)


def _settings_attributes_used_by_production() -> dict[str, set[str]]:
    usage: dict[str, set[str]] = {}
    roots = (ROOT / "backend/app", ROOT / "backend/scripts")
    for source_root in roots:
        for path in sorted(source_root.rglob("*.py")):
            if path == SETTINGS_PATH or "__pycache__" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            if "get_settings" not in source or "settings" not in source:
                continue
            tree = ast.parse(source, filename=str(path))
            binds_settings = False
            for node in ast.walk(tree):
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                value = node.value
                if not (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Name)
                    and value.func.id == "get_settings"
                ):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(
                    isinstance(target, ast.Name) and target.id == "settings"
                    for target in targets
                ):
                    binds_settings = True
                    break
            if not binds_settings:
                continue
            attributes = {
                node.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "settings"
            }
            if attributes:
                usage[path.relative_to(ROOT).as_posix()] = attributes
    return usage


def test_settings_defines_every_attribute_used_by_production(tmp_path, monkeypatch):
    clean_environment = {
        "APP_ENV": "test",
        "NEXUS_PROCESS_ROLE": "unspecified",
        "DATABASE_URL": "sqlite:///" + str(tmp_path / "settings.db"),
        "UPLOAD_ROOT": str(tmp_path / "uploads"),
        "EXTERNAL_CHANNEL_TRANSPORT": "disabled",
        "EXTERNAL_CHANNEL_DEPLOYMENT_MODE": "disabled",
        "EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED": "false",
        "EXTERNAL_CHANNEL_BRIDGE_ENABLED": "false",
        "EXTERNAL_CHANNEL_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED": "false",
        "EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED": "false",
    }
    monkeypatch.setattr(os, "environ", clean_environment)
    instance = settings_module.Settings()
    usage = _settings_attributes_used_by_production()
    missing = {
        path: sorted(attribute for attribute in attributes if not hasattr(instance, attribute))
        for path, attributes in usage.items()
    }
    missing = {path: values for path, values in missing.items() if values}
    assert missing == {}


def test_settings_contract_covers_critical_runtime_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(
        os,
        "environ",
        {
            "APP_ENV": "test",
            "DATABASE_URL": "sqlite:///" + str(tmp_path / "critical.db"),
            "UPLOAD_ROOT": str(tmp_path / "uploads"),
        },
    )
    instance = settings_module.Settings()
    for attribute in (
        "process_role",
        "database_url",
        "tenant_runtime_authority_mode",
        "jwt_secret_key",
        "storage_backend",
        "upload_root",
        "enable_outbound_dispatch",
        "outbound_provider",
        "whatsapp_dispatch_mode",
        "job_batch_size",
        "job_lock_seconds",
        "webchat_ai_enabled",
        "webchat_ai_auto_reply_mode",
        "provider_runtime_enabled",
        "private_ai_runtime_enabled",
        "knowledge_embeddings_enabled",
        "webchat_tracking_fact_lookup_enabled",
        "webchat_ws_enabled",
        "runtime_contract_signing_secret",
        "metrics_enabled",
        "metrics_token",
    ):
        assert hasattr(instance, attribute), attribute
