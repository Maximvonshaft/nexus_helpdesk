from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ticket_search_query_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.lite_pagination import _normalize_q  # noqa: E402


def test_ticket_search_rejects_short_query():
    with pytest.raises(HTTPException) as exc:
        _normalize_q("ab")

    assert exc.value.status_code == 400


def test_ticket_search_rejects_overlong_query():
    with pytest.raises(HTTPException) as exc:
        _normalize_q("x" * 81)

    assert exc.value.status_code == 400


def test_ticket_search_accepts_bounded_query():
    assert _normalize_q("  CS-123  ") == "CS-123"
