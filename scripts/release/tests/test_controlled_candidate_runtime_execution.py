from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
GATE = (ROOT / "scripts/release/run_controlled_rc_gate.sh").read_text(
    encoding="utf-8"
)
ASSURANCE = (
    ROOT / "scripts/release/run_controlled_image_assurance.sh"
).read_text(encoding="utf-8")
RUNTIME = (
    ROOT / "scripts/release/manage_controlled_assurance_runtime.sh"
).read_text(encoding="utf-8")
WORKFLOW = (
    ROOT / ".github/workflows/controlled-candidate-convergence.yml"
).read_text(encoding="utf-8")


def test_rc_gate_installs_the_exact_application_dependency_closure_first() -> None:
    install = (
        "python -m pip install --disable-pip-version-check "
        "-r backend/requirements.txt"
    )
    assert install in GATE
    assert GATE.index(install) < GATE.index("python -m unittest -v")
    assert GATE.index(install) < GATE.index(
        "bash scripts/release/run_rc_test_candidate.sh"
    )
    assert "pip install PyYAML" not in GATE
    assert "manage_controlled_assurance_runtime.sh" in GATE


def test_assurance_reuses_an_existing_runtime_or_owns_a_bounded_fallback() -> None:
    assert 'ASSURANCE_CONTAINER_NAME:-nexus-ci-candidate' in ASSURANCE
    assert 'docker inspect --format \'{{.State.Status}}\'' in ASSURANCE
    start = "manage_controlled_assurance_runtime.sh start"
    cleanup = "manage_controlled_assurance_runtime.sh cleanup"
    assert start in ASSURANCE
    assert cleanup in ASSURANCE
    assert ASSURANCE.index(start) < ASSURANCE.index("docker exec -i")
    assert "trap cleanup_assurance_runtime EXIT" in ASSURANCE
    assert ASSURANCE.index("trap cleanup_assurance_runtime EXIT") < ASSURANCE.index(
        "docker exec -i"
    )
    assert '"${assurance_container}" \\\n  python -' in ASSURANCE


def test_fallback_runtime_is_digest_pinned_migrated_and_fail_closed() -> None:
    for marker in (
        'RC_POSTGRES_IMAGE_PIN:?RC_POSTGRES_IMAGE_PIN required',
        '@sha256:[0-9a-f]{64}$',
        'python -m alembic upgrade head >&2',
        'PROVIDER_RUNTIME_ENABLED=false',
        'PROVIDER_RUNTIME_KILL_SWITCH=true',
        'PROVIDER_RUNTIME_CANARY_PERCENT=0',
        'PRIVATE_AI_RUNTIME_ENABLED=false',
        'WEBCHAT_AI_ENABLED=false',
        'WEBCHAT_HUMAN_CALL_ENABLED=false',
        'WEBCHAT_LIVE_AI_VOICE_ENABLED=false',
        'ENABLE_OUTBOUND_DISPATCH=false',
        'OUTBOUND_PROVIDER=disabled',
        'WHATSAPP_NATIVE_ENABLED=false',
        'SPEEDAF_MCP_ENABLED=false',
        'OPERATIONS_DISPATCH_MODE=disabled',
    ):
        assert marker in RUNTIME
    assert ":latest" not in RUNTIME
    assert "docker build" not in RUNTIME
    assert RUNTIME.count('docker rm -f "${candidate_name}"') == 1
    assert RUNTIME.count('docker rm -f "${database_name}"') == 1
    assert 'docker network rm "${network_name}"' in RUNTIME
    assert "trap cleanup ERR" in RUNTIME
    assert "printf '%s\\n' \"${candidate_name}\"" in RUNTIME


def test_publisher_still_uses_one_rc_build_and_one_assurance_authority() -> None:
    assert "bash scripts/release/run_controlled_rc_gate.sh" in WORKFLOW
    assert "bash scripts/release/run_controlled_image_assurance.sh" in WORKFLOW
    assert "docker build " not in WORKFLOW
    assert WORKFLOW.count("run_controlled_image_assurance.sh") == 1
