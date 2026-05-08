from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/lite_cases_pagination_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.services.lite_pagination import (  # noqa: E402
    _decode_cursor,
    _encode_cursor,
    _normalize_q,
    _safe_limit,
)


def test_lite_limit_defaults_and_caps():
    assert _safe_limit(None) == 50
    assert _safe_limit(500) == 100
    assert _safe_limit(1) == 1


def test_lite_cursor_round_trip():
    updated_at = datetime(2026, 5, 7, 12, 30, tzinfo=timezone.utc)
    cursor = _encode_cursor(updated_at=updated_at, ticket_id=123)

    decoded_updated_at, decoded_id = _decode_cursor(cursor)

    assert decoded_updated_at == updated_at
    assert decoded_id == 123


def test_lite_invalid_cursor_returns_400():
    with pytest.raises(HTTPException) as exc:
        _decode_cursor("not-a-valid-cursor")

    assert exc.value.status_code == 400


def test_lite_q_search_bounds():
    assert _normalize_q("  abc  ") == "abc"
    with pytest.raises(HTTPException) as short_exc:
        _normalize_q("ab")
    with pytest.raises(HTTPException) as long_exc:
        _normalize_q("x" * 81)

    assert short_exc.value.status_code == 400
    assert long_exc.value.status_code == 400
