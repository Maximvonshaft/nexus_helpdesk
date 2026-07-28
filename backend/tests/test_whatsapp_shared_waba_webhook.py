from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-shared-waba-webhook.db",
)

from app.api import whatsapp_meta_shared_webhook
from app.db import Base
from app.model_registry import register_all_models
from app.models import ChannelAccount, Tenant
from app.models_whatsapp import WhatsAppConnection
from app.services.secret_crypto import SecretCryptoService
from app.services.whatsapp_meta_cloud import subscribe_meta_waba

register_all_models()


class _Response:
    status_code = 200

    def json(self):
        return {"success": True}


class _Client:
    def __init__(self):
        self.calls: list[dict[str, object]] = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": timeout,
            }
        )
        return _Response()


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'shared-waba-webhook.db'}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        future=True,
    )
    Base.metadata.create_all(engine)
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def _connection(
    db_session,
    *,
    tenant: Tenant,
    account_id: str,
    phone_number_id: str,
    waba_id: str,
    app_secret: str,
) -> WhatsAppConnection:
    account = ChannelAccount(
        tenant_id=tenant.id,
        provider="whatsapp",
        account_id=account_id,
        display_name=account_id,
        is_active=True,
    )
    db_session.add(account)
    db_session.flush()
    crypto = SecretCryptoService.whatsapp()
    connection = WhatsAppConnection(
        tenant_id=tenant.id,
        channel_account_id=account.id,
        transport="meta_cloud_api",
        desired_state="active",
        observed_state="connected",
        authentication_state="linked",
        listener_state="active",
        verification_state="verified",
        desired_generation=1,
        observed_generation=1,
        waba_id=waba_id,
        phone_number_id=phone_number_id,
        graph_api_version="v23.0",
        access_token_encrypted=crypto.encrypt("access-token"),
        app_secret_encrypted=crypto.encrypt(app_secret),
        verify_token_encrypted=crypto.encrypt("verify-token"),
    )
    db_session.add(connection)
    db_session.flush()
    return connection


def _request(raw_body: bytes) -> Request:
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {
            "type": "http.request",
            "body": raw_body,
            "more_body": False,
        }

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/integrations/whatsapp/meta/webhook",
            "raw_path": b"/api/integrations/whatsapp/meta/webhook",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("testserver", 443),
        },
        receive,
    )


def test_waba_subscription_canonicalizes_connection_callback_to_shared_route():
    client = _Client()
    connection = SimpleNamespace(
        transport="meta_cloud_api",
        graph_api_version="v23.0",
        phone_number_id="phone-1",
        waba_id="waba-shared",
    )

    subscribe_meta_waba(
        connection,
        access_token="access-token",
        callback_url=(
            "https://support.example.test/"
            "api/integrations/whatsapp/meta/123/webhook"
        ),
        verify_token="verify-token",
        client=client,
    )

    assert len(client.calls) == 1
    assert client.calls[0]["json"] == {
        "override_callback_uri": (
            "https://support.example.test/"
            "api/integrations/whatsapp/meta/webhook"
        ),
        "verify_token": "verify-token",
    }


def test_shared_webhook_routes_two_phone_numbers_on_one_waba_to_exact_accounts(
    db_session,
    monkeypatch,
):
    tenant = Tenant(
        tenant_key="shared-waba",
        display_name="Shared WABA",
        is_active=True,
    )
    db_session.add(tenant)
    db_session.flush()
    secret = "shared-meta-app-secret"
    _connection(
        db_session,
        tenant=tenant,
        account_id="wa-phone-a",
        phone_number_id="phone-a",
        waba_id="waba-shared",
        app_secret=secret,
    )
    _connection(
        db_session,
        tenant=tenant,
        account_id="wa-phone-b",
        phone_number_id="phone-b",
        waba_id="waba-shared",
        app_secret=secret,
    )
    db_session.commit()

    observed: list[str] = []

    def fake_ingest(_db, payload):
        observed.append(str(payload["account_id"]))
        return SimpleNamespace(
            as_dict=lambda: {
                "account_id": payload["account_id"],
                "external_message_id": payload["external_message_id"],
            }
        )

    monkeypatch.setattr(
        whatsapp_meta_shared_webhook,
        "ingest_whatsapp_inbound",
        fake_ingest,
    )

    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-shared",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "phone_number_id": "phone-a",
                                "display_phone_number": "+41000000001",
                            },
                            "contacts": [
                                {
                                    "wa_id": "41790000001",
                                    "profile": {"name": "Customer A"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid-a",
                                    "from": "41790000001",
                                    "timestamp": "1785250000",
                                    "type": "text",
                                    "text": {"body": "message-a"},
                                }
                            ],
                        },
                    }
                ],
            },
            {
                "id": "waba-shared",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {
                                "phone_number_id": "phone-b",
                                "display_phone_number": "+41000000002",
                            },
                            "contacts": [
                                {
                                    "wa_id": "41790000002",
                                    "profile": {"name": "Customer B"},
                                }
                            ],
                            "messages": [
                                {
                                    "id": "wamid-b",
                                    "from": "41790000002",
                                    "timestamp": "1785250001",
                                    "type": "text",
                                    "text": {"body": "message-b"},
                                }
                            ],
                        },
                    }
                ],
            },
        ],
    }
    raw_body = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    signature = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    result = asyncio.run(
        whatsapp_meta_shared_webhook.receive_shared_meta_whatsapp_webhook(
            request=_request(raw_body),
            x_hub_signature_256=signature,
            db=db_session,
        )
    )

    assert result["ok"] is True
    assert observed == ["wa-phone-a", "wa-phone-b"]
    assert [item["account_id"] for item in result["inbound"]] == [
        "wa-phone-a",
        "wa-phone-b",
    ]
