from __future__ import annotations

from .base import LLMProvider, LLMResult


class ExternalLLMProvider(LLMProvider):
    provider_name = "external"

    def __init__(self, *, endpoint: str | None = None, token_file: str | None = None) -> None:
        self.endpoint = endpoint
        self.token_file = token_file

    def respond(self, text: str, *, language: str | None = None) -> LLMResult:
        raise RuntimeError("external LLM adapter must be wired to the approved provider before production use")
