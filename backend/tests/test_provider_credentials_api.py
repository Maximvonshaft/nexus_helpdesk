from app.api.admin_provider_credentials import router


def test_provider_credentials_admin_surface_enabled_with_safe_routes():
    assert router.prefix == "/api/admin/provider-credentials"
    paths = {route.path for route in router.routes}
    assert "/api/admin/provider-credentials/codex/status" in paths
    assert "/api/admin/provider-credentials/codex/authorize" in paths
    assert "/api/admin/provider-credentials/codex/callback" in paths
    assert "/api/admin/provider-credentials/codex/device/start" in paths
    assert "/api/admin/provider-credentials/codex/device/status/{session_id}" in paths
    assert "/api/admin/provider-credentials/codex/device/poll/{session_id}" in paths
    assert "/api/admin/provider-credentials/codex/refresh/{credential_id}" in paths
    assert "/api/admin/provider-credentials/codex/revoke/{credential_id}" in paths
    assert "/api/admin/provider-credentials/codex/disconnect/{credential_id}" in paths
