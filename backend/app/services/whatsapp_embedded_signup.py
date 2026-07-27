from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy.orm import Session

from ..models_whatsapp import WhatsAppEmbeddedSignupSession
from ..utils.time import utc_now
from .whatsapp_embedded_signup_settings import (
    WhatsAppEmbeddedSignupSettings,
    get_whatsapp_embedded_signup_settings,
)


_REQUIRED_SCOPES = {
    "business_management",
    "whatsapp_business_management",
    "whatsapp_business_messaging",
}
_GRAPH_ENDPOINT_SUFFIXES: dict[str, str] = {
    "oauth": "oauth/access_token",
    "debug": "debug_token",
    "objects": "",
}
_GRAPH_VERSION_RE = re.compile(r"^v[0-9]{1,2}\.[0-9]{1,2}$")
_META_OBJECT_ID_RE = re.compile(r"^[0-9]{5,32}$")


class MetaSignupHttpClient(Protocol):
    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: float,
    ) -> Any:
        ...


class EmbeddedSignupError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class EmbeddedSignupAccountIntent:
    display_name: str
    account_id: str
    market_id: int | None
    priority: int


@dataclass(frozen=True)
class EmbeddedSignupPublicSession:
    session_id: str
    state: str
    expires_at: object
    app_id: str
    configuration_id: str
    graph_api_version: str
    allowed_origin: str


@dataclass(frozen=True)
class MetaSignupAssets:
    access_token: str
    business_account_id: str | None
    waba_id: str
    phone_number_id: str
    display_phone_number: str | None
    verified_name: str | None


def start_embedded_signup_session(
    db: Session,
    *,
    tenant_id: int,
    requested_by: int,
    intent: EmbeddedSignupAccountIntent,
) -> EmbeddedSignupPublicSession:
    settings = _settings()
    session_id = uuid.uuid4().hex
    state = _encode_state(
        settings,
        session_id=session_id,
        tenant_id=tenant_id,
        requested_by=requested_by,
        intent=intent,
    )
    row = WhatsAppEmbeddedSignupSession(
        id=session_id,
        tenant_id=tenant_id,
        requested_by=requested_by,
        state_digest=_digest(state),
        status="pending",
        expires_at=utc_now() + timedelta(seconds=settings.session_ttl_seconds),
    )
    db.add(row)
    db.flush()
    return EmbeddedSignupPublicSession(
        session_id=row.id,
        state=state,
        expires_at=row.expires_at,
        app_id=str(settings.app_id),
        configuration_id=str(settings.configuration_id),
        graph_api_version=str(settings.graph_api_version),
        allowed_origin=str(settings.allowed_origin),
    )


def require_pending_signup_session(
    db: Session,
    *,
    session_id: str,
    tenant_id: int,
    requested_by: int,
    state: str,
    intent: EmbeddedSignupAccountIntent,
) -> WhatsAppEmbeddedSignupSession:
    row = (
        db.query(WhatsAppEmbeddedSignupSession)
        .filter(
            WhatsAppEmbeddedSignupSession.id == session_id,
            WhatsAppEmbeddedSignupSession.tenant_id == tenant_id,
            WhatsAppEmbeddedSignupSession.requested_by == requested_by,
        )
        .first()
    )
    if row is None:
        raise EmbeddedSignupError("embedded_signup_session_not_found")
    if row.status != "pending":
        raise EmbeddedSignupError("embedded_signup_session_not_pending")
    if row.expires_at <= utc_now():
        row.status = "expired"
        row.updated_at = utc_now()
        db.flush()
        raise EmbeddedSignupError("embedded_signup_session_expired")
    supplied = _digest(state)
    if not hmac.compare_digest(supplied, row.state_digest):
        raise EmbeddedSignupError("embedded_signup_state_invalid")
    payload = _decode_state(_settings(), state)
    expected = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "requested_by": requested_by,
        "display_name": intent.display_name,
        "account_id": intent.account_id,
        "market_id": intent.market_id,
        "priority": intent.priority,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise EmbeddedSignupError("embedded_signup_intent_mismatch")
    return row


