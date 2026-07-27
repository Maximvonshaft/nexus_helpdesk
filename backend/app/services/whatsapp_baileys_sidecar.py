from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol

import httpx

from ..models_whatsapp import WhatsAppConnection
from .whatsapp_runtime_settings import get_whatsapp_runtime_settings


class BaileysSidecarHttpClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> Any:
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


class BaileysSidecarError(RuntimeError):
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
class BaileysAccountSnapshot:
    account_id: str
    status: str
    authentication_state: str
    listener_state: str
    qr_status: str
    generation: int
    qr: str | None = None
    qr_data_url: str | None = None
    qr_expires_at: str | None = None
    phone_number: str | None = None
    jid: str | None = None
    last_qr_generated_at: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_inbound_at: str | None = None
    last_outbound_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    reconnect_count: int = 0

    @classmethod
    def from_payload(
        cls,
        account_id: str,
        payload: dict[str, Any],
    ) -> "BaileysAccountSnapshot":
        status = _optional(payload.get("status")) or "error"
        authentication_state = _optional(payload.get("authentication_state"))
        if not authentication_state:
            authentication_state = (
                "linked"
                if status == "connected"
                else "pending"
                if status in {"connecting", "qr_pending", "auth_persisting"}
                else "revoked"
                if status in {"logged_out", "disconnected"}
                else "error"
                if status == "error"
                else "unconfigured"
            )
        listener_state = _optional(payload.get("listener_state"))
        if not listener_state:
            listener_state = (
                "active"
                if status == "connected"
                else "reconnecting"
                if status == "reconnecting"
                else "starting"
                if status in {"connecting", "qr_pending", "auth_persisting"}
                else "error"
                if status == "error"
                else "stopped"
            )
        return cls(
            account_id=_optional(payload.get("account_id")) or account_id,
            status=status,
            authentication_state=authentication_state,
            listener_state=listener_state,
            qr_status=_optional(payload.get("qr_status")) or "none",
            generation=max(0, int(payload.get("generation") or 0)),
            qr=_optional(payload.get("qr")),
            qr_data_url=_optional(payload.get("qr_data_url")),
            qr_expires_at=_optional(payload.get("qr_expires_at")),
            phone_number=_optional(payload.get("phone_number")),
            jid=_optional(payload.get("jid")),
            last_qr_generated_at=_optional(payload.get("last_qr_generated_at")),
            last_connected_at=_optional(payload.get("last_connected_at")),
            last_disconnected_at=_optional(payload.get("last_disconnected_at")),
            last_inbound_at=_optional(payload.get("last_inbound_at")),
            last_outbound_at=_optional(payload.get("last_outbound_at")),
            last_error_code=_optional(payload.get("last_error_code")),
            last_error_message=_optional(payload.get("last_error_message")),
            reconnect_count=max(0, int(payload.get("reconnect_count") or 0)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__dataclass_fields__
        }


@dataclass(frozen=True)
class BaileysPairingCode:
    pairing_code: str
    phone_number_suffix: str
    expires_at: str | None


@dataclass(frozen=True)
class BaileysSendResult:
    provider_message_id: str
    sent_at: datetime


def call_baileys_account_action(
    connection: WhatsAppConnection,
    action: Literal["start", "status", "qr", "logout", "restart"],
    *,
    method: Literal["GET", "POST"],
    client: BaileysSidecarHttpClient | None = None,
) -> BaileysAccountSnapshot:
    account_id = _session_key(connection)
    data = _request(
        account_id,
        action,
        method=method,
        client=client,
    )
    return BaileysAccountSnapshot.from_payload(account_id, data)


def request_baileys_pairing_code(
    connection: WhatsAppConnection,
    *,
    phone_number: str,
    client: BaileysSidecarHttpClient | None = None,
) -> BaileysPairingCode:
    digits = "".join(char for char in str(phone_number or "") if char.isdigit())
    if not 8 <= len(digits) <= 16:
        raise BaileysSidecarError(
            "invalid_phone_number",
            "Pairing phone number must contain 8 to 16 digits",
            retryable=False,
        )
    data = _request(
        _session_key(connection),
        "pairing-code",
        method="POST",
        payload={"phone_number": digits},
        client=client,
    )
    pairing_code = _optional(data.get("pairing_code"))
    if data.get("ok") is not True or not pairing_code:
        raise BaileysSidecarError(
            _optional(data.get("error_code")) or "pairing_code_failed",
            _optional(data.get("error_message")) or "Unable to generate pairing code",
            retryable=bool(data.get("retryable", True)),
        )
    return BaileysPairingCode(
        pairing_code=pairing_code,
        phone_number_suffix=_optional(data.get("phone_number_suffix")) or digits[-4:],
        expires_at=_optional(data.get("expires_at")),
    )


def send_baileys_text(
    connection: WhatsAppConnection,
    *,
    target: str,
    body: str,
    idempotency_key: str,
    metadata: dict[str, Any],
    client: BaileysSidecarHttpClient | None = None,
) -> BaileysSendResult:
    data = _request(
        _session_key(connection),
        "send",
        method="POST",
        payload={
            "idempotency_key": idempotency_key,
            "target": target,
            "body": body,
            "metadata": metadata,
        },
        client=client,
    )
    if data.get("ok") is not True or str(data.get("status") or "") != "sent":
        raise BaileysSidecarError(
            _optional(data.get("error_code")) or "baileys_send_failed",
            _optional(data.get("error_message")) or "Baileys did not accept the message",
            retryable=bool(data.get("retryable", True)),
        )
    provider_message_id = _optional(data.get("provider_message_id"))
    if not provider_message_id:
        raise BaileysSidecarError(
            "baileys_send_missing_message_id",
            "Baileys accepted no visible WhatsApp message",
            retryable=True,
        )
    sent_at = _parse_datetime(data.get("sent_at"))
    return BaileysSendResult(provider_message_id=provider_message_id, sent_at=sent_at)


def _request(
    account_id: str,
    action: str,
    *,
    method: Literal["GET", "POST"],
    payload: dict[str, Any] | None = None,
    client: BaileysSidecarHttpClient | None = None,
) -> dict[str, Any]:
    settings = get_whatsapp_runtime_settings()
    if not settings.enabled:
        raise BaileysSidecarError(
            "whatsapp_disabled",
            "WhatsApp is disabled in this runtime",
            retryable=False,
        )
    token = settings.baileys_sidecar_token
    if not token:
        raise BaileysSidecarError(
            "whatsapp_baileys_sidecar_token_missing",
            "Baileys sidecar token is not configured",
            retryable=False,
        )
    url = f"{settings.baileys_sidecar_url}/accounts/{account_id}/{action}"
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        if method == "GET":
            response = active_client.get(
                url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=float(settings.transport_timeout_seconds),
            )
        else:
            response = active_client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=float(settings.transport_timeout_seconds),
            )
        return _response_json(response)
    except httpx.TimeoutException as exc:
        raise BaileysSidecarError(
            "baileys_sidecar_timeout",
            "Baileys sidecar timed out",
            retryable=True,
        ) from exc
    except httpx.HTTPError as exc:
        raise BaileysSidecarError(
            "baileys_sidecar_transport_error",
            str(exc),
            retryable=True,
        ) from exc
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()


