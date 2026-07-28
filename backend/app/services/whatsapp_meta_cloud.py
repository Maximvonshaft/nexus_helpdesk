from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

import httpx

from ..models_whatsapp import WhatsAppConnection


META_GRAPH_ORIGIN = "https://graph.facebook.com"
_META_CALLBACK_MARKER = "/api/integrations/whatsapp/meta/"
_META_SHARED_CALLBACK_PATH = "/api/integrations/whatsapp/meta/webhook"


class MetaCloudHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        timeout: float,
    ) -> Any:
        ...

    def post(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict[str, Any] | None = None,
        timeout: float,
    ) -> Any:
        ...


class MetaCloudTransportError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True)
class MetaCloudProbe:
    status: str
    authentication_state: str
    listener_state: str
    phone_number: str | None
    verified_name: str | None
    quality_rating: str | None
    code_verification_status: str | None
    generation: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "authentication_state": self.authentication_state,
            "listener_state": self.listener_state,
            "phone_number": self.phone_number,
            "verified_name": self.verified_name,
            "quality_rating": self.quality_rating,
            "code_verification_status": self.code_verification_status,
            "generation": self.generation,
            "last_connected_at": datetime.now(timezone.utc),
            "last_error_code": None,
            "last_error_message": None,
        }


@dataclass(frozen=True)
class MetaCloudSendResult:
    provider_message_id: str
    sent_at: datetime


def probe_meta_cloud_connection(
    connection: WhatsAppConnection,
    *,
    access_token: str,
    client: MetaCloudHttpClient | None = None,
    timeout_seconds: float = 10.0,
) -> MetaCloudProbe:
    _require_meta_identity(connection)
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        response = active_client.get(
            _phone_number_url(connection),
            headers=_headers(access_token),
            params={
                "fields": (
                    "display_phone_number,verified_name,quality_rating,"
                    "code_verification_status"
                )
            },
            timeout=timeout_seconds,
        )
        data = _response_json(response)
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()
    if str(data.get("id") or "") != str(connection.phone_number_id):
        raise MetaCloudTransportError(
            "meta_phone_number_identity_mismatch",
            "Meta returned a different phone number identity",
            retryable=False,
        )
    code_status = _optional(data.get("code_verification_status"))
    active = not code_status or code_status.upper() in {
        "VERIFIED",
        "NOT_VERIFIED",
    }
    return MetaCloudProbe(
        status="connected" if active else "degraded",
        authentication_state="linked",
        listener_state="active",
        phone_number=_optional(data.get("display_phone_number")),
        verified_name=_optional(data.get("verified_name")),
        quality_rating=_optional(data.get("quality_rating")),
        code_verification_status=code_status,
        generation=connection.desired_generation,
    )


