from __future__ import annotations

import hashlib
from typing import Any

from .voice_provider import VoiceParticipantToken, VoiceProvider, VoiceProviderActionResult


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
        digest = hashlib.sha256(
            f"{room_name}:{participant_identity}:{ttl_seconds}".encode("utf-8")
        ).hexdigest()[:32]
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

    def dispatch_agent(
        self,
        *,
        room_name: str,
        agent_name: str,
        metadata: dict[str, Any],
    ) -> VoiceProviderActionResult:
        reference = hashlib.sha256(
            f"{room_name}:{agent_name}".encode("utf-8")
        ).hexdigest()[:20]
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="dispatched",
            provider_reference=f"mock-dispatch-{reference}",
            safe_payload={"agent_name": agent_name, "metadata_keys": sorted(metadata)},
        )

    def execute_action(
        self,
        *,
        room_name: str,
        action_type: str,
        target: str | None = None,
        digits: str | None = None,
        participant_identity: str | None = None,
        outbound_trunk_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> VoiceProviderActionResult:
        material = ":".join(
            value or ""
            for value in (
                room_name,
                action_type,
                target,
                participant_identity,
                outbound_trunk_id,
                idempotency_key,
            )
        )
        reference = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
        return VoiceProviderActionResult(
            status="succeeded",
            provider_status="executed",
            provider_reference=f"mock-action-{reference}",
            safe_payload={
                "target_present": bool(target),
                "digits_length": len(digits or ""),
                "participant_identity_present": bool(participant_identity),
            },
        )
