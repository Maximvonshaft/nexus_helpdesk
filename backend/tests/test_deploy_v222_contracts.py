from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_deploy_v222_server_compose_exists():
    compose = ROOT / "deploy" / "docker-compose.server.yml"
    assert compose.exists()


def test_deploy_v222_worker_processes_remain_declared():
    compose_text = (ROOT / "deploy" / "docker-compose.server.yml").read_text(encoding="utf-8")
    assert "worker-outbound" in compose_text
    assert "worker-background" in compose_text
    assert "worker-webchat-ai" in compose_text
    assert "postgres:" in compose_text
