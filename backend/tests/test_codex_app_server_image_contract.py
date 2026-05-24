from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_codex_app_server_proxy_scripts_are_copied_into_runtime_image():
    dockerfile = _read("Dockerfile")

    expected_copies = {
        "deploy/codex_app_server_bridge_proxy.py": "/app/deploy/",
        "deploy/codex_app_server_private_upstream_proxy.py": "/app/deploy/",
        "deploy/codex_private_reply_engine.py": "/app/deploy/",
    }
    for source, target in expected_copies.items():
        pattern = rf"^COPY\s+{re.escape(source)}\s+{re.escape(target)}\s*$"
        assert re.search(pattern, dockerfile, flags=re.MULTILINE), f"missing Dockerfile copy for {source}"


def test_codex_app_server_compose_commands_point_to_copied_scripts():
    dockerfile = _read("Dockerfile")
    compose = _read("deploy/docker-compose.server.yml")

    copied_scripts = set(re.findall(r"^COPY\s+deploy/([^\s]+)\s+/app/deploy/\s*$", dockerfile, flags=re.MULTILINE))
    command_scripts = set(re.findall(r"command:\s+python\s+/app/deploy/([^\s]+)", compose))

    assert "codex_app_server_bridge_proxy.py" in command_scripts
    assert "codex_app_server_private_upstream_proxy.py" in command_scripts
    assert "codex_private_reply_engine.py" in command_scripts
    assert command_scripts <= copied_scripts
