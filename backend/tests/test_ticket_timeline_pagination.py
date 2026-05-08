from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/ticket_timeline_pagination_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.api.ticket_perf import _encode_timeline_cursor, _item_key, _parse_cursor, _safe_limit  # noqa: E402


def _item(source_type: str, source_id: int, ts: str):
    return {"source_type": source_type, "source_id": source_id, "created_at": ts}


def test_timeline_limit_defaults_and_caps():
    assert _safe_limit(None) == 50
    assert _safe_limit(500) == 100
    assert _safe_limit(1) == 1


def test_timeline_cursor_round_trip_and_sort_key_stable():
    item = _item("comment", 42, "2026-05-07T12:00:00+00:00")
    cursor = _encode_timeline_cursor(item)
    decoded_key = _parse_cursor(cursor)

    assert decoded_key == _item_key(item)
    assert decoded_key[0] == datetime(2026, 5, 7, 12, 0, tzinfo=timezone.utc)


def test_timeline_invalid_cursor_returns_400():
    with pytest.raises(HTTPException) as exc:
        _parse_cursor("bad")

    assert exc.value.status_code == 400


def test_timeline_sort_order_distinguishes_source_and_id():
    assert _item_key(_item("comment", 1, "2026-05-07T12:00:00+00:00")) != _item_key(_item("internal_note", 2, "2026-05-07T12:00:00+00:00"))
