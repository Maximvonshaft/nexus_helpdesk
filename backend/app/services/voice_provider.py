from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class VoiceParticipantToken:
    provider: str
    room_name: str
    participant_identity: str
    participant_token: str
    expires_in_seconds: int


@dataclass(frozen=True)
class VoiceProviderActionResult:
    """Provider dispatch result.

    ``status=succeeded`` means the provider API completed the requested action.
    ``status=awaiting_event`` means a room controller accepted the command and a
    normalized provider/controller event must confirm the final outcome.
    """

    status: str
    provider_status: str
    provider_reason: str | None = None
    provider_reference: str | None = None
    safe_payload: dict[str, Any] | None = None


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

    def dispatch_agent(
        self,
        *,
        room_name: str,
        agent_name: str,
        metadata: dict[str, Any],
    ) -> VoiceProviderActionResult:
        raise NotImplementedError

    def execute_action(
        self,
        *,
        room_name: str,
        action_type: str,
        target: str | None = None,
        digits: str | None = None,
        participant_identity: str | None = None,
        controller_identity: str | None = None,
        outbound_trunk_id: str | None = None,
        recording_reference: str | None = None,
        idempotency_key: str | None = None,
    ) -> VoiceProviderActionResult:
        raise NotImplementedError
