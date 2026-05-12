from __future__ import annotations

from dataclasses import dataclass

from ..models import TicketOutboundMessage
from ..settings import get_settings
from ..utils.time import utc_now


@dataclass(frozen=True)
class ProviderSendResult:
    provider: str
    provider_message_id: str | None
    provider_thread_id: str | None
    status: str
    error_code: str | None = None
    error_message: str | None = None
    raw_response_ref: str | None = None


class SandboxEmailProvider:
    provider_name = 'sandbox_email'

    def dispatch(self, message: TicketOutboundMessage) -> ProviderSendResult:
        body = message.body or ''
        if 'SANDBOX_FAIL' in body:
            return ProviderSendResult(
                provider=self.provider_name,
                provider_message_id=None,
                provider_thread_id=None,
                status='failed',
                error_code='sandbox_failure',
                error_message='Sandbox forced failure',
                raw_response_ref=f'sandbox_email_failed_{message.id}',
            )
        return ProviderSendResult(
            provider=self.provider_name,
            provider_message_id=f'sandbox_email_{message.id}',
            provider_thread_id=f'sandbox_thread_{message.ticket_id}',
            status='sent',
            raw_response_ref=f'sandbox_email_sent_{message.id}_{int(utc_now().timestamp())}',
        )


def get_email_provider() -> SandboxEmailProvider:
    settings = get_settings()
    provider = (settings.email_provider or 'sandbox').strip().lower()
    if provider != 'sandbox':
        raise RuntimeError(f'unsupported_email_provider:{provider}')
    return SandboxEmailProvider()
