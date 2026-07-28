from __future__ import annotations

import os

import pytest
from fastapi import FastAPI

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-shared-waba-route.db",
)

from app.api.whatsapp_integration import (
    receive_meta_whatsapp_webhook,
    verify_meta_whatsapp_webhook,
)
from app.api.whatsapp_meta_shared_webhook import (
    receive_shared_meta_whatsapp_webhook,
    verify_shared_meta_whatsapp_webhook,
)
from app.bootstrap.routers import register_api_routers


@pytest.mark.parametrize(
    ("shared_endpoint", "dynamic_endpoint"),
    [
        (
            verify_shared_meta_whatsapp_webhook,
            verify_meta_whatsapp_webhook,
        ),
        (
            receive_shared_meta_whatsapp_webhook,
            receive_meta_whatsapp_webhook,
        ),
    ],
)
def test_shared_waba_webhook_is_registered_before_connection_specific_route(
    shared_endpoint,
    dynamic_endpoint,
):
    """Static WABA verification and event routes must win first-match routing."""

    isolated_app = FastAPI()
    register_api_routers(isolated_app)
    routes = list(isolated_app.routes)
    shared_index = next(
        index
        for index, route in enumerate(routes)
        if getattr(route, "endpoint", None) is shared_endpoint
    )
    dynamic_index = next(
        index
        for index, route in enumerate(routes)
        if getattr(route, "endpoint", None) is dynamic_endpoint
    )

    assert shared_index < dynamic_index
    assert routes[shared_index].path.endswith("/meta/webhook")
    assert routes[dynamic_index].path.endswith("/meta/{connection_id}/webhook")
