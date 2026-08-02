from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api import webchat as webchat_api
from app.api import webchat_preauth
from app.bootstrap.routers import register_api_routers
from app.db import Base
from app.model_registry import register_all_models
from app.services import webchat_rate_limit
from app.services.webchat_rate_limit_policy import (
    WebchatRateLimitPolicy,
    load_webchat_preauth_rate_limit_policy,
)


def _request(
    path: str,
    *,
    conversation_id: str = "wc_preauth_contract",
    client: str = "203.0.113.25",
) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [],
            "path_params": {"conversation_id": conversation_id},
            "client": (client, 50000),
            "server": ("testserver", 443),
        }
    )


def _policy(*, max_requests: int) -> WebchatRateLimitPolicy:
    return WebchatRateLimitPolicy(
        window_seconds=60,
        max_requests=max_requests,
    )


def test_default_preauth_budget_is_independent_and_shared_ip_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEBCHAT_PREAUTH_RATE_LIMIT_WINDOW_SECONDS", raising=False)
    monkeypatch.delenv("WEBCHAT_PREAUTH_RATE_LIMIT_MAX_REQUESTS", raising=False)
    business = SimpleNamespace(
        webchat_rate_limit_window_seconds=60,
        webchat_rate_limit_max_requests=20,
    )

    policy = load_webchat_preauth_rate_limit_policy(business)

    assert policy == WebchatRateLimitPolicy(
        window_seconds=60,
        max_requests=3000,
    )


def test_preauth_budget_cannot_be_stricter_than_authorized_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WEBCHAT_PREAUTH_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("WEBCHAT_PREAUTH_RATE_LIMIT_MAX_REQUESTS", "19")
    business = SimpleNamespace(
        webchat_rate_limit_window_seconds=60,
        webchat_rate_limit_max_requests=20,
    )

    with pytest.raises(RuntimeError, match="must not create a stricter request rate"):
        load_webchat_preauth_rate_limit_policy(business)


def test_memory_preauth_bucket_cannot_be_bypassed_with_random_conversation_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webchat_rate_limit._MEMORY_BUCKETS.clear()
    monkeypatch.setattr(
        webchat_rate_limit.settings,
        "webchat_rate_limit_backend",
        "memory",
    )
    monkeypatch.setattr(webchat_rate_limit, "preauth_policy", _policy(max_requests=1))
    first = _request(
        "/api/webchat/conversations/wc_random_a/messages",
        conversation_id="wc_random_a",
    )
    second = _request(
        "/api/webchat/conversations/wc_random_b/messages",
        conversation_id="wc_random_b",
    )

    webchat_rate_limit.enforce_webchat_preauth_rate_limit(object(), first)
    with pytest.raises(HTTPException) as exc:
        webchat_rate_limit.enforce_webchat_preauth_rate_limit(object(), second)

    assert exc.value.status_code == 429
    assert exc.value.detail == "too many webchat requests"
    assert len(webchat_rate_limit._MEMORY_BUCKETS) == 1


def test_preauth_and_authorized_memory_budgets_are_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webchat_rate_limit._MEMORY_BUCKETS.clear()
    monkeypatch.setattr(
        webchat_rate_limit.settings,
        "webchat_rate_limit_backend",
        "memory",
    )
    monkeypatch.setattr(
        webchat_rate_limit.settings,
        "webchat_rate_limit_window_seconds",
        60,
    )
    monkeypatch.setattr(
        webchat_rate_limit.settings,
        "webchat_rate_limit_max_requests",
        1,
    )
    monkeypatch.setattr(webchat_rate_limit, "preauth_policy", _policy(max_requests=3))
    request = _request(
        "/api/webchat/conversations/wc_authorized/messages"
    )

    webchat_rate_limit.enforce_webchat_preauth_rate_limit(object(), request)
    webchat_rate_limit.enforce_webchat_preauth_rate_limit(object(), request)
    webchat_rate_limit._enforce_memory(
        "authorized-contract",
        policy=webchat_rate_limit._authorized_policy(),
    )
    with pytest.raises(HTTPException):
        webchat_rate_limit._enforce_memory(
            "authorized-contract",
            policy=webchat_rate_limit._authorized_policy(),
        )

    assert len(next(iter(webchat_rate_limit._MEMORY_BUCKETS.values()))) == 2


