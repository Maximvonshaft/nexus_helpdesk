from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "render_activation_configuration.py"
SPEC = importlib.util.spec_from_file_location("render_activation_configuration", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _values() -> dict[str, str]:
    return {
        "PRODUCTION_PROFILE": "provider_canary",
        "PROVIDER_RUNTIME_ENABLED": "true",
        "PROVIDER_RUNTIME_TRAFFIC_MODE": "canary",
        "PROVIDER_RUNTIME_KILL_SWITCH": "false",
        "PROVIDER_RUNTIME_CANARY_PERCENT": "5",
        "LIVEKIT_URL": "wss://voice.example.test",
        "LIVEKIT_API_SECRET": "must-not-appear",
        "COMPOSE_PROFILES": "whatsapp-media,whatsapp-baileys",
    }


def test_render_is_deterministic_and_contains_no_secret_values() -> None:
    first = MODULE.render(_values())
    second = MODULE.render(dict(reversed(list(_values().items()))))

    assert first == second
    assert first["configuration_digest"].startswith("sha256:")
    rendered = str(first)
    assert "must-not-appear" not in rendered
    assert first["configuration"]["secret_presence"][
        "livekit_api_secret_configured"
    ] is True
    assert first["configuration"]["compose_profiles"] == [
        "whatsapp-baileys",
        "whatsapp-media",
    ]


def test_material_runtime_drift_changes_the_digest() -> None:
    original = MODULE.render(_values())
    changed = _values()
    changed["PROVIDER_RUNTIME_CANARY_PERCENT"] = "6"

    assert MODULE.render(changed)["configuration_digest"] != original[
        "configuration_digest"
    ]


def test_secret_rotation_does_not_expose_or_hash_secret_contents() -> None:
    first = _values()
    second = _values()
    second["LIVEKIT_API_SECRET"] = "rotated-secret-value"

    assert MODULE.render(first)["configuration_digest"] == MODULE.render(second)[
        "configuration_digest"
    ]
