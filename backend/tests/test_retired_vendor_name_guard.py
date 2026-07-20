from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN = ("open" + "claw").lower()
TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".css",
    ".env",
    ".example",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".mjs",
    ".py",
    ".sh",
    ".sql",
    ".svg",
    ".toml",
    ".ts",
    ".tsx",
    ".txt",
    ".yaml",
    ".yml",
}
SKIP_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".tmp-release-metadata",
    ".venv",
    "__pycache__",
    "artifacts",
    "dist",
    "frontend_dist",
    "node_modules",
    "probe_reports",
}


def _is_text_candidate(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in TEXT_SUFFIXES or path.name.endswith(".env.example")


def test_retired_vendor_name_is_absent_from_repo_paths_and_text() -> None:
    offenders: list[str] = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        rel = path.relative_to(ROOT).as_posix()
        if FORBIDDEN in rel.lower():
            offenders.append(rel)
            continue
        if not path.is_file() or not _is_text_candidate(path):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if FORBIDDEN in text.lower():
            offenders.append(rel)
    assert offenders == []
