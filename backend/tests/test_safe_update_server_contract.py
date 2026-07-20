from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="safe update contract requires POSIX filesystem semantics")

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "safe_update_server.sh"
PROTECTED_FILES = {
    "Dockerfile": "FROM scratch\n",
    "deploy/.env.controlled": "SECRET_VALUE=top-secret\n",
    "deploy/docker-compose.controlled.yml": "services: {}\n",
    "deploy/nginx/default.conf": "server { listen 80; }\n",
    "backend/.env": "ANOTHER_SECRET=do-not-log\n",
}


def _fake_repo(
    tmp_path: Path,
    *,
    protected_files: dict[str, str] | None = None,
) -> Path:
    repo = tmp_path / "repo"
    script = repo / "scripts" / "deploy" / "safe_update_server.sh"
    script.parent.mkdir(parents=True)
    script.write_text(SOURCE_SCRIPT.read_text(encoding="utf-8"), encoding="utf-8")
    script.chmod(0o755)

    for relative_path, content in (protected_files or PROTECTED_FILES).items():
        target = repo / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        target.chmod(0o644)
    return repo


def _run(
    repo: Path,
    backup_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["BACKUP_DIR"] = str(backup_dir)
    env.update(extra_env or {})
    return subprocess.run(
        [
            "bash",
            "-c",
            'umask 022; exec bash "$1"',
            "safe-update-test",
            str(repo / "scripts" / "deploy" / "safe_update_server.sh"),
        ],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_default_backup_root_matches_repository_ignore_contract() -> None:
    script = SOURCE_SCRIPT.read_text(encoding="utf-8")
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert '$ROOT_DIR/deploy_backups/$STAMP' in script
    assert '$ROOT_DIR/.deploy_backups/$STAMP' not in script
    assert "/deploy_backups/" in gitignore


def test_backup_is_private_atomic_and_checksum_verified(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    backup_dir = tmp_path / "backups" / "candidate"

    completed = _run(repo, backup_dir)

    assert completed.returncode == 0, completed.stderr
    assert backup_dir.is_dir()
    assert _mode(backup_dir) == 0o700
    assert "Configuration backup verified" in completed.stdout
    assert "protected_files=5" in completed.stdout
    assert "top-secret" not in completed.stdout + completed.stderr
    assert "do-not-log" not in completed.stdout + completed.stderr

    for relative_path, expected_content in PROTECTED_FILES.items():
        copied = backup_dir / relative_path
        assert copied.read_text(encoding="utf-8") == expected_content
        assert _mode(copied) == 0o600

    for directory in [path for path in backup_dir.rglob("*") if path.is_dir()]:
        assert _mode(directory) == 0o700

    manifest = backup_dir / "SHA256SUMS"
    assert _mode(manifest) == 0o600
    assert len(manifest.read_text(encoding="utf-8").splitlines()) == len(
        PROTECTED_FILES
    )
    verification = subprocess.run(
        ["sha256sum", "--check", "--strict", "SHA256SUMS"],
        cwd=backup_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verification.returncode == 0, verification.stderr
    assert not list(backup_dir.parent.glob(f"{backup_dir.name}.tmp.*"))


def test_existing_target_fails_without_overwrite_or_mixed_evidence(
    tmp_path: Path,
) -> None:
    repo = _fake_repo(tmp_path)
    backup_dir = tmp_path / "backups" / "candidate"
    backup_dir.mkdir(parents=True)
    sentinel = backup_dir / "existing.txt"
    sentinel.write_text("keep-me\n", encoding="utf-8")

    completed = _run(repo, backup_dir)

    assert completed.returncode == 2
    assert "refusing existing backup target" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep-me\n"
    assert not (backup_dir / "SHA256SUMS").exists()
    assert not list(backup_dir.parent.glob(f"{backup_dir.name}.tmp.*"))


def test_dangling_symlink_target_fails_without_replacement(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    backup_parent = tmp_path / "backups"
    backup_parent.mkdir()
    backup_dir = backup_parent / "candidate"
    missing_target = tmp_path / "missing-target"
    backup_dir.symlink_to(missing_target)

    completed = _run(repo, backup_dir)

    assert completed.returncode == 2
    assert "refusing existing backup target" in completed.stderr
    assert backup_dir.is_symlink()
    assert os.readlink(backup_dir) == str(missing_target)
    assert not missing_target.exists()
    assert not list(backup_parent.glob(f"{backup_dir.name}.tmp.*"))


def test_target_appearing_during_publication_is_not_overwritten(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    backup_dir = tmp_path / "backups" / "candidate"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_mv = fake_bin / "mv"
    fake_mv.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
destination="${!#}"
mkdir -p -- "$destination"
printf 'attacker-owned\n' > "$destination/existing.txt"
""",
        encoding="utf-8",
    )
    fake_mv.chmod(0o755)

    completed = _run(
        repo,
        backup_dir,
        extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
    )

    assert completed.returncode == 5
    assert "backup target appeared during publication" in completed.stderr
    assert (backup_dir / "existing.txt").read_text(encoding="utf-8") == (
        "attacker-owned\n"
    )
    assert not (backup_dir / "SHA256SUMS").exists()
    assert not list(backup_dir.parent.glob(f"{backup_dir.name}.tmp.*"))


def test_symlinked_protected_source_fails_closed(tmp_path: Path) -> None:
    repo = _fake_repo(tmp_path)
    protected = repo / "deploy" / ".env.controlled"
    protected.unlink()
    outside = tmp_path / "outside-secret"
    outside.write_text("OUTSIDE_SECRET=value\n", encoding="utf-8")
    protected.symlink_to(outside)
    backup_dir = tmp_path / "backups" / "candidate"

    completed = _run(repo, backup_dir)

    assert completed.returncode == 3
    assert "refusing symlinked protected file: deploy/.env.controlled" in completed.stderr
    assert not backup_dir.exists()
    assert "OUTSIDE_SECRET" not in completed.stdout + completed.stderr
    assert not list(backup_dir.parent.glob(f"{backup_dir.name}.tmp.*"))


def test_missing_optional_files_are_reported_without_fake_manifest_entries(
    tmp_path: Path,
) -> None:
    repo = _fake_repo(tmp_path, protected_files={"Dockerfile": "FROM scratch\n"})
    backup_dir = tmp_path / "backups" / "candidate"

    completed = _run(repo, backup_dir)

    assert completed.returncode == 0, completed.stderr
    assert "protected_files=1" in completed.stdout
    assert "Missing optional protected file: deploy/.env.controlled" in completed.stdout
    assert "Missing optional protected file: backend/.env" in completed.stdout
    manifest_lines = (backup_dir / "SHA256SUMS").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(manifest_lines) == 1
    assert manifest_lines[0].endswith("  Dockerfile")
    assert not (backup_dir / "deploy" / ".env.controlled").exists()


@pytest.mark.parametrize(
    "relative_path",
    ["Dockerfile", "deploy/.env.controlled", "backend/.env"],
)
def test_non_regular_protected_source_fails_closed(
    tmp_path: Path,
    relative_path: str,
) -> None:
    repo = _fake_repo(tmp_path)
    target = repo / relative_path
    target.unlink()
    target.mkdir()
    backup_dir = tmp_path / "backups" / "candidate"

    completed = _run(repo, backup_dir)

    assert completed.returncode == 3
    assert f"refusing non-regular protected file: {relative_path}" in completed.stderr
    assert not backup_dir.exists()
