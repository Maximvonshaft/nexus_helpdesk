from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

import pytest

from app.models_whatsapp import WhatsAppConnection
from app.services.whatsapp_connection_service import (
    WhatsAppActivationError,
    assert_connection_can_activate,
)
from app.services.whatsapp_meta_webhook import verify_meta_webhook_signature
from app.services.whatsapp_transport_registry import (
    BAILEYS_SIDECAR_TRANSPORT,
    META_CLOUD_API_TRANSPORT,
    resolve_whatsapp_transport,
)


ROOT = Path(__file__).resolve().parents[2]


def test_connection_model_is_one_extension_of_channel_account() -> None:
    assert WhatsAppConnection.__tablename__ == "whatsapp_connections"
    table = WhatsAppConnection.__table__
    assert table.c.channel_account_id.unique is True
    assert table.c.tenant_id.nullable is False
    assert table.c.transport.nullable is False
    assert table.c.desired_generation.nullable is False
    assert table.c.observed_generation.nullable is False


@pytest.mark.parametrize(
    ("transport", "expected_name"),
    [
        (BAILEYS_SIDECAR_TRANSPORT, "baileys_sidecar"),
        (META_CLOUD_API_TRANSPORT, "meta_cloud_api"),
    ],
)
def test_transport_registry_treats_both_transports_as_first_class(
    transport: str,
    expected_name: str,
) -> None:
    adapter = resolve_whatsapp_transport(transport)
    assert adapter.name == expected_name
    assert adapter.supports_inbound is True
    assert adapter.supports_outbound is True
    assert adapter.supports_status_probe is True


def test_activation_fails_closed_until_transport_is_verified() -> None:
    connection = WhatsAppConnection(
        tenant_id=1,
        channel_account_id=1,
        transport=BAILEYS_SIDECAR_TRANSPORT,
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="pending",
        desired_generation=1,
        observed_generation=1,
        sidecar_session_key="wa-main",
    )
    with pytest.raises(WhatsAppActivationError, match="verification_required"):
        assert_connection_can_activate(connection)

    connection.verification_state = "verified"
    assert_connection_can_activate(connection)


def test_meta_webhook_signature_is_verified_against_raw_body() -> None:
    secret = "meta-app-secret"
    body = json.dumps({"object": "whatsapp_business_account"}).encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    verify_meta_webhook_signature(
        raw_body=body,
        signature=f"sha256={digest}",
        app_secret=secret,
    )
    with pytest.raises(ValueError, match="invalid_meta_webhook_signature"):
        verify_meta_webhook_signature(
            raw_body=body + b" ",
            signature=f"sha256={digest}",
            app_secret=secret,
        )


def test_canonical_deployment_contains_one_hardened_sidecar_service() -> None:
    compose = (ROOT / "deploy" / "docker-compose.controlled.yml").read_text(
        encoding="utf-8"
    )
    assert "whatsapp-sidecar-controlled:" in compose
    assert "WHATSAPP_ENABLED" in compose
    assert "WHATSAPP_BAILEYS_SIDECAR_URL" in compose
    assert "WHATSAPP_NATIVE_ENABLED" not in compose
    assert "WHATSAPP_DISPATCH_MODE" not in compose
    assert "npm ci &&" not in compose
    assert not (
        ROOT / "deploy" / "docker-compose.whatsapp-sidecar.example.yml"
    ).exists()


def test_operator_api_exposes_the_complete_binding_lifecycle() -> None:
    source = (
        ROOT / "webapp" / "src" / "lib" / "whatsappApi.ts"
    ).read_text(encoding="utf-8")
    for authority in (
        "createConnection",
        "updateConnection",
        "startBinding",
        "bindingQr",
        "requestPairingCode",
        "logout",
        "restart",
        "probe",
        "testInbound",
        "testOutbound",
        "createEmbeddedSignupSession",
        "completeEmbeddedSignup",
    ):
        assert authority in source
    assert "/api/admin/whatsapp/connections" in source
    assert "/native" not in source
