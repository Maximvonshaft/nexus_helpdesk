from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "webcall_canary_readiness.sh"
RUNBOOK = ROOT / "docs" / "runbooks" / "webcall_canary_readiness.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_webcall_canary_artifacts_exist():
    assert SCRIPT.exists()
    assert RUNBOOK.exists()


def test_webcall_canary_is_non_deploying():
    text = _read(SCRIPT)
    forbidden = [
        "docker compose up",
        "docker-compose up",
        "kubectl apply",
        "deploy/.env.prod >",
        "deploy/.env.prod=",
        "git push",
    ]
    for marker in forbidden:
        assert marker not in text


def test_webcall_canary_runs_required_backend_and_frontend_gates():
    text = _read(SCRIPT)
    required = [
        "backend/tests/test_webchat_voice_api.py",
        "backend/tests/test_livekit_voice_provider.py",
        "backend/tests/test_webchat_voice_room_compensation.py",
        "backend/tests/test_webchat_voice_static_headers.py",
        "backend/tests/test_webchat_voice_mock_ui_static.py",
        "backend/tests/test_webchat_voice_canary_readiness_static.py",
        "npm run typecheck",
        "npm run build",
        "npm test",
    ]
    for marker in required:
        assert marker in text


def test_webcall_canary_checks_click_to_accept_and_runtime_readiness():
    text = _read(SCRIPT)
    required = [
        "createLocalAudioTrack",
        "accepted.participant_token",
        "runtime-config",
        "webchat-voice",
        "CLICK_TO_ACCEPT_STATIC_CHECK=PASS",
        "TOKEN_SECRET_CLASSIFICATION=PASS",
    ]
    for marker in required:
        assert marker in text


def test_webcall_canary_runbook_documents_release_blocker_policy():
    text = _read(RUNBOOK)
    required = [
        "non-deploying",
        "modify deploy/.env.prod",
        "request microphone permission during page load",
        "CANARY_RESULT=PASS",
        "Any failure blocks release promotion",
    ]
    for marker in required:
        assert marker in text
