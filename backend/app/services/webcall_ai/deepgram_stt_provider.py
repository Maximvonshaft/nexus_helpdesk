from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .config import WebCallAISettings
from .media_schemas import WebCallSTTInput, WebCallSTTResult


class DeepgramSTTTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, str],
        timeout_ms: int,
    ) -> dict:
        ...


class UrllibDeepgramSTTTransport:
    def post_json(
        self,
        *,
        url: str,
        headers: dict[str, str],
        payload: dict[str, str],
        timeout_ms: int,
    ) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout_ms / 1000, context=context) as response:
            body = response.read()
        return json.loads(body.decode("utf-8"))


class DeepgramSTTProvider:
    name = "deepgram"

    def __init__(
        self,
        settings: WebCallAISettings,
        transport: DeepgramSTTTransport | None = None,
    ) -> None:
        self.settings = settings
        self.transport = transport or UrllibDeepgramSTTTransport()
        self.token = self._resolve_token(settings)
        if not self.token:
            raise RuntimeError("Deepgram STT token is required")

    def transcribe(self, input: WebCallSTTInput) -> WebCallSTTResult:
        if not input.audio_reference:
            return self._unavailable(input, "deepgram_audio_reference_required")
        if not input.audio_reference.startswith("https://"):
            return self._unavailable(input, "deepgram_audio_reference_must_be_https")
        if not self._audio_reference_allowed(input.audio_reference):
            return self._unavailable(input, "deepgram_audio_reference_host_not_allowed")

        try:
            response = self.transport.post_json(
                url=self._request_url(),
                headers={
                    "Authorization": f"Token {self.token}",
                    "Content-Type": "application/json",
                },
                payload={"url": input.audio_reference},
                timeout_ms=self.settings.stt_timeout_ms,
            )
        except TimeoutError:
            return self._unavailable(input, "deepgram_transport_timeout")
        except (urllib.error.URLError, OSError, ValueError, RuntimeError):
            return self._unavailable(input, "deepgram_transport_error")

        return self._parse_response(input, response)

    def _request_url(self) -> str:
        smart_format = "true" if self.settings.stt_deepgram_smart_format else "false"
        return f"{self.settings.stt_deepgram_endpoint}?model={self.settings.stt_deepgram_model}&smart_format={smart_format}"

    def _parse_response(self, input: WebCallSTTInput, response: dict) -> WebCallSTTResult:
        try:
            alternative = response["results"]["channels"][0]["alternatives"][0]
            transcript = str(alternative.get("transcript") or "").strip()
            confidence = alternative.get("confidence")
        except (KeyError, IndexError, TypeError, AttributeError):
            return self._unavailable(input, "deepgram_missing_transcript")

        if not transcript:
            return self._unavailable(input, "deepgram_missing_transcript")

        return WebCallSTTResult(
            text_redacted=transcript,
            language=input.locale or "en",
            confidence=self._normalize_confidence(confidence),
            is_final=True,
            provider=self.name,
            event_count=1,
            status="ok",
            error_code=None,
        )

    def _resolve_token(self, settings: WebCallAISettings) -> str | None:
        if settings.stt_token_file:
            try:
                token = Path(settings.stt_token_file).read_text(encoding="utf-8").strip()
            except OSError:
                return None
            return token or None
        if settings.app_env != "production" and settings.stt_inline_token:
            return settings.stt_inline_token.strip() or None
        return None

    def _audio_reference_allowed(self, audio_reference: str) -> bool:
        allowlist = self.settings.stt_deepgram_remote_url_allowlist
        if not allowlist:
            return True
        host = self._host_from_https_url(audio_reference)
        allowed_hosts = {item.strip().lower() for item in allowlist.split(",") if item.strip()}
        return host in allowed_hosts

    @staticmethod
    def _host_from_https_url(value: str) -> str:
        without_scheme = value[len("https://") :]
        return without_scheme.split("/", 1)[0].split(":", 1)[0].lower()

    @staticmethod
    def _normalize_confidence(value: object) -> int:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return 0
        if 0 <= numeric <= 1:
            numeric = round(numeric * 100)
        if numeric < 0:
            return 0
        if numeric > 100:
            return 100
        return int(round(numeric))

    def _unavailable(self, input: WebCallSTTInput, error_code: str) -> WebCallSTTResult:
        return WebCallSTTResult(
            text_redacted=None,
            language=input.locale or "en",
            confidence=None,
            is_final=False,
            provider=self.name,
            event_count=0,
            status="unavailable",
            error_code=error_code,
        )
