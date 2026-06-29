from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def test_candidate_compose_joins_external_runtime_network() -> None:
    compose = (ROOT / "deploy" / "docker-compose.candidate.yml").read_text(encoding="utf-8")

    assert "CANDIDATE_EXTERNAL_NETWORK" in compose
    assert "external: true" in compose
    assert re.search(
        r"app-candidate:\n(?:.*\n)*?    networks:\n"
        r"      - candidate\n"
        r"      - production_runtime\n",
        compose,
    )
    assert re.search(
        r"networks:\n"
        r"  candidate: \{\}\n"
        r"  production_runtime:\n"
        r"    name: \$\{CANDIDATE_EXTERNAL_NETWORK:-deploy_default\}\n"
        r"    external: true\n",
        compose,
    )


def test_candidate_env_example_documents_external_network() -> None:
    env_example = (ROOT / "deploy" / ".env.candidate.example").read_text(encoding="utf-8")

    assert "CANDIDATE_APP_PORT=18082" in env_example
    assert "CANDIDATE_EXTERNAL_NETWORK=deploy_default" in env_example
