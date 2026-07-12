from __future__ import annotations

import importlib.util
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/nexus_candidate_readiness_import.db")
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app import main as main_module  # noqa: E402
from app.settings import Settings  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WARMER_PATH = ROOT / "scripts" / "smoke" / "warm_private_ai_runtime.py"
WARMER_SPEC = importlib.util.spec_from_file_location("warm_private_ai_runtime", WARMER_PATH)
assert WARMER_SPEC and WARMER_SPEC.loader
runtime_warmer = importlib.util.module_from_spec(WARMER_SPEC)
WARMER_SPEC.loader.exec_module(runtime_warmer)


class _Connection:
    def execute(self, _statement):
        return None


class _Engine:
    @contextmanager
    def connect(self):
        yield _Connection()


def _ready_dependencies(
    monkeypatch,
    *,
    observed_head: str,
    observed_heads: tuple[str, ...] | None = None,
    metadata_complete: bool = True,
) -> None:
    heads = observed_heads if observed_heads is not None else (observed_head,)
    monkeypatch.setattr(main_module, "engine", _Engine())
    monkeypatch.setattr(main_module, "_migration_revisions", lambda _conn: heads, raising=False)
    monkeypatch.setattr(main_module, "_migration_revision", lambda _conn: heads[0] if heads else None, raising=False)
    monkeypatch.setattr(
        main_module,
        "check_storage_readiness",
        lambda: SimpleNamespace(ok=True, warnings=[], as_dict=lambda: {"ok": True, "backend": "s3"}),
    )
    monkeypatch.setattr(main_module, "_frontend_readiness", lambda: {"ok": True, "active_root": "frontend_dist"})
    monkeypatch.setattr(main_module, "runtime_contract_secret_ready", lambda: {"ok": True})
    monkeypatch.setattr(
        main_module,
        "_runtime_identity",
        lambda: {
            "app_version": "rc-9ae6e9f6",
            "git_sha": "9ae6e9f6",
            "image_tag": "nexusdesk/helpdesk:rc-9ae6e9f6",
            "build_time": "20260712T220000Z",
            "frontend_build_sha": "9ae6e9f6",
            "release_metadata_source": "environment",
            "release_metadata_complete": metadata_complete,
            "release_metadata_missing": [] if metadata_complete else ["image_tag"],
        },
    )


def _response_json(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def test_fastapi_version_uses_runtime_app_version() -> None:
    assert getattr(main_module.settings, "app_version", None) == main_module.app.version
    assert main_module.app.version != "20.4.0-round-b"


def test_settings_load_candidate_identity_controls(monkeypatch) -> None:
    monkeypatch.setenv("APP_VERSION", "rc-9ae6e9f6")
    monkeypatch.setenv("EXPECTED_MIGRATION_HEAD", "20260712_0060")
    monkeypatch.setenv("READINESS_REQUIRE_RELEASE_METADATA", "true")

    settings = Settings()

    assert settings.app_version == "rc-9ae6e9f6"
    assert settings.expected_migration_head == "20260712_0060"
    assert settings.readiness_require_release_metadata is True


def test_readyz_fails_closed_on_migration_mismatch(monkeypatch) -> None:
    _ready_dependencies(monkeypatch, observed_head="20260712_0059")
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head="20260712_0060", readiness_require_release_metadata=True),
    )

    response = main_module.readyz()
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["migration"]["ok"] is False
    assert payload["migration"]["expected"] == "20260712_0060"
    assert payload["migration"]["observed"] == "20260712_0059"
    assert "migration_head_mismatch" in payload["reason_codes"]


def test_readyz_fails_closed_on_multiple_observed_migration_heads(monkeypatch) -> None:
    _ready_dependencies(
        monkeypatch,
        observed_head="20260712_0060",
        observed_heads=("20260712_0060", "unexpected_parallel_head"),
    )
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head="20260712_0060", readiness_require_release_metadata=True),
    )

    response = main_module.readyz()
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["migration"]["ok"] is False
    assert payload["migration"]["observed"] is None
    assert payload["migration"]["observed_heads"] == ["20260712_0060", "unexpected_parallel_head"]
    assert "migration_heads_multiple" in payload["reason_codes"]


def test_readyz_fails_closed_on_incomplete_release_metadata(monkeypatch) -> None:
    _ready_dependencies(monkeypatch, observed_head="20260712_0060", metadata_complete=False)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head="20260712_0060", readiness_require_release_metadata=True),
    )

    response = main_module.readyz()
    payload = _response_json(response)

    assert response.status_code == 503
    assert payload["status"] == "not_ready"
    assert payload["release_metadata_ready"] is False
    assert "release_metadata_incomplete" in payload["reason_codes"]


def test_readyz_accepts_exact_candidate_identity(monkeypatch) -> None:
    _ready_dependencies(monkeypatch, observed_head="20260712_0060")
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head="20260712_0060", readiness_require_release_metadata=True),
    )

    payload = main_module.readyz()

    assert payload["status"] == "ready"
    assert payload["migration"] == {
        "ok": True,
        "expected": "20260712_0060",
        "observed": "20260712_0060",
        "required": True,
    }
    assert payload["release_metadata_ready"] is True
    assert payload["reason_codes"] == []


def test_existing_production_metadata_gate_does_not_implicitly_require_migration_head(monkeypatch) -> None:
    _ready_dependencies(monkeypatch, observed_head="current-production-head", metadata_complete=True)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head=None, readiness_require_release_metadata=True),
    )

    payload = main_module.readyz()

    assert payload["status"] == "ready"
    assert payload["migration"] == {
        "ok": True,
        "expected": None,
        "observed": "current-production-head",
        "required": False,
    }
    assert "migration_head_required" not in payload["reason_codes"]


def test_development_readiness_can_omit_candidate_identity(monkeypatch) -> None:
    _ready_dependencies(monkeypatch, observed_head="dev-head", metadata_complete=False)
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(expected_migration_head=None, readiness_require_release_metadata=False),
    )

    payload = main_module.readyz()

    assert payload["status"] == "ready"
    assert payload["migration"]["required"] is False
    assert payload["release_metadata_ready"] is True
    assert payload["reason_codes"] == []


def _assert_warmer_has_no_external_access(monkeypatch, capsys) -> None:
    calls: list[str] = []
    monkeypatch.setattr(runtime_warmer, "_read_token", lambda: calls.append("token") or "secret")
    monkeypatch.setattr(runtime_warmer, "_post_json", lambda *_args, **_kwargs: (calls.append("post") or 1, {}))

    assert runtime_warmer.main() == 0
    assert calls == []
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "status": "disabled",
        "reason": "provider_authority_disabled",
    }


def test_runtime_warmer_skips_all_secret_and_network_access_when_authority_is_disabled(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "false")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "0")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "false")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    _assert_warmer_has_no_external_access(monkeypatch, capsys)


def test_runtime_warmer_honors_provider_kill_switch_before_secret_or_network_access(monkeypatch, capsys) -> None:
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_ENABLED", "true")
    monkeypatch.setenv("PROVIDER_RUNTIME_CANARY_PERCENT", "25")
    monkeypatch.setenv("PROVIDER_RUNTIME_KILL_SWITCH", "true")
    monkeypatch.setenv("PRIVATE_AI_RUNTIME_BASE_URL", "http://ai-runtime.internal:18081")
    _assert_warmer_has_no_external_access(monkeypatch, capsys)
