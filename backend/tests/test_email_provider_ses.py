from __future__ import annotations

from app.services.email_providers.base import EmailSendPayload
from app.services.email_providers.ses import SesEmailProvider


class FakeSesClient:
    def send_email(self, **kwargs):
        self.kwargs = kwargs
        return {"MessageId": "ses-message-1"}


def test_ses_provider_builds_send_email_request():
    client = FakeSesClient()
    provider = SesEmailProvider(client=client)
    result = provider.send_email(EmailSendPayload(from_email="support@example.test", from_name="Support", to_email="alice@example.test", subject="Hello", body="Body", configuration_set="events"))
    assert result.provider_message_id == "ses-message-1"
    assert client.kwargs["Destination"]["ToAddresses"] == ["alice@example.test"]
    assert client.kwargs["ConfigurationSetName"] == "events"
