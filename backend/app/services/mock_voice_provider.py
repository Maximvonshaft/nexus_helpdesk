from __future__ import annotations

import hashlib

from .voice_provider import VoiceParticipantToken, VoiceProvider


class MockVoiceProvider(VoiceProvider):
    provider_name = "mock"

    def create_room(self, *, room_name: str) -> str:
        return room_name

    def issue_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
        digest = hashlib.sha256(f"{room_name}:{participant_identity}:{ttl_seconds}".encode("utf-8")).hexdigest()[:32]
        return VoiceParticipantToken(
            provider=self.provider_name,
            room_name=room_name,
            participant_identity=participant_identity,
            participant_token=f"mock_voice_token_{digest}",
            expires_in_seconds=ttl_seconds,
        )

    def close_room(self, *, room_name: str) -> None:
        return None

    def get_room_status(self, *, room_name: str) -> str:
        return "mock"