def exchange_and_validate_signup(
    *,
    code: str,
    waba_id: str,
    phone_number_id: str,
    business_account_id: str | None,
    client: MetaSignupHttpClient | None = None,
) -> MetaSignupAssets:
    settings = _settings()
    normalized_waba_id = _meta_object_id(waba_id, "embedded_signup_waba_id_invalid")
    normalized_phone_number_id = _meta_object_id(
        phone_number_id,
        "embedded_signup_phone_number_id_invalid",
    )
    normalized_business_account_id = (
        _meta_object_id(
            business_account_id,
            "embedded_signup_business_account_id_invalid",
        )
        if business_account_id
        else None
    )
    active_client = client or httpx.Client(
        follow_redirects=False,
        trust_env=False,
    )
    close_client = client is None
    try:
        token_payload = _get_json(
            active_client,
            settings=settings,
            endpoint="oauth",
            params={
                "client_id": str(settings.app_id),
                "client_secret": str(settings.app_secret),
                "code": code,
            },
            failure_code="embedded_signup_code_exchange_failed",
        )
        access_token = str(token_payload.get("access_token") or "").strip()
        if not access_token:
            raise EmbeddedSignupError("embedded_signup_access_token_missing")
        debug_payload = _get_json(
            active_client,
            settings=settings,
            endpoint="debug",
            params={
                "input_token": access_token,
                "access_token": f"{settings.app_id}|{settings.app_secret}",
            },
            failure_code="embedded_signup_token_debug_failed",
        )
        debug_data = debug_payload.get("data")
        if not isinstance(debug_data, dict):
            raise EmbeddedSignupError("embedded_signup_token_debug_invalid")
        if debug_data.get("is_valid") is not True:
            raise EmbeddedSignupError("embedded_signup_token_invalid")
        if str(debug_data.get("app_id") or "") != str(settings.app_id):
            raise EmbeddedSignupError("embedded_signup_app_id_mismatch")
        scopes = {
            str(scope).strip()
            for scope in (debug_data.get("scopes") or [])
            if str(scope).strip()
        }
        if not _REQUIRED_SCOPES.issubset(scopes):
            raise EmbeddedSignupError("embedded_signup_required_scopes_missing")

        object_payload = _get_json(
            active_client,
            settings=settings,
            endpoint="objects",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "ids": normalized_waba_id,
                "fields": (
                    "id,name,owner_business_info,"
                    "phone_numbers.limit(100){"
                    "id,display_phone_number,verified_name,"
                    "code_verification_status,quality_rating,platform_type}"
                ),
            },
            failure_code="embedded_signup_asset_lookup_failed",
        )
        waba_payload = object_payload.get(normalized_waba_id)
        if not isinstance(waba_payload, dict):
            raise EmbeddedSignupError("embedded_signup_waba_lookup_invalid")
        if str(waba_payload.get("id") or "") != normalized_waba_id:
            raise EmbeddedSignupError("embedded_signup_waba_mismatch")
        phone_container = waba_payload.get("phone_numbers")
        entries = (
            phone_container.get("data")
            if isinstance(phone_container, dict)
            else None
        )
        if not isinstance(entries, list):
            raise EmbeddedSignupError("embedded_signup_phone_lookup_invalid")
        selected = next(
            (
                item
                for item in entries
                if isinstance(item, dict)
                and str(item.get("id") or "") == normalized_phone_number_id
            ),
            None,
        )
        if selected is None:
            raise EmbeddedSignupError("embedded_signup_phone_number_mismatch")
        observed_business = _owner_business_id(waba_payload)
        if (
            normalized_business_account_id
            and observed_business
            and normalized_business_account_id != observed_business
        ):
            raise EmbeddedSignupError("embedded_signup_business_account_mismatch")
        return MetaSignupAssets(
            access_token=access_token,
            business_account_id=(
                normalized_business_account_id or observed_business
            ),
            waba_id=normalized_waba_id,
            phone_number_id=normalized_phone_number_id,
            display_phone_number=_optional(selected.get("display_phone_number")),
            verified_name=_optional(selected.get("verified_name")),
        )
    except EmbeddedSignupError:
        raise
    except httpx.HTTPError as exc:
        raise EmbeddedSignupError(
            "embedded_signup_meta_transport_error",
            retryable=True,
        ) from exc
    finally:
        if close_client and hasattr(active_client, "close"):
            active_client.close()


def mark_signup_exchanging(
    session: WhatsAppEmbeddedSignupSession,
    *,
    code: str,
    business_account_id: str | None,
    waba_id: str,
    phone_number_id: str,
) -> None:
    session.status = "exchanging"
    session.code_fingerprint = hashlib.sha256(code.encode("utf-8")).hexdigest()
    session.business_account_id = business_account_id
    session.waba_id = waba_id
    session.phone_number_id = phone_number_id
    session.last_error_code = None
    session.updated_at = utc_now()


