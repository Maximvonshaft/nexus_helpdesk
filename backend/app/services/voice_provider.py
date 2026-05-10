from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceParticipantToken:
    provider: str
    room_name: str
    participant_identity: str
    participant_token: str
    expires_in_seconds: int


class VoiceProviderError(RuntimeError):
    pass


class VoiceProvider:
    provider_name = "base"

    def create_room(self, *, room_name: str) -> str:
        raise NotImplementedError

    def issue_participant_token(
        self,
        *,
        room_name: str,
        participant_identity: str,
        ttl_seconds: int,
    ) -> VoiceParticipantToken:
        raise NotImplementedError

    def close_room(self, *, room_name: str) -> None:
        raise NotImplementedError

    def get_room_status(self, *, room_name: str) -> str:
        raise NotImplementedError
