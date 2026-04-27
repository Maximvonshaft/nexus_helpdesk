from __future__ import annotations

import mimetypes
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastapi import HTTPException, UploadFile

from ..settings import get_settings

settings = get_settings()
CHUNK_SIZE = 1024 * 1024


@dataclass
class StoredFile:
    storage_key: str
    absolute_path: Path | None
    size_bytes: int
    detected_mime_type: str


class StorageBackend(Protocol):
    def save_upload(self, file: UploadFile, *, allowed_mime_types: set[str], allowed_extensions: set[str], max_bytes: int) -> StoredFile: ...
    def persist_bytes(self, *, content: bytes, filename: str, media_type: str, allowed_mime_types: set[str] | None = None, allowed_extensions: set[str] | None = None, max_bytes: int | None = None) -> StoredFile: ...
    def resolve(self, storage_key: str) -> Path: ...
    def download_url(self, storage_key: str, *, filename: str | None = None, media_type: str | None = None) -> str | None: ...


def _validate_persist_bytes_inputs(*, content: bytes, filename: str, media_type: str, allowed_mime_types: set[str] | None, allowed_extensions: set[str] | None, max_bytes: int | None) -> tuple[str, str]:
    suffix = Path(filename or 'attachment.bin').suffix.lower() or '.bin'
    normalized_media_type = (media_type or 'application/octet-stream').split(';', 1)[0].strip().lower()
    if max_bytes is not None and len(content) > max_bytes:
        raise HTTPException(status_code=413, detail='Persisted attachment exceeds configured size limit')
    if allowed_extensions is not None and suffix not in allowed_extensions and suffix not in {'.bin', '.json', '.txt'}:
        raise HTTPException(status_code=400, detail=f"File extension '{suffix}' is not allowed")
    if allowed_mime_types is not None and normalized_media_type not in allowed_mime_types:
        raise HTTPException(status_code=400, detail=f"MIME type '{normalized_media_type}' is not allowed")
    return suffix, normalized_media_type


class LocalStorageBackend:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _sniff_mime(self, sample: bytes, suffix: str, declared: str) -> str:
        declared = (declared or 'application/octet-stream').lower()
        if sample.startswith(b'%PDF-'):
            return 'application/pdf'
        if sample.startswith(b'\x89PNG\r\n\x1a\n'):
            return 'image/png'
        if sample.startswith(b'\xff\xd8\xff'):
            return 'image/jpeg'
        if len(sample) >= 12 and sample[:4] == b'RIFF' and sample[8:12] == b'WEBP':
            return 'image/webp'
        if suffix == '.txt':
            try:
                sample.decode('utf-8')
                return 'text/plain'
            except UnicodeDecodeError as exc:
                raise HTTPException(status_code=400, detail='Uploaded text file is not valid UTF-8') from exc
        guessed, _ = mimetypes.guess_type(f'file{suffix}')
        return (guessed or declared).lower()

    def save_upload(self, file: UploadFile, *, allowed_mime_types: set[str], allowed_extensions: set[str], max_bytes: int) -> StoredFile:
        suffix = Path(file.filename or 'upload.bin').suffix.lower()
        if suffix not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"File extension '{suffix or '[none]'}' is not allowed")
        storage_key = f"{uuid.uuid4().hex}{suffix}"
        absolute_path = (self.root / storage_key).resolve()
        try:
            absolute_path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=500, detail='Resolved storage path escaped storage root') from exc

        total = 0
        sample = b''
        try:
            with open(absolute_path, 'wb') as handle:
                while True:
                    chunk = file.file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if not sample:
                        sample = chunk[:512]
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail='Uploaded file exceeds MAX_UPLOAD_BYTES')
                    handle.write(chunk)
        except Exception:
            absolute_path.unlink(missing_ok=True)
            raise

        detected_mime = self._sniff_mime(sample, suffix, file.content_type or 'application/octet-stream')
        if detected_mime not in allowed_mime_types:
            absolute_path.unlink(missing_ok=True)
            raise HTTPException(status_code=400, detail=f"Detected MIME type '{detected_mime}' is not allowed")
        return StoredFile(storage_key=storage_key, absolute_path=absolute_path, size_bytes=total, detected_mime_type=detected_mime)

    def persist_bytes(self, *, content: bytes, filename: str, media_type: str, allowed_mime_types: set[str] | None = None, allowed_extensions: set[str] | None = None, max_bytes: int | None = None) -> StoredFile:
        suffix, detected_mime = _validate_persist_bytes_inputs(
            content=content,
            filename=filename,
            media_type=media_type,
            allowed_mime_types=allowed_mime_types,
            allowed_extensions=allowed_extensions,
            max_bytes=max_bytes,
        )
        storage_key = f"{uuid.uuid4().hex}{suffix}"
        absolute_path = (self.root / storage_key).resolve()
        try:
            absolute_path.relative_to(self.root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=500, detail='Resolved storage path escaped storage root') from exc
        absolute_path.write_bytes(content)
        return StoredFile(storage_key=storage_key, absolute_path=absolute_path, size_bytes=len(content), detected_mime_type=detected_mime)

    def resolve(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=403, detail='Attachment path is outside storage root') from exc
        if not candidate.exists() or not candidate.is_file():
            raise HTTPException(status_code=404, detail='Attachment file is missing')
        return candidate

    def download_url(self, storage_key: str, *, filename: str | None = None, media_type: str | None = None) -> str | None:
        return None


