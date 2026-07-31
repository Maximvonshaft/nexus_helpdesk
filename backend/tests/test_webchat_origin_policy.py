from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.services.webchat_origin_policy import validate_public_origin


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/webchat/conversations/wc_test/messages",
            "headers": headers,
        }
    )


def _production_settings():
    return SimpleNamespace(
        app_env="production",
        webchat_allowed_origins=["https://support.invalid"],
        webchat_allow_no_origin=False,
    )


def test_same_origin_browser_poll_without_origin_or_referer_is_allowed():
    request = _request(
        [
            (b"sec-fetch-site", b"same-origin"),
            (b"sec-fetch-mode", b"cors"),
            (b"sec-fetch-dest", b"empty"),
        ]
    )

    assert validate_public_origin(request, _production_settings()) is None


def test_plain_no_origin_request_remains_forbidden():
    with pytest.raises(HTTPException) as exc:
        validate_public_origin(_request([]), _production_settings())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Webchat origin is required"


def test_cross_site_fetch_metadata_cannot_bypass_origin_policy():
    request = _request(
        [
            (b"sec-fetch-site", b"cross-site"),
            (b"sec-fetch-mode", b"cors"),
            (b"sec-fetch-dest", b"empty"),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        validate_public_origin(request, _production_settings())

    assert exc.value.status_code == 403
    assert exc.value.detail == "Webchat origin is required"
