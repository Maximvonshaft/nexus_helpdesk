from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services.storage import LocalStorageBackend


def test_persist_bytes_rejects_filename_extension_conflicting_with_mime(
    tmp_path: Path,
) -> None:
    storage = LocalStorageBackend(tmp_path)

    with pytest.raises(HTTPException) as exc_info:
        storage.persist_bytes(
            content=b"MZ" + b"\x00" * 32,
            filename="payload.exe",
            media_type="application/pdf",
            allowed_mime_types={"application/pdf"},
            allowed_extensions={".exe"},
            max_bytes=1024,
        )

    assert exc_info.value.status_code == 400
    assert "does not match MIME type" in str(exc_info.value.detail)
    assert list(tmp_path.iterdir()) == []


def test_persist_bytes_accepts_matching_known_mime_extension(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path)

    stored = storage.persist_bytes(
        content=b"%PDF-1.7\n",
        filename="customer-document.pdf",
        media_type="application/pdf",
        allowed_mime_types={"application/pdf"},
        allowed_extensions={".pdf"},
        max_bytes=1024,
    )

    assert stored.storage_key.endswith(".pdf")
    assert stored.detected_mime_type == "application/pdf"
    assert stored.absolute_path is not None
    assert stored.absolute_path.read_bytes() == b"%PDF-1.7\n"


def test_persist_bytes_keeps_json_event_contract_valid(tmp_path: Path) -> None:
    storage = LocalStorageBackend(tmp_path)

    stored = storage.persist_bytes(
        content=b'{"event":"ok"}',
        filename="telephony-event.json",
        media_type="application/json",
        allowed_mime_types={"application/json"},
        allowed_extensions={".json"},
        max_bytes=1024,
    )

    assert stored.storage_key.endswith(".json")
    assert stored.detected_mime_type == "application/json"
