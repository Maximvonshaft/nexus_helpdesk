from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "deploy" / "prepare_production_release_env.sh"
SHA = "0123456789abcdef0123456789abcdef01234567"
BUILD_TIME = "20260712T110000Z"
APP_VERSION = f"candidate-{SHA[:12]}"
IMAGE_TAG = f"ghcr.io/example/nexus:{APP_VERSION}-{BUILD_TIME}"


def _bash_path(path: Path) -> str:
    value = str(path)
    if os.name != "nt":
        return value
    drive, tail = os.path.splitdrive(value)
    return f"/{drive[0].lower()}{tail.replace(chr(92), '/')}"


def _bash_executable() -> str:
    if os.name != "nt":
        return "bash"
    git_bash = Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "Git" / "bin" / "bash.exe"
    return str(git_bash) if git_bash.is_file() else "bash"


def _metadata(**overrides: str) -> str:
    values = {
        "GIT_SHA": SHA,
        "COMMIT_SHA": SHA,
        "APP_GIT_SHA": SHA,
        "BUILD_TIME": BUILD_TIME,
        "APP_BUILD_TIME": BUILD_TIME,
        "APP_VERSION": APP_VERSION,
        "IMAGE_TAG": IMAGE_TAG,
        "APP_IMAGE_TAG": IMAGE_TAG,
        "FRONTEND_BUILD_SHA": SHA,
        "APP_FRONTEND_BUILD_SHA": SHA,
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run(
    tmp_path: Path,
    metadata: str,
    *,
    host_port: str = "",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    metadata_path = tmp_path / "release-metadata.env"
    prod_path = tmp_path / ".env.prod"
    output_path = tmp_path / ".env.prod.next"
    metadata_path.write_text(metadata, encoding="utf-8", newline="\n")
    prod_path.write_text(
        "APP_ENV=production\n"
        "ENABLE_OUTBOUND_DISPATCH=false\n"
        "CUSTOM_SETTING=keep\n",
        encoding="utf-8",
        newline="\n",
    )
    output_path.write_text("STALE_CANDIDATE=true\n", encoding="utf-8", newline="\n")
    env = os.environ.copy()
    env.update(
        {
            "PROD_ENV": _bash_path(prod_path),
            "OUTPUT_ENV": _bash_path(output_path),
            "APP_HOST_PORT_OVERRIDE": host_port,
        }
    )
    completed = subprocess.run(
        [_bash_executable(), _bash_path(SCRIPT), _bash_path(metadata_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed, output_path


def test_canonical_metadata_is_written_atomically_with_mode_0600(
    tmp_path: Path,
) -> None:
    completed, output_path = _run(tmp_path, _metadata(), host_port="18086")
    assert completed.returncode == 0, completed.stderr
    output = output_path.read_text(encoding="utf-8")
    assert "STALE_CANDIDATE=true\n" not in output
    assert "CUSTOM_SETTING=keep\n" in output
    assert "ENABLE_OUTBOUND_DISPATCH=false\n" in output
    assert f"GIT_SHA={SHA}\n" in output
    assert f"IMAGE_TAG={IMAGE_TAG}\n" in output
    assert "APP_HOST_PORT=18086\n" in output
    if os.name != "nt":
        assert stat.S_IMODE(output_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "metadata",
    [
        _metadata(IMAGE_TAG=r"safe\nENABLE_OUTBOUND_DISPATCH=true"),
        _metadata(GIT_SHA="not-a-sha"),
        _metadata(BUILD_TIME="2026-07-12T11:00:00Z"),
        _metadata(APP_VERSION="candidate;touch-pwned"),
        _metadata() + f"GIT_SHA={SHA}\n",
        _metadata() + "UNEXPECTED_KEY=value\n",
        _metadata(APP_IMAGE_TAG="ghcr.io/example/other:bad"),
        _metadata(
            IMAGE_TAG=(
                f"ghcr.io/example/nexus:old:{APP_VERSION}-{BUILD_TIME}"
            )
        ),
    ],
)
def test_invalid_metadata_fails_without_output(
    tmp_path: Path,
    metadata: str,
) -> None:
    completed, output_path = _run(tmp_path, metadata)
    assert completed.returncode != 0
    assert not output_path.exists()
    assert "PRODUCTION_RELEASE_ENV_PREPARED=true" not in completed.stdout


def test_carriage_return_metadata_fails_without_output(tmp_path: Path) -> None:
    metadata_path = tmp_path / "release-metadata.env"
    prod_path = tmp_path / ".env.prod"
    output_path = tmp_path / ".env.prod.next"
    metadata_path.write_bytes(_metadata().replace("\n", "\r\n").encode())
    prod_path.write_text("APP_ENV=production\n", encoding="utf-8")
    output_path.write_text("STALE_CANDIDATE=true\n", encoding="utf-8")
    env = os.environ.copy()
    env.update({"PROD_ENV": _bash_path(prod_path), "OUTPUT_ENV": _bash_path(output_path)})
    completed = subprocess.run(
        [_bash_executable(), _bash_path(SCRIPT), _bash_path(metadata_path)],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode != 0
    assert not output_path.exists()


@pytest.mark.parametrize(
    "host_port",
    [r"18086\nENABLE_OUTBOUND_DISPATCH=true", "0", "65536", "abc"],
)
def test_invalid_host_port_override_fails_closed(
    tmp_path: Path,
    host_port: str,
) -> None:
    completed, output_path = _run(
        tmp_path,
        _metadata(),
        host_port=host_port,
    )
    assert completed.returncode != 0
    assert not output_path.exists()