def test_database_preauth_bucket_survives_rollback_and_random_ids_share_one_row(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    register_all_models()
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'preauth-rate-limit.db'}",
        future=True,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
        future=True,
    )
    monkeypatch.setattr(
        webchat_rate_limit.settings,
        "webchat_rate_limit_backend",
        "database",
    )
    monkeypatch.setattr(webchat_rate_limit, "preauth_policy", _policy(max_requests=20))
    first = _request(
        "/api/webchat/conversations/wc_random_a/messages",
        conversation_id="wc_random_a",
    )
    second = _request(
        "/api/webchat/conversations/wc_random_b/voice/sessions",
        conversation_id="wc_random_b",
    )
    outer = factory()
    try:
        webchat_rate_limit.enforce_webchat_preauth_rate_limit(outer, first)
        outer.rollback()
        webchat_rate_limit.enforce_webchat_preauth_rate_limit(outer, second)
        outer.rollback()
        with factory() as verifier:
            rows = verifier.execute(
                text(
                    "SELECT request_count FROM webchat_rate_limits "
                    "ORDER BY id"
                )
            ).mappings().all()
        assert [int(row["request_count"]) for row in rows] == [2]
    finally:
        outer.close()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_dependency_applies_same_preauth_authority_to_text_and_voice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def capture(_db, request) -> None:
        captured.append(request.url.path)

    monkeypatch.setattr(
        webchat_preauth,
        "enforce_webchat_preauth_rate_limit",
        capture,
    )
    db = object()
    webchat_preauth.enforce_webchat_conversation_preauth(
        _request(
            "/api/webchat/conversations/wc_preauth_contract/messages"
        ),
        db,
    )
    webchat_preauth.enforce_webchat_conversation_preauth(
        _request(
            "/api/webchat/conversations/wc_preauth_contract/voice/sessions"
        ),
        db,
    )

    assert captured == [
        "/api/webchat/conversations/wc_preauth_contract/messages",
        "/api/webchat/conversations/wc_preauth_contract/voice/sessions",
    ]


def _include_router_calls(module) -> list[ast.Call]:
    tree = ast.parse(inspect.getsource(module))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "include_router"
    ]


def _router_argument_name(call: ast.Call) -> str | None:
    if not call.args or not isinstance(call.args[0], ast.Name):
        return None
    return call.args[0].id


def test_public_router_has_one_canonical_preauth_registration() -> None:
    calls = _include_router_calls(webchat_api)
    public_calls = [
        call
        for call in calls
        if _router_argument_name(call) == "public_router"
    ]
    admin_calls = [
        call
        for call in calls
        if _router_argument_name(call) == "admin_router"
    ]

    assert len(public_calls) == 1
    assert len(admin_calls) == 1

    dependencies = next(
        (
            keyword.value
            for keyword in public_calls[0].keywords
            if keyword.arg == "dependencies"
        ),
        None,
    )
    assert isinstance(dependencies, ast.List)
    assert len(dependencies.elts) == 1
    dependency = dependencies.elts[0]
    assert isinstance(dependency, ast.Call)
    assert isinstance(dependency.func, ast.Name)
    assert dependency.func.id == "Depends"
    assert len(dependency.args) == 1
    assert isinstance(dependency.args[0], ast.Name)
    assert dependency.args[0].id == "enforce_webchat_conversation_preauth"

    assert not any(
        keyword.arg == "dependencies"
        for keyword in admin_calls[0].keywords
    )


def test_public_voice_router_keeps_one_registration_with_preauth_authority() -> None:
    source = inspect.getsource(register_api_routers)
    final_router_loop = source.rsplit("for router in (", 1)[1]
    assert final_router_loop.count("webchat_voice_router,") == 1
    assert "if router is webchat_voice_router" in final_router_loop
    assert "[Depends(enforce_webchat_conversation_preauth)]" in final_router_loop
