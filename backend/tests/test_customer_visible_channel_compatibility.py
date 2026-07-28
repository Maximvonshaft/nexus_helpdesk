from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:////tmp/nexus-customer-visible-channel-compatibility.db",
)
os.environ.setdefault("ALLOW_DEV_AUTH", "false")

from app.enums import SourceChannel
from app.services.customer_visible_message_service import _effective_channel


@pytest.mark.parametrize(
    "channel_key",
    ["", "webchat", SourceChannel.web_chat.value],
)
def test_ticketless_internal_webchat_channel_aliases_remain_supported(
    channel_key: str,
) -> None:
    resolved = _effective_channel(
        ticket=None,
        conversation=SimpleNamespace(channel_key=channel_key),
        requested=SourceChannel.web_chat,
    )

    assert resolved == SourceChannel.web_chat


def test_ticketless_webchat_alias_cannot_be_reinterpreted_as_whatsapp() -> None:
    with pytest.raises(
        ValueError,
        match="ticketless customer-visible message channel mismatch",
    ):
        _effective_channel(
            ticket=None,
            conversation=SimpleNamespace(channel_key="webchat"),
            requested=SourceChannel.whatsapp,
        )
