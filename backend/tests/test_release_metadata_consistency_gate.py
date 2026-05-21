import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "release_metadata_consistency_gate.py"

spec = importlib.util.spec_from_file_location("release_metadata_consistency_gate", SCRIPT)
assert spec is not None and spec.loader is not None
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def test_release_metadata_gate_passes_consistent_payloads():
    image = "nexusdesk/helpdesk:main-test-20260521T130459Z"
    result = gate.evaluate_consistency(
        docker_image=image,
        healthz={"image_tag": image},
        readyz={
            "image_tag": image,
            "database": "ok",
            "migration_revision": "20260518_0025",
        },
    )

    assert result["ok"] is True
    assert result["checks"] == {
        "docker_image_matches_healthz_image_tag": True,
        "healthz_image_tag_matches_readyz_image_tag": True,
        "readyz_database_ok": True,
        "readyz_migration_revision_non_empty": True,
    }


@pytest.mark.parametrize(
    "docker_image, healthz, readyz",
    [
        (
            "repo/app:new",
            {"image_tag": "repo/app:old"},
            {"image_tag": "repo/app:old", "database": "ok", "migration_revision": "rev"},
        ),
        (
            "repo/app:new",
            {"image_tag": "repo/app:new"},
            {"image_tag": "repo/app:other", "database": "ok", "migration_revision": "rev"},
        ),
        (
            "repo/app:new",
            {"image_tag": "repo/app:new"},
            {"image_tag": "repo/app:new", "database": "error", "migration_revision": "rev"},
        ),
        (
            "repo/app:new",
            {"image_tag": "repo/app:new"},
            {"image_tag": "repo/app:new", "database": "ok", "migration_revision": ""},
        ),
    ],
)
def test_release_metadata_gate_fails_on_drift_or_not_ready(docker_image, healthz, readyz):
    result = gate.evaluate_consistency(
        docker_image=docker_image,
        healthz=healthz,
        readyz=readyz,
    )

    assert result["ok"] is False
