from __future__ import annotations

import inspect

from app.api.webchat_voice import router, unthrottled_router
from app.bootstrap.routers import register_api_routers


_PUBLIC_VOICE_PATHS = {
    "/api/webchat/conversations/{conversation_id}/voice/policy",
    "/api/webchat/conversations/{conversation_id}/voice/sessions",
    "/api/webchat/conversations/{conversation_id}/voice/{voice_session_id}/end",
}


def test_voice_routers_have_one_disjoint_public_admission_boundary() -> None:
    public_paths = {route.path for route in router.routes}
    unthrottled_paths = {route.path for route in unthrottled_router.routes}

    assert public_paths == _PUBLIC_VOICE_PATHS
    assert "/api/webchat/admin/voice/sessions" in unthrottled_paths
    assert "/api/webchat/voice/runtime-config" in unthrottled_paths
    assert not (public_paths & unthrottled_paths)
    assert all("/admin/" not in path for path in public_paths)
    assert all(not path.endswith("/runtime-config") for path in public_paths)


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
