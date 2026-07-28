from __future__ import annotations

import os

import pytest
from fastapi import FastAPI

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-shared-waba-route.db",
)

from app.api.whatsapp_meta_shared_webhook import (
    receive_shared_meta_whatsapp_webhook,
    verify_shared_meta_whatsapp_webhook,
)
from app.bootstrap.routers import register_api_routers


@pytest.mark.parametrize(
    ("method", "expected_endpoint"),
    [
        ("GET", verify_shared_meta_whatsapp_webhook),
        ("POST", receive_shared_meta_whatsapp_webhook),
    ],
)
def test_shared_waba_webhook_is_registered_before_connection_specific_route(
    method,
    expected_endpoint,
):
    """Static WABA verification and event routes must win first-match routing."""

    isolated_app = FastAPI()
    register_api_routers(isolated_app)
    routes = [
        route
        for route in isolated_app.routes
        if getattr(route, "path", None)
        in {
            "/api/integrations/whatsapp/meta/webhook",
            "/api/integrations/whatsapp/meta/{connection_id}/webhook",
        }
        and method in (getattr(route, "methods", None) or set())
    ]
    assert [route.path for route in routes] == [
        "/api/integrations/whatsapp/meta/webhook",
        "/api/integrations/whatsapp/meta/{connection_id}/webhook",
    ]
    assert routes[0].endpoint is expected_endpoint