def _response_json(response: Any) -> dict[str, Any]:
    status_code = int(getattr(response, "status_code", 200) or 200)
    try:
        data = response.json()
    except Exception as exc:
        raise BaileysSidecarError(
            "baileys_sidecar_bad_response",
            "Baileys sidecar returned non-JSON",
            retryable=status_code >= 500,
            status_code=status_code,
        ) from exc
    if status_code >= 400:
        code = _optional(data.get("error_code")) if isinstance(data, dict) else None
        message = _optional(data.get("error_message")) if isinstance(data, dict) else None
        raise BaileysSidecarError(
            code or f"baileys_sidecar_http_{status_code}",
            message or "Baileys sidecar request failed",
            retryable=status_code == 429 or status_code >= 500,
            status_code=status_code,
        )
    if not isinstance(data, dict):
        raise BaileysSidecarError(
            "baileys_sidecar_bad_response",
            "Baileys sidecar returned a non-object response",
            retryable=True,
            status_code=status_code,
        )
    return data


def _session_key(connection: WhatsAppConnection) -> str:
    if connection.transport != "baileys_sidecar":
        raise BaileysSidecarError(
            "baileys_transport_required",
            "Baileys operation received a non-Baileys connection",
            retryable=False,
        )
    key = str(connection.sidecar_session_key or "").strip()
    if not key:
        raise BaileysSidecarError(
            "baileys_session_key_required",
            "Baileys session key is not configured",
            retryable=False,
        )
    return key


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    if text:
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now().astimezone()


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
