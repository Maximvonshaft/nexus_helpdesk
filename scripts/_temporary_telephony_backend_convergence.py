#!/usr/bin/env python3
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path.cwd()
TARGET_BRANCH = "feat/canonical-livekit-telephony"
EXPECTED_PRODUCT_HEAD = "5df55702dd9d29b791944dd1d3961eb5d5ad06d3"


def run(*args: str, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd or ROOT, env=env, check=True)


def output(*args: str) -> str:
    return subprocess.run(
        args,
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def replace_exact(path: Path, old: str, new: str, label: str) -> None:
    source = path.read_text(encoding="utf-8")
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one occurrence, got {count}")
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def remove_exact(path: Path, block: str, label: str) -> None:
    replace_exact(path, block, "", label)


def main() -> int:
    temporary_head = output("git", "rev-parse", "HEAD")
    run("git", "fetch", "--no-tags", "--quiet", "origin", TARGET_BRANCH)
    if output("git", "rev-parse", f"origin/{TARGET_BRANCH}") != temporary_head:
        raise RuntimeError("temporary branch moved before convergence")

    run("git", "reset", "--hard", EXPECTED_PRODUCT_HEAD)
    run("git", "clean", "-ffdx")

    # Retire tests that exclusively assert the removed Media Edge/AudioWorklet chain.
    for relative in (
        "backend/tests/test_live_voice_credential_rotation_runbook.py",
        "backend/tests/test_webchat_voice_p0_static.py",
    ):
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"retired test missing before deletion: {relative}")
        path.unlink()

    # Keep the security-header suite, but remove the retired upstream health proxy case.
    static_headers = ROOT / "backend/tests/test_webchat_voice_static_headers.py"
    remove_exact(
        static_headers,
        '''def test_live_voice_enabled_health_route_is_same_origin_proxy_scope(monkeypatch):
    client = _client(
        monkeypatch,
        WEBCHAT_VOICE_ENABLED="false",
        WEBCHAT_HUMAN_CALL_ENABLED="false",
        WEBCHAT_LIVE_AI_VOICE_ENABLED="true",
        WEBCHAT_VOICE_PROVIDER="mock",
        WEBCHAT_VOICE_CONNECT_SRC="",
        LIVE_VOICE_UPSTREAM_WS_URL="ws://127.0.0.1:1/live/ws",
        LIVE_VOICE_UPSTREAM_HEALTH_URL="http://127.0.0.1:1/live/health",
        LIVE_VOICE_UPSTREAM_TOKEN="unit-secret-token",
    )

    response = client.get("/webchat/live/health")

    assert response.status_code == 503
    assert _permissions(response) == "camera=(), microphone=(self), geolocation=()"
    assert "unit-secret-token" not in response.text
    assert "connect-src 'self'" in _csp(response)


''',
        "retired live voice health proxy test",
    )

    # Stabilize the compatibility redirect test against cross-test environment state.
    public_route = ROOT / "backend/tests/test_public_voice_route_compatibility.py"
    replace_exact(
        public_route,
        '''    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    client = TestClient(app, raise_server_exceptions=False)
''',
        '''    monkeypatch.setenv("WEBCHAT_HUMAN_CALL_ENABLED", "true")
    monkeypatch.setenv("WEBCHAT_VOICE_PROVIDER", "mock")
    monkeypatch.setenv("WEBCHAT_VOICE_ALLOWED_PATH_PREFIXES", "/webchat/voice,/webcall")
    client = TestClient(app, raise_server_exceptions=False)
''',
        "public voice redirect environment",
    )

    # Align the broad Voice API suite with mandatory routing scope and channel capacity.
    voice_api = ROOT / "backend/tests/test_webchat_voice_api.py"
    replace_exact(
        voice_api,
        "from app.models_agent_routing import ConversationControl\n",
        "from app.models_agent_routing import ConversationControl, OperatorAgentState\n",
        "voice API routing model imports",
    )
    replace_exact(
        voice_api,
        "from app.webchat_models import WebchatConversation, WebchatEvent, WebchatMessage  # noqa: F401 - ensure metadata registration\n",
        "from app.webchat_models import WebchatConversation, WebchatEvent, WebchatHandoffRequest, WebchatMessage  # noqa: F401 - ensure metadata registration\n",
        "voice API handoff model import",
    )
    replace_exact(
        voice_api,
        '''        for user in users:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.commit()
''',
        '''        for user in users:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.flush()
        now = utc_now()
        db.add(
            OperatorAgentState(
                user_id=9202,
                status="online",
                max_concurrent_conversations=3,
                max_concurrent_voice_calls=5,
                voice_wrap_up_seconds=0,
                last_heartbeat_at=now,
                status_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            OperatorQueueScopeGrant(
                user_id=9202,
                tenant_key="pytest-voice",
                country_code="ME",
                channel_key="website",
                enabled=True,
                granted_by=9202,
            )
        )
        db.commit()
''',
        "voice API agent routing fixture",
    )
    replace_exact(
        voice_api,
        '''        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        ticket = Ticket(
''',
        '''        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        control = db.query(ConversationControl).filter(
            ConversationControl.conversation_id == conversation.id
        ).one()
        control.country_code = "ME"
        ticket = Ticket(
''',
        "ticket-backed voice routing scope fixture",
    )
    source = voice_api.read_text(encoding="utf-8")
    if source.count('"not_executed"') < 2 or source.count('"provider_adapter_pending"') < 2:
        raise RuntimeError("legacy provider command expectations were not found")
    source = source.replace('"not_executed"', '"executed"')
    source = source.replace('"provider_adapter_pending"', '"provider_command_completed"')
    voice_api.write_text(source, encoding="utf-8")

    # Channel workbench tests now provision the same routing facts required in production.
    workbench = ROOT / "backend/tests/test_channel_workbench_backend_contracts.py"
    replace_exact(
        workbench,
        "from app.models import AdminAuditLog, BackgroundJob, Customer, OutboundEmailAccount, Team, Ticket, TicketAttachment, TicketEvent, TicketInboundEmailMessage, TicketOutboundMessage, User  # noqa: E402\n",
        "from app.models import AdminAuditLog, BackgroundJob, Customer, OutboundEmailAccount, Team, Ticket, TicketAttachment, TicketEvent, TicketInboundEmailMessage, TicketOutboundMessage, User  # noqa: E402\nfrom app.models_agent_routing import ConversationControl, OperatorAgentState  # noqa: E402\nfrom app.operator_models import OperatorQueueScopeGrant  # noqa: E402\n",
        "channel workbench routing imports",
    )
    replace_exact(
        workbench,
        "from app.settings import get_settings  # noqa: E402\n",
        "from app.settings import get_settings  # noqa: E402\nfrom app.utils.time import utc_now  # noqa: E402\n",
        "channel workbench time import",
    )
    replace_exact(
        workbench,
        '''    conversation = db_session.query(WebchatConversation).filter(
        WebchatConversation.public_id == conversation_id
    ).one()
    ticket = Ticket(
''',
        '''    conversation = db_session.query(WebchatConversation).filter(
        WebchatConversation.public_id == conversation_id
    ).one()
    control = db_session.query(ConversationControl).filter(
        ConversationControl.conversation_id == conversation.id
    ).one()
    control.country_code = "ME"
    now = utc_now()
    for operator in db_session.query(User).filter(User.is_active.is_(True)).all():
        state = db_session.query(OperatorAgentState).filter(
            OperatorAgentState.user_id == operator.id
        ).first()
        if state is None:
            db_session.add(
                OperatorAgentState(
                    user_id=operator.id,
                    status="online",
                    max_concurrent_conversations=3,
                    max_concurrent_voice_calls=1,
                    voice_wrap_up_seconds=0,
                    last_heartbeat_at=now,
                    status_changed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        grant = db_session.query(OperatorQueueScopeGrant).filter(
            OperatorQueueScopeGrant.user_id == operator.id,
            OperatorQueueScopeGrant.tenant_key == control.tenant_key,
            OperatorQueueScopeGrant.country_code == control.country_code,
            OperatorQueueScopeGrant.channel_key == control.channel_key,
        ).first()
        if grant is None:
            db_session.add(
                OperatorQueueScopeGrant(
                    user_id=operator.id,
                    tenant_key=control.tenant_key,
                    country_code=control.country_code,
                    channel_key=control.channel_key,
                    enabled=True,
                    granted_by=operator.id,
                )
            )
    db_session.flush()
    ticket = Ticket(
''',
        "channel workbench voice route fixture",
    )

    # Preserve queue/timeline coverage and replace the obsolete reject-hangs-up contract.
    p0_gap = ROOT / "backend/tests/test_webchat_voice_p0_gap_closure.py"
    replace_exact(
        p0_gap,
        "from app.models import Ticket, User\n",
        "from app.models import Ticket, User\nfrom app.models_agent_routing import ConversationControl, OperatorAgentState\nfrom app.operator_models import OperatorQueueScopeGrant\nfrom app.utils.time import utc_now\n",
        "P0 gap routing imports",
    )
    replace_exact(
        p0_gap,
        '''        for user in [
            User(id=9301, username="voice_p0_admin", display_name="Voice P0 Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9302, username="voice_p0_agent", display_name="Voice P0 Agent", password_hash="test", role=UserRole.admin, is_active=True),
        ]:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.commit()
''',
        '''        for user in [
            User(id=9301, username="voice_p0_admin", display_name="Voice P0 Admin", password_hash="test", role=UserRole.admin, is_active=True),
            User(id=9302, username="voice_p0_agent", display_name="Voice P0 Agent", password_hash="test", role=UserRole.admin, is_active=True),
        ]:
            existing = db.query(User).filter(User.id == user.id).first()
            if existing is None:
                db.add(user)
            else:
                existing.username = user.username
                existing.display_name = user.display_name
                existing.role = user.role
                existing.is_active = True
        db.flush()
        now = utc_now()
        db.add(
            OperatorAgentState(
                user_id=9301,
                status="online",
                max_concurrent_conversations=3,
                max_concurrent_voice_calls=3,
                voice_wrap_up_seconds=0,
                last_heartbeat_at=now,
                status_changed_at=now,
                created_at=now,
                updated_at=now,
            )
        )
        db.add(
            OperatorQueueScopeGrant(
                user_id=9301,
                tenant_key="pytest-voice-p0",
                country_code="ME",
                channel_key="website",
                enabled=True,
                granted_by=9301,
            )
        )
        db.commit()
''',
        "P0 gap agent routing fixture",
    )
    replace_exact(
        p0_gap,
        '''        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        ticket = Ticket(
''',
        '''        conversation = db.query(WebchatConversation).filter(
            WebchatConversation.public_id == conversation_id
        ).one()
        control = db.query(ConversationControl).filter(
            ConversationControl.conversation_id == conversation.id
        ).one()
        control.country_code = "ME"
        ticket = Ticket(
''',
        "P0 gap voice routing scope fixture",
    )
    remove_exact(
        p0_gap,
        '''def test_admin_reject_ringing_call_is_idempotent_and_writes_evidence():
    client = TestClient(app)
    _conversation_id, _visitor_token, ticket_id, voice_session_id = _create_voice_session(client, name="Reject Visitor")

    first = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_admin_headers(9301),
        json={"reason": "agent unavailable"},
    )
    second = client.post(
        f"/api/webchat/admin/voice/{voice_session_id}/reject",
        headers=_admin_headers(9301),
        json={},
    )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["status"] == "cancelled"
    assert "participant_token" not in first.text

    db = SessionLocal()
    try:
        row = db.query(WebchatVoiceSession).filter(WebchatVoiceSession.public_id == voice_session_id).one()
        assert row.status == "cancelled"
        assert row.accepted_by_user_id is None
        assert row.ended_by_user_id == 9301
        messages = db.query(WebchatMessage).filter(WebchatMessage.ticket_id == ticket_id, WebchatMessage.message_type == "voice_call").all()
        assert len(messages) == 1
        events = [event.event_type for event in db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id).all()]
        assert events.count("voice.session.rejected") == 1
    finally:
        db.close()


''',
        "obsolete reject-hangs-up P0 contract",
    )
    source = p0_gap.read_text(encoding="utf-8")
    source = source.replace("headers=_admin_headers(9302)", "headers=_admin_headers(9301)")
    source = source.replace('assert voice_items[0]["payload"]["accepted_by"] == 9302', 'assert voice_items[0]["payload"]["accepted_by"] == 9301')
    source = source.replace('assert voice_items[0]["payload"]["ended_by"] == 9302', 'assert voice_items[0]["payload"]["ended_by"] == 9301')
    p0_gap.write_text(source, encoding="utf-8")

    run("git", "add", "-A")
    run("git", "diff", "--cached", "--check")
    run("git", "config", "user.name", "github-actions[bot]")
    run("git", "config", "user.email", "418982752+github-actions[bot]@users.noreply.github.com")
    run("git", "commit", "-m", "test: converge telephony regression authority")

    run(sys.executable, "-m", "compileall", "-q", "backend/app", "backend/tests", "scripts")
    run(sys.executable, "scripts/ci/check_telephony_authority_residue.py")
    run(sys.executable, "scripts/verify_repository.py", "--static-only")

    test_env = dict(os.environ)
    test_env.update(
        {
            "APP_ENV": "development",
            "DATABASE_URL": "sqlite:////tmp/nexus_telephony_backend_convergence.db",
            "PYTHONPATH": "backend",
        }
    )
    run(
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "backend/tests/test_channel_workbench_backend_contracts.py",
        "backend/tests/test_public_voice_route_compatibility.py",
        "backend/tests/test_webchat_voice_api.py",
        "backend/tests/test_webchat_voice_p0_gap_closure.py",
        "backend/tests/test_webchat_voice_room_compensation.py",
        "backend/tests/test_webchat_voice_static_headers.py",
        "backend/tests/test_webchat_ws_static_contracts.py",
        "backend/tests/test_websocket_upgrade_probe.py",
        "backend/tests/test_canonical_livekit_telephony.py",
        "backend/tests/test_canonical_telephony_residue.py",
        "backend/tests/test_voice_provider_error_boundary.py",
        env=test_env,
    )

    if output("git", "status", "--porcelain"):
        raise RuntimeError("verification mutated telephony convergence candidate")

    candidate_head = output("git", "rev-parse", "HEAD")
    run(
        "git",
        "push",
        f"--force-with-lease=refs/heads/{TARGET_BRANCH}:{temporary_head}",
        "origin",
        f"HEAD:refs/heads/{TARGET_BRANCH}",
    )
    print(f"published_candidate={candidate_head}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
