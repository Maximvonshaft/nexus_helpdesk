from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import httpx

from ..models_whatsapp import WhatsAppConnection
from .whatsapp_meta_cloud import MetaCloudTransportError


_GRAPH_VERSION_RE = re.compile(r"^v[0-9]{1,2}\.[0-9]{1,2}$")
_META_OBJECT_ID_RE = re.compile(r"^[0-9]{5,32}$")
_MEDIA_KINDS = {"image", "video", "audio", "document", "sticker"}


class MetaMediaHttpClient(Protocol):
    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        data: dict[str, str] | None = None,
        files: dict[str, tuple[str, bytes, str]] | None = None,
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> Any:
        ...


@dataclass(frozen=True)
class MetaMediaSendResult:
    provider_media_id: str
    provider_message_id: str
    sent_at: datetime


def send_meta_cloud_media(
    connection: WhatsAppConnection,
    *,
    access_token: str,
    target: str,
    content: bytes,
    media_kind: str,
    media_type: str,
    filename: str,
    caption: str | None,
    client: MetaMediaHttpClient | None = None,
) -> MetaMediaSendResult:
    version, phone_number_id = _identity(connection)
    kind = str(media_kind or "").strip().lower()
    if kind not in _MEDIA_KINDS:
        raise MetaCloudTransportError(
            "unsupported_whatsapp_media_kind",
            "Unsupported Meta WhatsApp media kind",
            retryable=False,
        )
    if not content:
        raise MetaCloudTransportError(
            "empty_media_content",
            "WhatsApp media content is empty",
            retryable=False,
        )
    digits = "".join(char for char in str(target or "") if char.isdigit())
    if not 8 <= len(digits) <= 16:
        raise MetaCloudTransportError(
            "invalid_whatsapp_target",
            "WhatsApp target must be an E.164-compatible phone number",
            retryable=False,
        )
    mime = str(media_type or "application/octet-stream").split(";", 1)[0].strip().lower()
    safe_filename = str(filename or f"whatsapp-{kind}").replace("\\", "/").rsplit("/", 1)[-1][:255]
    active_client = client or httpx.Client(follow_redirects=False, trust_env=False)
    close_client = client is None
    try:
        upload_response = active_client.post(
            _endpoint(version, phone_number_id, "media"),
            headers=_auth_headers(access_token),
            data={"messaging_product": "whatsapp", "type": mime},
            files={"file": (safe_filename, content, mime)},
            timeout=60.0,
        )
        upload_payload = _response_json(upload_response, "meta_media_upload_failed")
        provider_media_id = str(upload_payload.get("id") or "").strip()
        if not provider_media_id:
            raise MetaCloudTransportError(
                "meta_media_upload_missing_id",
                "Meta returned no uploaded media identity",
                retryable=True,
            )
        media_object: dict[str, Any] = {"id": provider_media_id}
        normalized_caption = str(caption or "").strip()
        if normalized_caption and kind in {"image", "video", "document"}:
            media_object["caption"] = normalized_caption[:1024]
        if kind == "document":
            media_object["filename"] = safe_filename
        send_response = active_client.post(
            _endpoint(version, phone_number_id, "messages"),
            headers={**_auth_headers(access_token), "Content-Type": "application/json"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits,
                "type": kind,
                kind: media_object,
            },
            timeout=30.0,
        )
        send_payload = _response_json(send_response, "meta_media_send_failed")
        messages = send_payload.get("messages") if isinstance(send_payload.get("messages"), list) else []
        first = messages[0] if messages and isinstance(messages[0], dict) else {}
        provider_message_id = str(first.get("id") or "").strip()
        if not provider_message_id:
            raise MetaCloudTransportError(
                "meta_media_send_missing_message_id",
                "Meta accepted no visible media message",
                retryable=True,
            )
        return MetaMediaSendResult(
            provider_media_id=provider_media_id,
            provider_message_id=provider_message_id,
            sent_at=datetime.now(timezone.utc),
        )
    except httpx.TimeoutException as exc:
        raise MetaCloudTransportError(
            "meta_media_timeout",
            "Meta media operation timed out",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise MetaCloudTransportError(
            "meta_media_transport_error",
            "Meta media transport failed",
            retryable=True,
        ) from exc
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()


def _identity(connection: WhatsAppConnection) -> tuple[str, str]:
    if connection.transport != "meta_cloud_api":
        raise MetaCloudTransportError(
            "meta_transport_required",
            "Meta media operation received a non-Meta connection",
            retryable=False,
        )
    version = str(connection.graph_api_version or "").strip()
    phone_number_id = str(connection.phone_number_id or "").strip()
    if not _GRAPH_VERSION_RE.fullmatch(version) or not _META_OBJECT_ID_RE.fullmatch(phone_number_id):
        raise MetaCloudTransportError(
            "meta_cloud_configuration_missing",
            "Meta phone number and Graph API version are invalid",
            retryable=False,
        )
    return version, phone_number_id


def _endpoint(version: str, phone_number_id: str, suffix: str) -> str:
    if suffix not in {"media", "messages"}:
        raise MetaCloudTransportError(
            "meta_endpoint_invalid",
            "Meta endpoint is not allowed",
            retryable=False,
        )
    return f"https://graph.facebook.com/{version}/{phone_number_id}/{suffix}"


def _auth_headers(access_token: str) -> dict[str, str]:
    token = str(access_token or "").strip()
    if not token:
        raise MetaCloudTransportError(
            "meta_access_token_missing",
            "Meta access token is not configured",
            retryable=False,
        )
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "NexusDesk-WhatsApp/1",
    }


def _response_json(response: Any, failure_code: str) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 0) or 0)
    try:
        payload = response.json()
    except Exception as exc:
        raise MetaCloudTransportError(
            failure_code,
            "Meta returned a non-JSON response",
            retryable=status_code >= 500,
            status_code=status_code,
        ) from exc
    if status_code >= 400 or not isinstance(payload, dict) or payload.get("error"):
        error = payload.get("error") if isinstance(payload, dict) and isinstance(payload.get("error"), dict) else {}
        message = str(error.get("message") or failure_code)[:1000]
        raise MetaCloudTransportError(
            failure_code,
            message,
            retryable=status_code == 429 or status_code >= 500,
            status_code=status_code,
        )
    return payload
