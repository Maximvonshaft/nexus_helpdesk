from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

from fastapi import HTTPException

from .storage import (
    CHUNK_SIZE,
    LocalStorageBackend,
    S3CompatibleStorageBackend,
    get_storage_backend,
)


class StorageReadError(RuntimeError):
    pass


def read_storage_bytes(storage_key: str, *, max_bytes: int) -> bytes:
    """Read one trusted storage object through the canonical backend with a hard cap."""

    key = str(storage_key or "").strip()
    if not key or len(key) > 255 or "\x00" in key:
        raise StorageReadError("storage_key_invalid")
    if max_bytes <= 0:
        raise StorageReadError("storage_read_limit_invalid")
    backend = get_storage_backend()
    if isinstance(backend, LocalStorageBackend):
        try:
            path = backend.resolve(key)
        except HTTPException as exc:
            raise StorageReadError("storage_object_missing") from exc
        return _read_local(path, max_bytes=max_bytes)
    if isinstance(backend, S3CompatibleStorageBackend):
        return _read_s3(backend, key, max_bytes=max_bytes)
    raise StorageReadError("storage_backend_read_unsupported")


def _read_local(path: Path, *, max_bytes: int) -> bytes:
    try:
        metadata = path.stat()
    except OSError as exc:
        raise StorageReadError("storage_object_missing") from exc
    if not metadata.is_file():
        raise StorageReadError("storage_object_not_file")
    if metadata.st_size <= 0 or metadata.st_size > max_bytes:
        raise StorageReadError("storage_object_size_invalid")
    try:
        with path.open("rb") as handle:
            return _read_stream(handle, max_bytes=max_bytes)
    except OSError as exc:
        raise StorageReadError("storage_object_read_failed") from exc


def _read_s3(
    backend: S3CompatibleStorageBackend,
    storage_key: str,
    *,
    max_bytes: int,
) -> bytes:
    client = backend._client()
    try:
        metadata = client.head_object(Bucket=backend.bucket, Key=storage_key)
        size = int(metadata.get("ContentLength") or 0)
        if size <= 0 or size > max_bytes:
            raise StorageReadError("storage_object_size_invalid")
        response = client.get_object(Bucket=backend.bucket, Key=storage_key)
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise StorageReadError("storage_object_read_failed")
        try:
            content = _read_stream(body, max_bytes=max_bytes)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
    except StorageReadError:
        raise
    except Exception as exc:
        if backend._is_missing(exc):
            raise StorageReadError("storage_object_missing") from exc
        raise StorageReadError("storage_object_read_failed") from exc
    return content


def _read_stream(handle: BinaryIO, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = handle.read(min(CHUNK_SIZE, max_bytes - total + 1))
        if not chunk:
            break
        if not isinstance(chunk, bytes):
            chunk = bytes(chunk)
        total += len(chunk)
        if total > max_bytes:
            raise StorageReadError("storage_object_size_invalid")
        chunks.append(chunk)
    if total <= 0:
        raise StorageReadError("storage_object_empty")
    return b"".join(chunks)
