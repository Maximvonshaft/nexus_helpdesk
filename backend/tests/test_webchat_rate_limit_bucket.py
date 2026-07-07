from __future__ import annotations

from types import SimpleNamespace

from app.services.webchat_rate_limit import _bucket_key


def _request(ip: str = "203.0.113.10"):
    return SimpleNamespace(client=SimpleNamespace(host=ip), headers={})


def test_webchat_rate_limit_bucket_key_is_stable_hash_within_database_limit():
    key = _bucket_key(
        request=_request(),
        tenant_key="tenant-" + "x" * 120,
        conversation_id="wc_" + "y" * 80,
    )

    assert len(key) == 64
    assert key.isalnum()


def test_webchat_rate_limit_bucket_key_preserves_scope_separation():
    request = _request()

    base = _bucket_key(request=request, tenant_key="tenant-a", conversation_id="wc_1")

    assert _bucket_key(request=request, tenant_key="tenant-b", conversation_id="wc_1") != base
    assert _bucket_key(request=request, tenant_key="tenant-a", conversation_id="wc_2") != base
    assert _bucket_key(request=_request("203.0.113.11"), tenant_key="tenant-a", conversation_id="wc_1") != base
