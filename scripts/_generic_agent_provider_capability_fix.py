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


schemas_path = "backend/app/services/provider_runtime/schemas.py"
schemas = read(schemas_path)
schemas = replace_once(
    schemas,
    "    # webchat_runtime_reply remains as a compatibility capability name.\n"
    "    # Both fields describe the same generic Agent-turn protocol.\n"
    "    agent_turn: bool = False\n"
    "    webchat_runtime_reply: bool = False\n",
    "    agent_turn: bool = False\n",
    label="provider capability compatibility field",
)
write(schemas_path, schemas)

adapter_path = "backend/app/services/provider_runtime/adapters/private_ai_runtime.py"
adapter = read(adapter_path)
adapter = replace_once(
    adapter,
    "        agent_turn=True,\n        webchat_runtime_reply=True,\n",
    "        agent_turn=True,\n",
    label="private provider compatibility capability",
)
write(adapter_path, adapter)

# The preceding migration script temporarily adds an over-broad token marker.
# Replace it with production-code signatures that cannot match migration history
# or ordinary naming in tests/docs.
residue_path = "scripts/ci/check_agent_runtime_residue.py"
residue = read(residue_path)
residue = residue.replace(
    '    "nexus.webchat_runtime_reply",\n    "webchat_runtime_reply",\n',
    '    "nexus.webchat_runtime_reply",\n',
)
for marker in (
    '    "_WEBCHAT_RUNTIME_SCENARIO",\n',
    '    "webchat_runtime_reply: bool",\n',
    '    "webchat_runtime_reply=True",\n',
):
    if marker not in residue:
        residue = residue.replace(
            '    "nexus.webchat_runtime_reply",\n',
            '    "nexus.webchat_runtime_reply",\n' + marker,
            1,
        )
write(residue_path, residue)

architecture_path = "backend/tests/test_agent_runtime_architecture.py"
architecture = read(architecture_path)
test = '''


def test_provider_capability_contract_has_only_agent_turn() -> None:
    schemas = Path(
        "backend/app/services/provider_runtime/schemas.py"
    ).read_text(encoding="utf-8")
    adapter = Path(
        "backend/app/services/provider_runtime/adapters/private_ai_runtime.py"
    ).read_text(encoding="utf-8")
    assert "agent_turn: bool" in schemas
    assert "webchat_runtime_reply: bool" not in schemas
    assert "agent_turn=True" in adapter
    assert "webchat_runtime_reply=True" not in adapter
'''
if "test_provider_capability_contract_has_only_agent_turn" not in architecture:
    architecture = architecture.rstrip() + test
write(architecture_path, architecture)

assert "webchat_runtime_reply: bool" not in read(schemas_path)
assert "webchat_runtime_reply=True" not in read(adapter_path)
assert '    "webchat_runtime_reply",\n' not in read(residue_path)