def mark_signup_completed(
    session: WhatsAppEmbeddedSignupSession,
    *,
    connection_id: int,
) -> None:
    session.status = "completed"
    session.connection_id = connection_id
    session.completed_at = utc_now()
    session.last_error_code = None
    session.updated_at = utc_now()


def mark_signup_failed(
    session: WhatsAppEmbeddedSignupSession,
    *,
    code: str,
) -> None:
    session.status = "failed"
    session.last_error_code = code[:120]
    session.updated_at = utc_now()


def _settings() -> WhatsAppEmbeddedSignupSettings:
    try:
        settings = get_whatsapp_embedded_signup_settings()
    except RuntimeError as exc:
        raise EmbeddedSignupError("embedded_signup_runtime_invalid") from exc
    if not settings.enabled:
        raise EmbeddedSignupError("embedded_signup_disabled")
    return settings


def _get_json(
    client: MetaSignupHttpClient,
    *,
    settings: WhatsAppEmbeddedSignupSettings,
    endpoint: Literal["oauth", "debug", "objects"],
    failure_code: str,
    headers: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> dict[str, Any]:
    response = client.get(
        _graph_endpoint(settings, endpoint),
        headers=headers,
        params=params,
        timeout=20.0,
    )
    status_code = int(getattr(response, "status_code", 0) or 0)
    if status_code != 200:
        raise EmbeddedSignupError(
            failure_code,
            retryable=status_code == 429 or status_code >= 500,
        )
    try:
        payload = response.json()
    except Exception as exc:
        raise EmbeddedSignupError(failure_code, retryable=True) from exc
    if not isinstance(payload, dict) or payload.get("error"):
        raise EmbeddedSignupError(failure_code, retryable=False)
    return payload


def _graph_endpoint(
    settings: WhatsAppEmbeddedSignupSettings,
    endpoint: Literal["oauth", "debug", "objects"],
) -> str:
    version = str(settings.graph_api_version or "").strip()
    if not _GRAPH_VERSION_RE.fullmatch(version):
        raise EmbeddedSignupError("embedded_signup_graph_version_invalid")
    suffix = _GRAPH_ENDPOINT_SUFFIXES[endpoint]
    root = f"https://graph.facebook.com/{version}/"
    return root + suffix


def _meta_object_id(value: str, error_code: str) -> str:
    normalized = str(value or "").strip()
    if not _META_OBJECT_ID_RE.fullmatch(normalized):
        raise EmbeddedSignupError(error_code)
    return normalized


def _encode_state(
    settings: WhatsAppEmbeddedSignupSettings,
    *,
    session_id: str,
    tenant_id: int,
    requested_by: int,
    intent: EmbeddedSignupAccountIntent,
) -> str:
    payload = {
        "session_id": session_id,
        "tenant_id": tenant_id,
        "requested_by": requested_by,
        "display_name": intent.display_name,
        "account_id": intent.account_id,
        "market_id": intent.market_id,
        "priority": intent.priority,
        "nonce": secrets.token_hex(16),
    }
    encoded = _b64url(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    signature = hmac.new(
        str(settings.app_secret).encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{encoded}.{signature}"


def _decode_state(
    settings: WhatsAppEmbeddedSignupSettings,
    state: str,
) -> dict[str, Any]:
    try:
        encoded, supplied_signature = state.rsplit(".", 1)
    except ValueError as exc:
        raise EmbeddedSignupError("embedded_signup_state_invalid") from exc
    expected_signature = hmac.new(
        str(settings.app_secret).encode("utf-8"),
        encoded.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise EmbeddedSignupError("embedded_signup_state_invalid")
    try:
        payload = json.loads(_b64url_decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EmbeddedSignupError("embedded_signup_state_invalid") from exc
    if not isinstance(payload, dict):
        raise EmbeddedSignupError("embedded_signup_state_invalid")
    return payload


def _owner_business_id(payload: dict[str, Any]) -> str | None:
    owner = payload.get("owner_business_info")
    if isinstance(owner, dict):
        value = _optional(owner.get("id"))
        if value and _META_OBJECT_ID_RE.fullmatch(value):
            return value
    return None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _b64url(content: bytes) -> str:
    return base64.urlsafe_b64encode(content).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _optional(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
