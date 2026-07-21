from __future__ import annotations

from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


verify_path = "scripts/verify_repository.py"
verify = read(verify_path)
if '    "scripts/ci/check_agent_runtime_residue.py",\n' not in verify:
    verify = replace_once(
        verify,
        '    "scripts/ci/check_legacy_surface_registry.py",\n',
        '    "scripts/ci/check_legacy_surface_registry.py",\n'
        '    "scripts/ci/check_agent_runtime_residue.py",\n',
        label="required generic runtime gate",
    )
if 'failures.extend(_qualification_failures("scripts/ci/check_agent_runtime_residue.py"))' not in verify:
    verify = replace_once(
        verify,
        '    failures.extend(_qualification_failures("scripts/ci/check_legacy_surface_registry.py"))\n',
        '    failures.extend(_qualification_failures("scripts/ci/check_legacy_surface_registry.py"))\n'
        '    failures.extend(_qualification_failures("scripts/ci/check_agent_runtime_residue.py"))\n',
        label="static generic runtime gate",
    )
write(verify_path, verify)

architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
test = '''


def test_canonical_static_authority_runs_agent_runtime_residue_gate() -> None:
    source = Path("scripts/verify_repository.py").read_text(encoding="utf-8")
    assert '"scripts/ci/check_agent_runtime_residue.py"' in source
    assert (
        '_qualification_failures("scripts/ci/check_agent_runtime_residue.py")'
        in source
    )
'''
if "test_canonical_static_authority_runs_agent_runtime_residue_gate" not in architecture:
    architecture = architecture.rstrip() + test
write(architecture_path, architecture)

assert '_qualification_failures("scripts/ci/check_agent_runtime_residue.py")' in read(verify_path)
