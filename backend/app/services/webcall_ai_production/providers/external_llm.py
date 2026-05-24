from __future__ import annotations

import os

import httpx

from .base import LLMProvider, LLMResult
from .http_utils import classify_http_error, endpoint_required, read_secret_file, retry_call

SYSTEM_PROMPT = (
    "You are NexusDesk WebCall AI for logistics customer support. "
    "Only answer read-only shipment tracking questions. Never invent tracking facts. "
    "Never perform or promise cancellation, address changes, refunds, work orders, payments, "
    "or driver phone disclosure. If a request is unsafe, outside logistics tracking, or the "
    "tracking fact is unavailable, request human handoff. Return JSON with response_text, "
    "intent, handoff_required, and handoff_reason."
)


class ExternalLLMProvider(LLMProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        endpoint = endpoint_required(self.endpoint, provider=self.provider_name)
        token = read_secret_file(self.token_file, provider=self.provider_name)
        timeout = float(os.getenv("LLM_TIMEOUT_SECONDS", "15"))
        retries = int(os.getenv("LLM_RETRIES", "1"))

        def request() -> LLMResult:
            try:
                with httpx.Client(timeout=timeout) as client:
                    response = client.post(
                        endpoint,
                        headers={"Authorization": f"Bearer {token}"},
                        json={
                            "system": SYSTEM_PROMPT,
                            "input": text or "",
                            "language": language or "en",
                            "response_format": "json",
                        },
                    )
                    response.raise_for_status()
                    payload = response.json()
            except Exception as exc:
                raise classify_http_error(self.provider_name, exc) from exc
            response_text = str(payload.get("response_text") or payload.get("text") or "").strip()
            if not response_text:
                response_text = "I cannot verify that safely right now. I will hand this call to a human support agent."
            handoff_required = bool(payload.get("handoff_required", False))
            return LLMResult(
                response_text=response_text,
                intent=str(payload.get("intent") or ("handoff" if handoff_required else "tracking_lookup")),
                handoff_required=handoff_required,
                handoff_reason=str(payload.get("handoff_reason") or "")[:160] or None,
                provider_name=self.provider_name,
            )

        return retry_call(request, provider=self.provider_name, retries=retries)
