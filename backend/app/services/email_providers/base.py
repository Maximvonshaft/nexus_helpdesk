from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class EmailSendPayload:
    from_email: str
    to_email: str
    subject: str
    body: str
    from_name: str | None = None
    cc: list[str] = field(default_factory=list)
    bcc: list[str] = field(default_factory=list)
    reply_to: str | None = None
    configuration_set: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmailSendResult:
    provider_message_id: str
    provider_status: str = "sent"


class EmailProvider(Protocol):
    def send_email(self, payload: EmailSendPayload) -> EmailSendResult:
        ...