def shared_meta_callback_url(callback_url: str) -> str:
    """Return the one WABA-level callback authority for an application origin."""

    raw = str(callback_url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme != "https" or not parsed.netloc:
        raise MetaCloudTransportError(
            "meta_https_callback_required",
            "Meta webhook callback override must use HTTPS",
            retryable=False,
        )
    marker_index = parsed.path.find(_META_CALLBACK_MARKER)
    if marker_index < 0:
        raise MetaCloudTransportError(
            "meta_shared_callback_path_required",
            "Meta webhook callback must use the canonical integration path",
            retryable=False,
        )
    prefix = parsed.path[:marker_index].rstrip("/")
    path = f"{prefix}{_META_SHARED_CALLBACK_PATH}"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def subscribe_meta_waba(
    connection: WhatsAppConnection,
    *,
    access_token: str,
    callback_url: str | None = None,
    verify_token: str | None = None,
    client: MetaCloudHttpClient | None = None,
    timeout_seconds: float = 10.0,
) -> None:
    _require_meta_identity(connection)
    active_client = client or httpx.Client()
    close_client = client is None
    payload: dict[str, Any] | None = None
    if callback_url or verify_token:
        if not callback_url:
            raise MetaCloudTransportError(
                "meta_https_callback_required",
                "Meta webhook callback override must use HTTPS",
                retryable=False,
            )
        if not verify_token:
            raise MetaCloudTransportError(
                "meta_verify_token_required",
                "Meta webhook callback override requires a verify token",
                retryable=False,
            )
        payload = {
            "override_callback_uri": shared_meta_callback_url(callback_url),
            "verify_token": verify_token,
        }
    try:
        response = active_client.post(
            _waba_url(connection, "subscribed_apps"),
            headers=_headers(access_token),
            json=payload,
            timeout=timeout_seconds,
        )
        data = _response_json(response)
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()
    if data.get("success") is not True:
        raise MetaCloudTransportError(
            "meta_waba_subscription_failed",
            "Meta did not confirm the WABA subscription",
            retryable=True,
        )


def send_meta_cloud_text(
    connection: WhatsAppConnection,
    *,
    access_token: str,
    target: str,
    body: str,
    client: MetaCloudHttpClient | None = None,
    timeout_seconds: float = 15.0,
) -> MetaCloudSendResult:
    _require_meta_identity(connection)
    digits = "".join(char for char in str(target or "") if char.isdigit())
    if not 8 <= len(digits) <= 16:
        raise MetaCloudTransportError(
            "invalid_whatsapp_target",
            "WhatsApp target must be an E.164-compatible phone number",
            retryable=False,
        )
    text = str(body or "").strip()
    if not text:
        raise MetaCloudTransportError(
            "empty_whatsapp_message",
            "WhatsApp message body is required",
            retryable=False,
        )
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        response = active_client.post(
            _phone_number_url(connection, "messages"),
            headers=_headers(access_token),
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": digits,
                "type": "text",
                "text": {
                    "preview_url": False,
                    "body": text,
                },
            },
            timeout=timeout_seconds,
        )
        data = _response_json(response)
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()
    messages = data.get("messages") if isinstance(data.get("messages"), list) else []
    first = messages[0] if messages and isinstance(messages[0], dict) else {}
    provider_message_id = _optional(first.get("id"))
    if not provider_message_id:
        raise MetaCloudTransportError(
            "meta_send_missing_message_id",
            "Meta accepted no visible WhatsApp message",
            retryable=True,
        )
    return MetaCloudSendResult(
        provider_message_id=provider_message_id,
        sent_at=datetime.now(timezone.utc),
    )


def _headers(access_token: str) -> dict[str, str]:
    token = str(access_token or "").strip()
    if not token:
        raise MetaCloudTransportError(
            "meta_access_token_missing",
            "Meta access token is not configured",
            retryable=False,
        )
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": "NexusDesk-WhatsApp/1",
    }


def _phone_number_url(
    connection: WhatsAppConnection,
    suffix: str | None = None,
) -> str:
    base = (
        f"{META_GRAPH_ORIGIN}/{connection.graph_api_version}/"
        f"{connection.phone_number_id}"
    )
    return f"{base}/{suffix}" if suffix else base


def _waba_url(connection: WhatsAppConnection, suffix: str) -> str:
    return (
        f"{META_GRAPH_ORIGIN}/{connection.graph_api_version}/"
        f"{connection.waba_id}/{suffix}"
    )


def _require_meta_identity(connection: WhatsAppConnection) -> None:
    if connection.transport != "meta_cloud_api":
        raise MetaCloudTransportError(
            "meta_transport_required",
            "Meta transport operation received a non-Meta connection",
            retryable=False,
        )
    if not connection.graph_api_version or not connection.phone_number_id or not connection.waba_id:
        raise MetaCloudTransportError(
            "meta_cloud_configuration_missing",
            "Meta WABA, phone number and Graph API version are required",
            retryable=False,
        )


def _response_json(response: Any) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 200) or 200)
    try:
        data = response.json()
    except Exception as exc:
        raise MetaCloudTransportError(
            "meta_bad_response",
            "Meta returned a non-JSON response",
            retryable=status_code >= 500,
            status_code=status_code,
        ) from exc
    if status_code >= 400:
        error = data.get("error") if isinstance(data, dict) and isinstance(data.get("error"), dict) else {}
        code = _optional(error.get("code")) or f"http_{status_code}"
        message = _optional(error.get("message")) or "Meta Cloud API request failed"
        raise MetaCloudTransportError(
            f"meta_{code}",
            message,
            retryable=status_code == 429 or status_code >= 500,
            status_code=status_code,
        )
    if not isinstance(data, dict):
        raise MetaCloudTransportError(
            "meta_bad_response",
            "Meta returned a non-object response",
            retryable=True,
            status_code=status_code,
        )
    return data


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
