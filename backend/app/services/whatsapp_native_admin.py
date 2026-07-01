from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from ..settings import get_settings


class WhatsAppSidecarAdminClient(Protocol):
    def get(self, url: str, *, headers: dict[str, str], timeout: float) -> Any:
        ...

    def post(self, url: str, *, headers: dict[str, str], timeout: float, json: dict[str, Any] | None = None) -> Any:
        ...


@dataclass(frozen=True)
class WhatsAppNativeAccountSnapshot:
    account_id: str
    status: str
    qr_status: str
    qr: str | None = None
    qr_data_url: str | None = None
    phone_number: str | None = None
    jid: str | None = None
    last_qr_generated_at: str | None = None
    last_connected_at: str | None = None
    last_disconnected_at: str | None = None
    last_error_code: str | None = None
    last_error_message: str | None = None
    last_transport_at: str | None = None
    last_qr_expires_at: str | None = None
    session_state: str | None = None
    browser: list[str] | None = None
    reconnect_count: int = 0

    @classmethod
    def from_payload(cls, account_id: str, payload: dict[str, Any]) -> "WhatsAppNativeAccountSnapshot":
        browser = payload.get("browser")
        return cls(
            account_id=str(payload.get("account_id") or account_id),
            status=str(payload.get("status") or "unknown"),
            qr_status=str(payload.get("qr_status") or "none"),
            qr=str(payload.get("qr")) if payload.get("qr") else None,
            qr_data_url=str(payload.get("qr_data_url")) if payload.get("qr_data_url") else None,
            phone_number=str(payload.get("phone_number")) if payload.get("phone_number") else None,
            jid=str(payload.get("jid")) if payload.get("jid") else None,
            last_qr_generated_at=str(payload.get("last_qr_generated_at")) if payload.get("last_qr_generated_at") else None,
            last_connected_at=str(payload.get("last_connected_at")) if payload.get("last_connected_at") else None,
            last_disconnected_at=str(payload.get("last_disconnected_at")) if payload.get("last_disconnected_at") else None,
            last_error_code=str(payload.get("last_error_code")) if payload.get("last_error_code") else None,
            last_error_message=str(payload.get("last_error_message")) if payload.get("last_error_message") else None,
            last_transport_at=str(payload.get("last_transport_at")) if payload.get("last_transport_at") else None,
            last_qr_expires_at=str(payload.get("last_qr_expires_at")) if payload.get("last_qr_expires_at") else None,
            session_state=str(payload.get("session_state")) if payload.get("session_state") else None,
            browser=[str(item) for item in browser] if isinstance(browser, list) else None,
            reconnect_count=int(payload.get("reconnect_count") or 0),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "status": self.status,
            "qr_status": self.qr_status,
            "qr": self.qr,
            "qr_data_url": self.qr_data_url,
            "phone_number": self.phone_number,
            "jid": self.jid,
            "last_qr_generated_at": self.last_qr_generated_at,
            "last_connected_at": self.last_connected_at,
            "last_disconnected_at": self.last_disconnected_at,
            "last_error_code": self.last_error_code,
            "last_error_message": self.last_error_message,
            "last_transport_at": self.last_transport_at,
            "last_qr_expires_at": self.last_qr_expires_at,
            "session_state": self.session_state,
            "browser": self.browser,
            "reconnect_count": self.reconnect_count,
        }


@dataclass(frozen=True)
class WhatsAppNativePairingCodeResult:
    ok: bool
    account_id: str
    pairing_code: str | None = None
    phone_number_suffix: str | None = None
    error_code: str | None = None
    retryable: bool | None = None

    @classmethod
    def from_payload(cls, account_id: str, payload: dict[str, Any]) -> "WhatsAppNativePairingCodeResult":
        return cls(
            ok=payload.get("ok") is True,
            account_id=str(payload.get("account_id") or account_id),
            pairing_code=str(payload.get("pairing_code")) if payload.get("pairing_code") else None,
            phone_number_suffix=str(payload.get("phone_number_suffix")) if payload.get("phone_number_suffix") else None,
            error_code=str(payload.get("error_code")) if payload.get("error_code") else None,
            retryable=payload.get("retryable") if isinstance(payload.get("retryable"), bool) else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "account_id": self.account_id,
            "pairing_code": self.pairing_code,
            "phone_number_suffix": self.phone_number_suffix,
            "error_code": self.error_code,
            "retryable": self.retryable,
        }


def whatsapp_health_from_native_status(status: str | None) -> str:
    value = (status or "").strip().lower()
    if value == "connected":
        return "healthy"
    if value in {"connecting", "qr_pending", "reconnecting", "idle"}:
        return "degraded"
    if value in {"disconnected", "error"}:
        return "offline"
    return "unknown"


def call_whatsapp_sidecar_account_action(
    account_id: str,
    action: str,
    *,
    method: str = "POST",
    client: WhatsAppSidecarAdminClient | None = None,
) -> WhatsAppNativeAccountSnapshot:
    settings = get_settings()
    if not settings.whatsapp_native_enabled:
        raise RuntimeError("whatsapp_native_disabled")
    if not settings.whatsapp_sidecar_token:
        raise RuntimeError("whatsapp_sidecar_token_missing")
    safe_action = action.strip().lower()
    if safe_action not in {"start", "status", "qr", "logout", "restart"}:
        raise RuntimeError("unsupported_whatsapp_sidecar_action")

    url = f"{settings.whatsapp_sidecar_url}/accounts/{account_id}/{safe_action}"
    headers = {"Authorization": f"Bearer {settings.whatsapp_sidecar_token}"}
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        if method.upper() == "GET":
            response = active_client.get(url, headers=headers, timeout=float(settings.whatsapp_sidecar_timeout_seconds))
        else:
            response = active_client.post(url, headers=headers, timeout=float(settings.whatsapp_sidecar_timeout_seconds))
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()
    if not isinstance(payload, dict):
        raise RuntimeError("whatsapp_sidecar_bad_response")
    return WhatsAppNativeAccountSnapshot.from_payload(account_id, payload)


def request_whatsapp_sidecar_pairing_code(
    account_id: str,
    phone_number: str,
    *,
    client: WhatsAppSidecarAdminClient | None = None,
) -> WhatsAppNativePairingCodeResult:
    settings = get_settings()
    if not settings.whatsapp_native_enabled:
        raise RuntimeError("whatsapp_native_disabled")
    if not settings.whatsapp_sidecar_token:
        raise RuntimeError("whatsapp_sidecar_token_missing")
    digits = "".join(ch for ch in phone_number if ch.isdigit())
    if not (8 <= len(digits) <= 16):
        raise RuntimeError("invalid_phone_number")

    url = f"{settings.whatsapp_sidecar_url}/accounts/{account_id}/pairing-code"
    headers = {"Authorization": f"Bearer {settings.whatsapp_sidecar_token}"}
    active_client = client or httpx.Client()
    close_client = client is None
    try:
        response = active_client.post(
            url,
            headers=headers,
            json={"phone_number": digits},
            timeout=float(settings.whatsapp_sidecar_timeout_seconds),
        )
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        payload = response.json()
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()
    if not isinstance(payload, dict):
        raise RuntimeError("whatsapp_sidecar_bad_response")
    return WhatsAppNativePairingCodeResult.from_payload(account_id, payload)
