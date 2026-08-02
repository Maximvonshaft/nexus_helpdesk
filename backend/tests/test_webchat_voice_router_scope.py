from __future__ import annotations

import inspect

from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute

from app.api.webchat_preauth import enforce_webchat_conversation_preauth
from app.api.webchat_voice import router, unthrottled_router
from app.bootstrap.routers import register_api_routers


_PUBLIC_VOICE_PATHS = {
    "/api/webchat/conversations/{conversation_id}/voice/policy",
    "/api/webchat/conversations/{conversation_id}/voice/sessions",
    "/api/webchat/conversations/{conversation_id}/voice/{voice_session_id}/end",
}


def _api_routes(app: FastAPI) -> dict[str, APIRoute]:
    return {
        route.path: route
        for route in app.routes
        if isinstance(route, APIRoute)
    }


def _dependency_calls(route: APIRoute) -> set[object]:
    return {dependency.call for dependency in route.dependant.dependencies}


def test_voice_preauth_is_limited_to_public_conversation_routes() -> None:
    assert {route.path for route in router.routes} == _PUBLIC_VOICE_PATHS

    unthrottled_paths = {route.path for route in unthrottled_router.routes}
    assert "/api/webchat/admin/voice/sessions" in unthrottled_paths
    assert "/api/webchat/voice/runtime-config" in unthrottled_paths
    assert not (_PUBLIC_VOICE_PATHS & unthrottled_paths)

    app = FastAPI()
    app.include_router(unthrottled_router)
    app.include_router(
        router,
        dependencies=[Depends(enforce_webchat_conversation_preauth)],
    )
    routes = _api_routes(app)

    for path in _PUBLIC_VOICE_PATHS:
        assert (
            enforce_webchat_conversation_preauth
            in _dependency_calls(routes[path])
        )
    for path in unthrottled_paths:
        assert (
            enforce_webchat_conversation_preauth
            not in _dependency_calls(routes[path])
        )


def test_bootstrap_registers_both_voice_routers_once() -> None:
    source = inspect.getsource(register_api_routers)
    final_router_loop = source.rsplit("for router in (", 1)[1]

    assert final_router_loop.count("webchat_voice_unthrottled_router,") == 1
    assert final_router_loop.count("webchat_voice_router,") == 1
    assert final_router_loop.index("webchat_voice_unthrottled_router,") < (
        final_router_loop.index("webchat_voice_router,")
    )
    assert "if router is webchat_voice_router" in final_router_loop
    assert "[Depends(enforce_webchat_conversation_preauth)]" in final_router_loop
