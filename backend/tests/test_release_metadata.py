from __future__ import annotations

import os
import subprocess
from pathlib import Path

from app.services.release_metadata import runtime_identity, runtime_identity_status


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "export_release_metadata.sh"


def test_runtime_identity_uses_explicit_env_values() -> None:
    env = {
        "GIT_SHA": "abc123",
        "BUILD_TIME": "20260527T190000Z",
        "IMAGE_TAG": "nexusdesk/helpdesk:main-abc123-20260527T190000Z",
        "APP_VERSION": "main-abc123",
        "FRONTEND_BUILD_SHA": "abc123",
    }

    assert runtime_identity(env=env, default_app_version="server") == {
        "app_version": "main-abc123",
        "git_sha": "abc123",
        "image_tag": "nexusdesk/helpdesk:main-abc123-20260527T190000Z",
        "build_time": "20260527T190000Z",
        "frontend_build_sha": "abc123",
    }


def test_runtime_identity_defaults_without_env_values() -> None:
    assert runtime_identity(env={}, default_app_version="server") == {
        "app_version": "server",
        "git_sha": "unknown",
        "image_tag": "unknown",
        "build_time": "unknown",
        "frontend_build_sha": "unknown",
    }


def test_runtime_identity_status_marks_missing_metadata() -> None:
    assert runtime_identity_status(env={}, default_app_version="server") == {
        "app_version": "server",
        "git_sha": "unknown",
        "image_tag": "unknown",
        "build_time": "unknown",
        "frontend_build_sha": "unknown",
        "release_metadata_source": "environment",
        "release_metadata_complete": False,
        "release_metadata_missing": [
            "git_sha",
            "image_tag",
            "build_time",
            "frontend_build_sha",
        ],
    }


def test_runtime_identity_status_marks_complete_metadata() -> None:
    data = runtime_identity_status(
        env={
            "GIT_SHA": "abc123",
            "BUILD_TIME": "20260629T120000Z",
            "IMAGE_TAG": "nexusdesk/helpdesk:main-abc123-20260629T120000Z",
            "FRONTEND_BUILD_SHA": "abc123",
        },
        default_app_version="server",
    )

    assert data["release_metadata_complete"] is True
    assert data["release_metadata_missing"] == []


def test_export_release_metadata_stdout_is_non_secret() -> None:
    result = subprocess.run(
        ["sh", str(SCRIPT)],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_SHA": "abcdef1234567890", "BUILD_TIME": "20260527T190000Z"},
    )

    output = result.stdout
    assert "GIT_SHA=abcdef1234567890" in output
    assert "FRONTEND_BUILD_SHA=abcdef1234567890" in output
    assert "BUILD_TIME=20260527T190000Z" in output
    assert "APP_VERSION=main-abcdef123456" in output
    assert "IMAGE_TAG=nexusdesk/helpdesk:main-abcdef123456-20260527T190000Z" in output

    lowered = output.lower()
    assert "password=" not in lowered
    assert "token=" not in lowered
    assert "secret=" not in lowered


def test_export_release_metadata_refuses_sensitive_output_path(tmp_path: Path) -> None:
    bad_path = tmp_path / "token.env"
    result = subprocess.run(
        ["sh", str(SCRIPT), str(bad_path)],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env={**os.environ, "GIT_SHA": "abcdef1234567890", "BUILD_TIME": "20260527T190000Z"},
    )

    assert result.returncode != 0
    assert not bad_path.exists()
    assert "Refusing to write release metadata" in result.stderr
