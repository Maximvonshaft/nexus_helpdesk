from __future__ import annotations

from email.utils import formataddr

from ...settings import get_settings
from .base import EmailSendPayload, EmailSendResult


class SesEmailProvider:
    def __init__(self, client=None) -> None:
        self._client = client

    def _client_or_raise(self):
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except Exception as exc:
            raise RuntimeError("boto3 is required for SES dispatch") from exc
        settings = get_settings()
        kwargs = {"region_name": settings.email_provider_region} if settings.email_provider_region else {}
        return boto3.client("sesv2", **kwargs)

    def send_email(self, payload: EmailSendPayload) -> EmailSendResult:
        client = self._client_or_raise()
        source = formataddr((payload.from_name, payload.from_email)) if payload.from_name else payload.from_email
        destination: dict[str, list[str]] = {"ToAddresses": [payload.to_email]}
        if payload.cc:
            destination["CcAddresses"] = payload.cc
        if payload.bcc:
            destination["BccAddresses"] = payload.bcc
        request = {
            "FromEmailAddress": source,
            "Destination": destination,
            "Content": {
                "Simple": {
                    "Subject": {"Data": payload.subject, "Charset": "UTF-8"},
                    "Body": {"Text": {"Data": payload.body, "Charset": "UTF-8"}},
                }
            },
        }
        if payload.reply_to:
            request["ReplyToAddresses"] = [payload.reply_to]
        if payload.configuration_set:
            request["ConfigurationSetName"] = payload.configuration_set
        if payload.tags:
            request["EmailTags"] = [{"Name": k[:256], "Value": v[:256]} for k, v in payload.tags.items()]
        response = client.send_email(**request)
        message_id = response.get("MessageId")
        if not message_id:
            raise RuntimeError("SES response did not include MessageId")
        return EmailSendResult(provider_message_id=message_id, provider_status="sent_via_ses")
