from __future__ import annotations

import inspect
import os

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-shared-waba-route.db",
)

from app.api.whatsapp_integration import (
    receive_meta_whatsapp_webhook,
    router as connection_specific_router,
    verify_meta_whatsapp_webhook,
)
from app.api.whatsapp_meta_shared_webhook import (
    receive_shared_meta_whatsapp_webhook,
    router as shared_waba_router,
    verify_shared_meta_whatsapp_webhook,
)
from app.bootstrap.routers import register_api_routers


@pytest.mark.parametrize(
    ("method", "shared_endpoint", "dynamic_endpoint"),
    [
        (
            "GET",
            verify_shared_meta_whatsapp_webhook,
            verify_meta_whatsapp_webhook,
        ),
        (
            "POST",
            receive_shared_meta_whatsapp_webhook,
            receive_meta_whatsapp_webhook,
        ),
    ],
)
def test_shared_and_connection_specific_waba_routes_are_both_defined(
    method,
    shared_endpoint,
    dynamic_endpoint,
):
    """Both authorities must exist before their application registration order matters."""

    shared_route = next(
        route
        for route in shared_waba_router.routes
        if getattr(route, "endpoint", None) is shared_endpoint
    )
    dynamic_route = next(
        route
        for route in connection_specific_router.routes
        if getattr(route, "endpoint", None) is dynamic_endpoint
    )

    assert method in (shared_route.methods or set())
    assert method in (dynamic_route.methods or set())
    assert shared_route.path.endswith("/meta/webhook")
    assert dynamic_route.path.endswith("/meta/{connection_id}/webhook")


def test_shared_waba_router_is_registered_before_dynamic_connection_router():
    """Starlette is first-match; production registration order is a hard contract."""

    source = inspect.getsource(register_api_routers)
    shared_marker = "whatsapp_meta_shared_webhook_router,"
    dynamic_marker = "whatsapp_integration_router,"

    assert shared_marker in source
    assert dynamic_marker in source
    assert source.index(shared_marker) < source.index(dynamic_marker)
