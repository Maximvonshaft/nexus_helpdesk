from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse

from app.services.provider_runtime.runtime_capabilities import (
    MAX_CAPABILITY_BYTES,
    CapabilityManifestError,
    parse_capability_manifest,
)


def _error(status_code: int, reason_code: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"reason_code": reason_code},
        headers={"Cache-Control": "no-store"},
    )


def _read_endpoint_token(token_file: Path) -> str | None:
    try:
        raw = token_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    token = raw.strip()
    if not token or len(token) > 4096 or any(char in token for char in "\r\n"):
        return None
    return token


def _read_valid_manifest(manifest_file: Path) -> dict:
    try:
        with manifest_file.open("rb") as handle:
            raw = handle.read(MAX_CAPABILITY_BYTES + 1)
    except OSError as exc:
        raise CapabilityManifestError("capability_payload_malformed") from exc
    return parse_capability_manifest(raw).to_dict()


def create_capability_router(*, manifest_file: Path, token_file: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/v1/capabilities")
    def get_capabilities(authorization: str | None = Header(default=None)):
        token = _read_endpoint_token(Path(token_file))
        if token is None:
            raise _error(503, "capability_token_unavailable")
        if authorization is None or not authorization.startswith("Bearer "):
            raise _error(401, "capability_unauthorized")
        presented = authorization.removeprefix("Bearer ")
        if not presented or not secrets.compare_digest(presented, token):
            raise _error(401, "capability_unauthorized")
        try:
            manifest = _read_valid_manifest(Path(manifest_file))
        except CapabilityManifestError:
            raise _error(503, "capability_manifest_unavailable")
        return JSONResponse(content=manifest, headers={"Cache-Control": "no-store"})

    return router
