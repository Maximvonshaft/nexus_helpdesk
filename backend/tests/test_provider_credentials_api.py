from app.api.admin_provider_credentials import router


def test_provider_credentials_admin_surface_disabled():
    assert router.prefix == "/api/admin/provider-credentials"
    assert router.routes == []
