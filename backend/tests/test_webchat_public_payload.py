from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.api import webchat_public
from app.services import webchat_performance
from app.services.webchat_public_payload import public_webchat_message_payload


def test_public_message_payload_is_the_single_serializer() -> None:
    row = SimpleNamespace(
        id=7,
        direction="visitor",
        body="hello",
        body_text="hello",
        message_type="text",
        payload_json='{"card_id":"safe"}',
        metadata_json='{"generated_by":"visitor","secret":"blocked"}',
        client_message_id="client-1",
        ai_turn_id=None,
        delivery_status="sent",
        action_status=None,
        author_label="Visitor",
        created_at=None,
    )

    payload = public_webchat_message_payload(row)

    assert payload["payload_json"] == {"card_id": "safe"}
    assert payload["metadata_json"] == {"generated_by": "visitor"}
    assert "secret" not in str(payload)


def test_public_surfaces_import_the_canonical_serializer_without_local_copies() -> None:
    public_source = inspect.getsource(webchat_public)
    polling_source = inspect.getsource(webchat_performance)

    assert "public_webchat_message_payload" in public_source
    assert "public_webchat_message_payload" in polling_source
    assert "def _message_read" not in public_source
    assert "def _message_read" not in polling_source
    assert "def _loads_json" not in public_source
    assert "def _loads_json" not in polling_source