class S3CompatibleStorageBackend:
    def __init__(self, *, bucket: str, endpoint_url: str | None, region: str | None, access_key: str | None, secret_key: str | None, presign_expiry_seconds: int, temp_root: Path):
        self.bucket = bucket
        self.endpoint_url = endpoint_url
        self.region = region
        self.access_key = access_key
        self.secret_key = secret_key
        self.presign_expiry_seconds = presign_expiry_seconds
        self.temp_root = temp_root
        self.temp_root.mkdir(parents=True, exist_ok=True)

    def _client(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("boto3 is required for STORAGE_BACKEND=s3") from exc
        kwargs = {"service_name": "s3", "endpoint_url": self.endpoint_url, "region_name": self.region}
        if self.access_key and self.secret_key:
            kwargs["aws_access_key_id"] = self.access_key
            kwargs["aws_secret_access_key"] = self.secret_key
        return boto3.client(**kwargs)

    def _sniff_mime(self, sample: bytes, suffix: str, declared: str) -> str:
        return LocalStorageBackend(self.temp_root)._sniff_mime(sample, suffix, declared)

    def save_upload(self, file: UploadFile, *, allowed_mime_types: set[str], allowed_extensions: set[str], max_bytes: int) -> StoredFile:
        suffix = Path(file.filename or 'upload.bin').suffix.lower()
        if suffix not in allowed_extensions:
            raise HTTPException(status_code=400, detail=f"File extension '{suffix or '[none]'}' is not allowed")
        storage_key = f"{uuid.uuid4().hex}{suffix}"
        total = 0
        sample = b''
        tmp_file = tempfile.NamedTemporaryFile(prefix='nexusdesk-', suffix=suffix, dir=self.temp_root, delete=False)
        tmp_path = Path(tmp_file.name)
        try:
            with tmp_file as handle:
                while True:
                    chunk = file.file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if not sample:
                        sample = chunk[:512]
                    total += len(chunk)
                    if total > max_bytes:
                        raise HTTPException(status_code=413, detail='Uploaded file exceeds MAX_UPLOAD_BYTES')
                    handle.write(chunk)
            detected_mime = self._sniff_mime(sample, suffix, file.content_type or 'application/octet-stream')
            if detected_mime not in allowed_mime_types:
                raise HTTPException(status_code=400, detail=f"Detected MIME type '{detected_mime}' is not allowed")
            client = self._client()
            client.upload_file(str(tmp_path), self.bucket, storage_key, ExtraArgs={"ContentType": detected_mime})
            return StoredFile(storage_key=storage_key, absolute_path=None, size_bytes=total, detected_mime_type=detected_mime)
        finally:
            tmp_path.unlink(missing_ok=True)

    def persist_bytes(self, *, content: bytes, filename: str, media_type: str, allowed_mime_types: set[str] | None = None, allowed_extensions: set[str] | None = None, max_bytes: int | None = None) -> StoredFile:
        suffix, detected_mime = _validate_persist_bytes_inputs(
            content=content,
            filename=filename,
            media_type=media_type,
            allowed_mime_types=allowed_mime_types,
            allowed_extensions=allowed_extensions,
            max_bytes=max_bytes,
        )
        storage_key = f"{uuid.uuid4().hex}{suffix}"
        client = self._client()
        client.put_object(Bucket=self.bucket, Key=storage_key, Body=content, ContentType=detected_mime)
        return StoredFile(storage_key=storage_key, absolute_path=None, size_bytes=len(content), detected_mime_type=detected_mime)

    def resolve(self, storage_key: str) -> Path:
        raise HTTPException(status_code=501, detail='Direct file resolution is not available for remote storage')

    def download_url(self, storage_key: str, *, filename: str | None = None, media_type: str | None = None) -> str | None:
        client = self._client()
        params = {"Bucket": self.bucket, "Key": storage_key}
        if filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
        if media_type:
            params["ResponseContentType"] = media_type
        return client.generate_presigned_url("get_object", Params=params, ExpiresIn=self.presign_expiry_seconds)


def get_storage_backend() -> StorageBackend:
    if settings.storage_backend == 'local':
        return LocalStorageBackend(settings.upload_root)
    if settings.storage_backend == 's3':
        return S3CompatibleStorageBackend(
            bucket=settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            presign_expiry_seconds=settings.s3_presign_expiry_seconds,
            temp_root=settings.upload_root / ".tmp",
        )
    raise RuntimeError(f"Unsupported STORAGE_BACKEND '{settings.storage_backend}'. Supported values: 'local', 's3'")
