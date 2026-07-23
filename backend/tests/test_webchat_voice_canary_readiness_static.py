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
    assert 'log "non_deploying=true"' in text
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


def test_webcall_canary_runs_current_authority_backend_and_frontend_gates():
    text = _read(SCRIPT)
    required = [
        "scripts/ci/check_telephony_authority_residue.py",
        "scripts/qualification/service_authority.py",
        "backend/tests/test_webchat_voice_api.py",
        "backend/tests/test_livekit_voice_provider.py",
        "backend/tests/test_livekit_agent_worker.py",
        "backend/tests/test_canonical_livekit_telephony.py",
        "backend/tests/test_public_voice_route_compatibility.py",
        "npm run verify",
        "npm run e2e",
    ]
    for marker in required:
        assert marker in text


def test_webcall_canary_checks_canonical_runtime_and_retired_page():
    text = _read(SCRIPT)
    required = [
        "/api/webchat/voice/runtime-config",
        '"media_plane"',
        "/webchat/voice/canary-retired",
        "retired Voice page is still callable",
        "microphone=\\(\\)",
        "MANUAL_PROVIDER_PROOF_REQUIRED=YES",
        "CANARY_RESULT=PASS",
    ]
    for marker in required:
        assert marker in text
    for retired in [
        "webchat-voice.tsx",
        "AgentWebCallPanel",
        "webchat_voice_service.py",
        "webchatVoiceApi.ts",
        "webchatVoiceTypes.ts",
    ]:
        assert retired not in text


def test_webcall_canary_runbook_documents_release_blocker_policy():
    text = _read(RUNBOOK)
    required = [
        "non-production readiness gate",
        "The canary must not:",
        "- deploy or modify production;",
        "microphone permission before an explicit join or accept action",
        "Any failed gate or missing Provider evidence blocks release promotion",
        "/webcall/{voice_session_id}",
        "warm consultation start/complete/cancel",
    ]
    for marker in required:
        assert marker in text
