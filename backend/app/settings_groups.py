from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DatabaseCapabilitySettings:
    url_scheme: str
    echo: bool
    expected_migration_head: str | None


@dataclass(frozen=True)
class AuthenticationCapabilitySettings:
    issuer: str
    audience: str
    access_token_expire_hours: int
    secret_configured: bool
    dev_auth_enabled: bool


@dataclass(frozen=True)
class StorageCapabilitySettings:
    backend: str
    upload_root: str
    s3_bucket_configured: bool
    s3_endpoint_configured: bool
    s3_credentials_configured: bool


@dataclass(frozen=True)
class OutboundCapabilitySettings:
    enabled: bool
    provider: str
    whatsapp_native_enabled: bool
    whatsapp_dispatch_mode: str
    email_mailbox_sync_enabled: bool


@dataclass(frozen=True)
class ProviderCapabilitySettings:
    runtime_enabled: bool
    private_ai_enabled: bool
    webchat_ai_enabled: bool
    auto_reply_mode: str


@dataclass(frozen=True)
class WebchatCapabilitySettings:
    allowed_origin_count: int
    allow_no_origin: bool
    legacy_token_transport: bool
    websocket_enabled: bool
    websocket_broker: str


@dataclass(frozen=True)
class VoiceCapabilitySettings:
    human_call_enabled: bool
    live_ai_voice_enabled: bool
    legacy_aggregate_enabled: bool


@dataclass(frozen=True)
class CompatibilityCapabilitySettings:
    external_channel_transport: str
    external_channel_deployment_mode: str
    external_channel_sync_enabled: bool
    external_channel_event_driver_enabled: bool
    external_channel_bridge_enabled: bool
    external_channel_cli_fallback_enabled: bool

    @property
    def retired_runtime_requested(self) -> bool:
        return bool(
            self.external_channel_transport != "disabled"
            or self.external_channel_deployment_mode != "disabled"
            or self.external_channel_sync_enabled
            or self.external_channel_event_driver_enabled
            or self.external_channel_bridge_enabled
            or self.external_channel_cli_fallback_enabled
        )


def _scheme(value: str) -> str:
    return str(value or "").split(":", 1)[0].lower() or "unknown"


def capability_groups(settings: Any) -> dict[str, Any]:
    """Return typed, secret-free capability ownership groups."""

    human_call_enabled = bool(
        getattr(settings, "webchat_human_call_enabled", False)
    )
    live_ai_voice_enabled = bool(
        getattr(settings, "webchat_live_ai_voice_enabled", False)
    )
    groups = {
        "database": DatabaseCapabilitySettings(
            url_scheme=_scheme(settings.database_url),
            echo=bool(settings.database_echo),
            expected_migration_head=settings.expected_migration_head,
        ),
        "authentication": AuthenticationCapabilitySettings(
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            access_token_expire_hours=int(settings.access_token_expire_hours),
            secret_configured=bool(settings.jwt_secret_key),
            dev_auth_enabled=bool(settings.allow_dev_auth),
        ),
        "storage": StorageCapabilitySettings(
            backend=settings.storage_backend,
            upload_root=str(Path(settings.upload_root)),
            s3_bucket_configured=bool(settings.s3_bucket),
            s3_endpoint_configured=bool(settings.s3_endpoint_url),
            s3_credentials_configured=bool(settings.s3_access_key and settings.s3_secret_key),
        ),
        "outbound": OutboundCapabilitySettings(
            enabled=bool(settings.enable_outbound_dispatch),
            provider=settings.outbound_provider,
            whatsapp_native_enabled=bool(settings.whatsapp_native_enabled),
            whatsapp_dispatch_mode=settings.whatsapp_dispatch_mode,
            email_mailbox_sync_enabled=bool(settings.email_mailbox_sync_enabled),
        ),
        "provider": ProviderCapabilitySettings(
            runtime_enabled=bool(settings.provider_runtime_enabled),
            private_ai_enabled=bool(settings.private_ai_runtime_enabled),
            webchat_ai_enabled=bool(settings.webchat_ai_enabled),
            auto_reply_mode=settings.webchat_ai_auto_reply_mode,
        ),
        "webchat": WebchatCapabilitySettings(
            allowed_origin_count=len(settings.webchat_allowed_origins),
            allow_no_origin=bool(settings.webchat_allow_no_origin),
            legacy_token_transport=bool(settings.webchat_allow_legacy_token_transport),
            websocket_enabled=bool(settings.webchat_ws_enabled),
            websocket_broker=settings.webchat_ws_broker,
        ),
        "voice": VoiceCapabilitySettings(
            human_call_enabled=human_call_enabled,
            live_ai_voice_enabled=live_ai_voice_enabled,
            legacy_aggregate_enabled=bool(getattr(settings, "webchat_voice_enabled", False)),
        ),
        "compatibility": CompatibilityCapabilitySettings(
            external_channel_transport=settings.external_channel_transport,
            external_channel_deployment_mode=settings.external_channel_deployment_mode,
            external_channel_sync_enabled=bool(settings.external_channel_sync_enabled),
            external_channel_event_driver_enabled=bool(settings.external_channel_event_driver_enabled),
            external_channel_bridge_enabled=bool(settings.external_channel_bridge_enabled),
            external_channel_cli_fallback_enabled=bool(settings.external_channel_cli_fallback_enabled),
        ),
    }
    return groups


def effective_safe_config(settings: Any) -> dict[str, Any]:
    return {
        "schema": "nexus.effective-safe-config.v1",
        "app_env": settings.app_env,
        "process_role": settings.process_role,
        "app_version": settings.app_version,
        "groups": {name: asdict(group) for name, group in capability_groups(settings).items()},
        "contains_secret_values": False,
    }
