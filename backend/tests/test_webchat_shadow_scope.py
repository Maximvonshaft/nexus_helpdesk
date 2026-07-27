from __future__ import annotations

from fastapi import HTTPException, Request

from app.services.webchat_tenant_binding import resolve_public_webchat_scope


class _ScopeSession:
    def __init__(self) -> None:
        self.info: dict[str, object] = {}


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/webchat/init",
            "raw_path": b"/api/webchat/init",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        }
    )


def test_unbound_nonproduction_caller_cannot_manufacture_tenant_boundary() -> None:
    db = _ScopeSession()

    scope = resolve_public_webchat_scope(
        db,  # type: ignore[arg-type]
        request=_request(),
        requested_tenant_key="arbitrary-fixture-tenant",
        requested_channel_key="website",
        app_env="test",
    )

    assert scope.tenant_key == "default"
    assert scope.channel_key == "website"
    assert scope.authority == "non_production_shadow"
    assert scope.binding_id is None


def test_unbound_production_caller_remains_fail_closed() -> None:
    db = _ScopeSession()

    try:
        resolve_public_webchat_scope(
            db,  # type: ignore[arg-type]
            request=_request(),
            requested_tenant_key="default",
            requested_channel_key="website",
            app_env="production",
        )
    except HTTPException as exc:
        assert exc.status_code == 403
        assert exc.detail == "webchat_public_binding_required"
    else:
        raise AssertionError("production WebChat accepted an unbound public scope")
