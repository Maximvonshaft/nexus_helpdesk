from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "nexus_postgres_acceptance",
    ROOT / "scripts/qualification/postgres_acceptance.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_local_disposable_database_url_is_allowed() -> None:
    MODULE._validate_database_url(
        "postgresql+psycopg://user:password@postgres:5432/nexus_acceptance",
        allow_remote=False,
    )


def test_database_name_without_disposable_marker_is_rejected() -> None:
    with pytest.raises(ValueError, match="disposable_database_name_marker_required"):
        MODULE._validate_database_url(
            "postgresql+psycopg://user:password@postgres:5432/nexusdesk",
            allow_remote=False,
        )


def test_remote_database_is_rejected_without_explicit_confirmation() -> None:
    with pytest.raises(ValueError, match="remote_database_requires_explicit_confirmation"):
        MODULE._validate_database_url(
            "postgresql+psycopg://user:password@db.example.invalid:5432/nexus_test",
            allow_remote=False,
        )


def test_query_and_multi_host_urls_are_rejected() -> None:
    with pytest.raises(ValueError, match="postgresql_database_url_unbounded"):
        MODULE._validate_database_url(
            "postgresql://user:password@postgres:5432/nexus_test?sslmode=require",
            allow_remote=False,
        )
