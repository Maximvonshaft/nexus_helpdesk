from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_control_tower_has_one_business_implementation():
    compatibility = ROOT / "app/services/control_tower_service.py"
    canonical = ROOT / "app/services/canonical_control_tower_service.py"

    assert not compatibility.exists()
    canonical_source = canonical.read_text(encoding="utf-8")
    assert "UserRole" not in canonical_source
    assert "has_global_case_visibility" in canonical_source


def test_control_tower_backend_emits_only_canonical_routes():
    source = (ROOT / "app/services/canonical_control_tower_service.py").read_text(encoding="utf-8")
    for legacy in ("/accounts", "/outbound-email", "/ai-control", "/bulletins", "/users", "/email", "/webcall"):
        assert legacy not in source
    for canonical in ("/workspace", "/knowledge", "/channels", "/runtime", "/control-tower"):
        assert canonical in source
