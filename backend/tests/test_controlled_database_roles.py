from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "deploy" / "docker-compose.controlled.yml"
ENV_EXAMPLE = ROOT / "deploy" / ".env.controlled.example"


def _service_blocks(text: str) -> dict[str, str]:
    services_text = text.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    matches = list(re.finditer(r"(?m)^  ([a-z0-9-]+):\n", services_text))
    blocks: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(services_text)
        blocks[match.group(1)] = services_text[match.end():end]
    return blocks


def test_each_controlled_service_uses_its_database_identity():
    blocks = _service_blocks(COMPOSE.read_text(encoding="utf-8"))
    expected = {
        "migrate-controlled": "DATABASE_URL_MIGRATION",
        "app-controlled": "DATABASE_URL_APP",
        "worker-outbound-controlled": "DATABASE_URL_OUTBOUND",
        "worker-background-controlled": "DATABASE_URL_BACKGROUND",
        "worker-webchat-ai-controlled": "DATABASE_URL_WEBCHAT_AI",
        "worker-handoff-snapshot-controlled": "DATABASE_URL_HANDOFF",
    }
    assert set(expected).issubset(blocks)
    for service, variable in expected.items():
        block = blocks[service]
        assert f"DATABASE_URL: ${{{variable}:?" in block
        for other in set(expected.values()) - {variable}:
            assert other not in block


def test_controlled_environment_declares_distinct_database_users():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")
    values = {}
    for variable in (
        "DATABASE_URL_MIGRATION",
        "DATABASE_URL_APP",
        "DATABASE_URL_OUTBOUND",
        "DATABASE_URL_BACKGROUND",
        "DATABASE_URL_WEBCHAT_AI",
        "DATABASE_URL_HANDOFF",
    ):
        match = re.search(rf"(?m)^{variable}=postgresql\+psycopg://([^:]+):", text)
        assert match, variable
        values[variable] = match.group(1)
    assert len(set(values.values())) == len(values), values
    assert values["DATABASE_URL_MIGRATION"] == "nexus_migration"
    assert all(user != "nexus_migration" for variable, user in values.items() if variable != "DATABASE_URL_MIGRATION")


def test_shared_database_url_is_not_used_by_controlled_services():
    text = COMPOSE.read_text(encoding="utf-8")
    services_text = text.split("\nservices:\n", 1)[1]
    assert "DATABASE_URL: ${DATABASE_URL:" not in services_text
