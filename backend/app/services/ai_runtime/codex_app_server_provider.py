from __future__ import annotations

import time
from typing import Any

import httpx

from ..webchat_fast_output_parser import (
    FastReplyParseError,
    ParsedFastReply,
    UnexpectedToolCallError,
    parse_openclaw_fast_reply,
)
from .provider_base import BaseFastAIProvider
from .schemas import FastAIProviderRequest, FastAIProviderResult


# Deprecated legacy direct WebChat Fast Reply provider. Production traffic must
# enter through WEBCHAT_FAST_AI_PROVIDER=provider_runtime instead.
def _clip(value: str | None, limit: int) -> str:
    cleaned = (value or "").strip()
    return cleaned[:limit]


def _safe_bridge_summary(*, status_code: int | None, error_code: str | None, parsed: bool, elapsed_ms: int) -> dict[str, Any]:
    return {
        "bridge": "codex_app_server",
        "status_code": status_code,
        "error_code": error_code,
        "parsed": parsed,
        "elapsed_ms": elapsed_ms,
    }


def _request_payload(request: FastAIProviderRequest) -> dict[str, Any]:
    return {
        "request_id": request.request_id or "webchat-fast-codex-app-server",
        "tenant_key": request.tenant_key or "default",
        "channel_key": request.channel_key or "website",
        "session_id": request.session_id,
        "body": _clip(request.body, 8000),
        "recent_context": request.recent_context or [],
        "tracking_fact_summary": _clip(request.tracking_fact_summary, 4000) or None,
        "tracking_fact_evidence_present": bool(request.tracking_fact_evidence_present),
        "strict_schema": "speedaf_webchat_fast_reply_v1",
    }


def _success_from_parsed(parsed: ParsedFastReply, *, elapsed_ms: int, status_code: int | None) -> FastAIProviderResult:
    return FastAIProviderResult(
        ok=True,
        ai_generated=True,
        reply_source="codex_app_server",
        raw_provider="codex_app_server",
        raw_payload_safe_summary=_safe_bridge_summary(
            status_code=status_code,
            error_code=None,
            parsed=True,
            elapsed_ms=elapsed_ms,
        ),
        reply=parsed.reply,
        intent=parsed.intent,
        tracking_number=parsed.tracking_number,
        handoff_required=parsed.handoff_required,
        handoff_reason=parsed.handoff_reason,
        recommended_agent_action=parsed.recommended_agent_action,
        tool_intents=[],
        elapsed_ms=elapsed_ms,
    )


class CodexAppServerProvider(BaseFastAIProvider):
    name = "codex_app_server"

    def is_configured(self) -> bool:
        return bool(
            self.settings.enabled
            and self.settings.codex_app_server_enabled
            and self.settings.codex_app_server_bridge_url
            and self.settings.codex_app_server_token
        )

    async def _call_bridge(self, request: FastAIProviderRequest) -> tuple[int, Any]:
        token = self.settings.codex_app_server_token
        if not self.settings.codex_app_server_bridge_url or not token:
            raise RuntimeError("codex_app_server_not_configured")
        timeout = httpx.Timeout(self.settings.codex_app_server_timeout_ms / 1000)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self.settings.codex_app_server_bridge_url,
                json=_request_payload(request),
                headers=headers,
            )
            response.raise_for_status()
            return response.status_code, response.json()

    async def generate(self, request: FastAIProviderRequest) -> FastAIProviderResult:
        started = time.monotonic()
        if not self.is_configured():
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="codex_app_server_not_configured",
                elapsed_ms=0,
                safe_summary=_safe_bridge_summary(
                    status_code=None,
                    error_code="codex_app_server_not_configured",
                    parsed=False,
                    elapsed_ms=0,
                ),
            )

        status_code: int | None = None
        try:
            status_code, payload = await self._call_bridge(request)
            parsed = parse_openclaw_fast_reply(payload)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return _success_from_parsed(parsed, elapsed_ms=elapsed_ms, status_code=status_code)
        except UnexpectedToolCallError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="ai_unexpected_tool_call",
                elapsed_ms=elapsed_ms,
                safe_summary=_safe_bridge_summary(
                    status_code=status_code,
                    error_code="ai_unexpected_tool_call",
                    parsed=False,
                    elapsed_ms=elapsed_ms,
                ),
            )
        except FastReplyParseError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="ai_invalid_output",
                elapsed_ms=elapsed_ms,
                safe_summary=_safe_bridge_summary(
                    status_code=status_code,
                    error_code="ai_invalid_output",
                    parsed=False,
                    elapsed_ms=elapsed_ms,
                ),
            )
        except httpx.HTTPStatusError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="codex_app_server_http_error",
                elapsed_ms=elapsed_ms,
                safe_summary=_safe_bridge_summary(
                    status_code=exc.response.status_code,
                    error_code="codex_app_server_http_error",
                    parsed=False,
                    elapsed_ms=elapsed_ms,
                ),
            )
        except (httpx.TimeoutException, httpx.TransportError, ValueError, RuntimeError):
            elapsed_ms = int((time.monotonic() - started) * 1000)
            return FastAIProviderResult.unavailable(
                provider=self.name,
                error_code="codex_app_server_unavailable",
                elapsed_ms=elapsed_ms,
                safe_summary=_safe_bridge_summary(
                    status_code=status_code,
                    error_code="codex_app_server_unavailable",
                    parsed=False,
                    elapsed_ms=elapsed_ms,
                ),
            )
