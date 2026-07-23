from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/livekit_agent_worker_tests.db")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from app.livekit_agent_config import (
    load_livekit_agent_worker_config,
    livekit_agent_registration_name,
)
from app.livekit_agent_worker import (
    latest_user_text,
    parse_agent_job_metadata,
    parse_controller_command,
    publish_dtmf_sequence,
)


def _configure_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIVEKIT_AGENT_NAME", "nexus-voice-agent")
    monkeypatch.setenv("LIVEKIT_AGENT_SHARED_SECRET", "test-shared-secret")
    monkeypatch.setenv("NEXUS_INTERNAL_API_URL", "http://app-controlled:8080")
    monkeypatch.setenv("NEXUS_VOICE_STT_MODEL", "deepgram/nova-3")
    monkeypatch.setenv("NEXUS_VOICE_TTS_MODEL", "cartesia/sonic-3:test-voice")
    monkeypatch.setenv("NEXUS_VOICE_TURN_DETECTION", "stt")


def test_worker_configuration_is_fail_closed(monkeypatch: pytest.MonkeyPatch):
    _configure_worker(monkeypatch)
    monkeypatch.delenv("NEXUS_VOICE_STT_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="NEXUS_VOICE_STT_MODEL is required"):
        load_livekit_agent_worker_config()


def test_worker_configuration_preserves_one_runtime_boundary(
    monkeypatch: pytest.MonkeyPatch,
):
    _configure_worker(monkeypatch)
    monkeypatch.delenv("NEXUS_VOICE_TRANSFER_LLM_MODEL", raising=False)

    config = load_livekit_agent_worker_config()

    assert config.agent_name == "nexus-voice-agent"
    assert config.nexus_internal_api_url == "http://app-controlled:8080"
    assert config.stt_model == "deepgram/nova-3"
    assert config.tts_model == "cartesia/sonic-3:test-voice"
    assert config.transfer_llm_model is None
    assert livekit_agent_registration_name() == "nexus-voice-agent"


def test_job_metadata_requires_canonical_public_identities():
    metadata = parse_agent_job_metadata(
        json.dumps(
            {
                "schema": "nexus.livekit-agent-session.v1",
                "role": "ai_controller",
                "voice_session_id": "wv_public",
                "conversation_public_id": "wc_public",
                "channel_account_id": 9,
            }
        )
    )

    assert metadata.role == "ai_controller"
    assert metadata.voice_session_id == "wv_public"
    assert metadata.conversation_public_id == "wc_public"
    assert metadata.channel_account_id == 9

    with pytest.raises(RuntimeError, match="authority_context_missing"):
        parse_agent_job_metadata(
            json.dumps(
                {
                    "schema": "nexus.livekit-agent-session.v1",
                    "role": "ai_controller",
                    "voice_session_id": "wv_public",
                    "conversation_id": 123,
                }
            )
        )


def test_controller_command_protocol_is_exact_and_bounded():
    packet = SimpleNamespace(
        topic="nexus.telephony.command.v1",
        data=json.dumps(
            {
                "schema": "nexus.telephony.command.v1",
                "command_id": "vc_123",
                "action": "keypad",
                "digits": "12#",
                "participant_identity": "caller_1",
            }
        ).encode("utf-8"),
    )
    command = parse_controller_command(packet)

    assert command == {
        "command_id": "vc_123",
        "action": "keypad",
        "target": None,
        "digits": "12#",
        "participant_identity": "caller_1",
        "outbound_trunk_id": None,
    }
    assert parse_controller_command(
        SimpleNamespace(topic="other", data=packet.data)
    ) is None
    assert parse_controller_command(
        SimpleNamespace(
            topic="nexus.telephony.command.v1",
            data=b'{"schema":"nexus.telephony.command.v1","command_id":"x","action":"answer"}',
        )
    ) is None


@pytest.mark.asyncio
async def test_dtmf_sequence_executes_real_room_control():
    class FakeLocalParticipant:
        def __init__(self) -> None:
            self.calls: list[tuple[int, str]] = []

        async def publish_dtmf(self, *, code: int, digit: str) -> None:
            self.calls.append((code, digit))

    participant = FakeLocalParticipant()
    sent = await publish_dtmf_sequence(participant, "12#")

    assert sent == 3
    assert participant.calls == [(1, "1"), (2, "2"), (11, "#")]

    with pytest.raises(ValueError, match="invalid_dtmf_digit"):
        await publish_dtmf_sequence(participant, "X")


def test_latest_user_text_reads_only_the_latest_customer_message():
    chat_ctx = SimpleNamespace(
        items=[
            SimpleNamespace(type="message", role="assistant", text_content="hello"),
            SimpleNamespace(type="message", role="user", text_content="first request"),
            SimpleNamespace(type="message", role="assistant", text_content="reply"),
            SimpleNamespace(type="message", role="user", text_content="latest request"),
        ]
    )

    assert latest_user_text(chat_ctx) == "latest request"


def test_worker_source_has_no_second_business_llm_authority():
    source = (ROOT / "app" / "livekit_agent_worker.py").read_text(encoding="utf-8")

    assert '"/api/telephony/internal/agent-turn"' in source
    assert "class NexusVoiceAgent" in source
    assert "async def llm_node" in source
    assert "AgentSession(" in source
    assert "llm=inference.LLM" not in source.split("class NexusVoiceAgent", 1)[1].split(
        "class TelephonyController", 1
    )[0]
    assert "AgentServer(host=\"0.0.0.0\", port=8081)" in source
