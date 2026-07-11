from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.nexus_osr import runtime_evidence_transport as transport  # noqa: E402

PUBLIC_TEST_IP = "93.184.216.34"


def _run(status_code: int, body: bytes = b"{}") -> dict:
    return transport.run_read_only_http_probe(
        transport.ReadOnlyProbeSpec(
            path="runtime_decision",
            endpoint="/api/admin/osr/runtime-decision-audits?limit=1",
        ),
        base_url="https://staging.example.test",
        allowed_hosts=["staging.example.test"],
        tenant_id="tenant-a",
        bearer_token="not-emitted",
        resolver=lambda _host, _port: [PUBLIC_TEST_IP],
        executor=lambda *_args: (status_code, body),
    )


@pytest.mark.parametrize("status_code", [401, 403])
def test_auth_denial_is_explicit_permission_failure(status_code: int) -> None:
    result = _run(status_code, json.dumps({"detail": "denied"}).encode("utf-8"))

    assert result["error_code"] == "permission_denied"
    assert result["permission_granted"] is False
    assert result["status_code"] == status_code
    assert result["payload"] == {}
    assert "not-emitted" not in json.dumps(result)


@pytest.mark.parametrize("status_code", [400, 404, 429, 500, 503])
def test_non_success_status_cannot_claim_permission_or_payload(status_code: int) -> None:
    result = _run(status_code, json.dumps({"tenant_id": "tenant-a", "state": "ready"}).encode("utf-8"))

    assert result["error_code"] == "source_unavailable"
    assert result["permission_granted"] is False
    assert result["status_code"] == status_code
    assert result["payload"] == {}


def test_successful_object_response_is_the_only_permission_success() -> None:
    payload = {"tenant_id": "tenant-a", "state": "ready"}
    result = _run(200, json.dumps(payload).encode("utf-8"))

    assert "error_code" not in result
    assert result["permission_granted"] is True
    assert result["status_code"] == 200
    assert result["payload"] == payload


def test_redirect_does_not_claim_permission() -> None:
    result = _run(302, b"")

    assert result["error_code"] == "unsafe_probe_url"
    assert result["permission_granted"] is False
    assert result["status_code"] == 302
