from __future__ import annotations

from app.services.provider_runtime.router import _harmonize_provider_timeout_ms


def test_codex_direct_timeout_budget_is_not_cut_by_outer_runtime(monkeypatch):
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", "25")
    monkeypatch.delenv("CODEX_DIRECT_ALLOW_OUTER_TIMEOUT_CAP", raising=False)

    assert _harmonize_provider_timeout_ms(primary_provider="codex_direct", timeout_ms=10000) == 26000


def test_codex_direct_timeout_budget_preserves_larger_outer_budget(monkeypatch):
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", "25")

    assert _harmonize_provider_timeout_ms(primary_provider="codex_direct", timeout_ms=40000) == 40000


def test_codex_direct_timeout_budget_allows_explicit_outer_cap(monkeypatch):
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("CODEX_DIRECT_ALLOW_OUTER_TIMEOUT_CAP", "true")

    assert _harmonize_provider_timeout_ms(primary_provider="codex_direct", timeout_ms=10000) == 10000


def test_timeout_budget_harmonization_does_not_change_other_providers(monkeypatch):
    monkeypatch.setenv("CODEX_DIRECT_TIMEOUT_SECONDS", "25")

    assert _harmonize_provider_timeout_ms(primary_provider="codex_app_server", timeout_ms=10000) == 10000
