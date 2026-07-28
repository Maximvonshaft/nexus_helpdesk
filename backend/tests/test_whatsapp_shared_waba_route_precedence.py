from __future__ import annotations

import os

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-whatsapp-shared-waba-route.db",
)

from app.api.whatsapp_meta_shared_webhook import (
    receive_shared_meta_whatsapp_webhook,
)
from app.main import app


def test_shared_waba_webhook_is_registered_before_connection_specific_route():
    """The static WABA authority must win Starlette's first-match routing."""

    routes = [
        route
        for route in app.routes
        if getattr(route, "path", None)
        in {
            "/api/integrations/whatsapp/meta/webhook",
            "/api/integrations/whatsapp/meta/{connection_id}/webhook",
        }
        and "POST" in (getattr(route, "methods", None) or set())
    ]
    assert [route.path for route in routes] == [
        "/api/integrations/whatsapp/meta/webhook",
        "/api/integrations/whatsapp/meta/{connection_id}/webhook",
    ]
    assert routes[0].endpoint is receive_shared_meta_whatsapp_webhook
