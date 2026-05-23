from app.services.webcall_ai.voice_egress_client import (
    FakeAudioReferenceEgressClient,
    LiveKitAudioPublishStubClient,
)


def test_fake_egress_sends_audio_reference_without_token_or_raw_audio():
    result = FakeAudioReferenceEgressClient().send_audio_reference(
        audio_reference="mock://tts/audio",
        participant_identity="ai_webcall_test",
    )

    assert result.sent is True
    assert result.provider == "fake_audio_reference"
    assert result.audio_reference == "mock://tts/audio"
    assert "token" not in repr(result).lower()
    assert "bytes" not in repr(result).lower()


def test_fake_egress_requires_audio_reference():
    result = FakeAudioReferenceEgressClient().send_audio_reference(audio_reference="")

    assert result.sent is False
    assert result.error_code == "audio_reference_required"


def test_livekit_publish_stub_fails_closed_without_injected_publisher():
    result = LiveKitAudioPublishStubClient().send_audio_reference(audio_reference="mock://tts/audio")

    assert result.sent is False
    assert result.error_code == "livekit_audio_publish_unavailable"


def test_livekit_publish_stub_with_injected_fake_publisher_records_no_token_or_raw_audio():
    calls = []

    class FakePublisher:
        def send(self, *, audio_reference: str, participant_identity: str | None = None) -> bool:
            calls.append({"audio_reference": audio_reference, "participant_identity": participant_identity})
            return True

    result = LiveKitAudioPublishStubClient(FakePublisher()).send_audio_reference(
        audio_reference="mock://tts/audio",
        participant_identity="ai_webcall_test",
    )

    assert result.sent is True
    assert calls == [{"audio_reference": "mock://tts/audio", "participant_identity": "ai_webcall_test"}]
    assert "token" not in repr(result).lower()
    assert "bytes" not in repr(result).lower()
