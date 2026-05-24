from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .credential_crypto import CredentialCryptoService
from .oauth_refresh_manager import OAuthRefreshManager


class CodexLLMEndpointNotConfigured(RuntimeError):
    pass


class CodexLLMCredentialRefreshRequired(RuntimeError):
    pass


class CodexLLMProviderCallFailed(RuntimeError):
    pass


class CodexLLMBridgeNotReady(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class CodexLLMCredential:
    id: str
    tenant_id: str
    account_id: str | None = None
    chatgpt_plan_type: str | None = None


@dataclass(frozen=True)
class CodexLLMResult:
    response_text: str
    provider_status: int
    latency_ms: int
    api_style: str


class CodexLLMClient:
    """Callable Code X LLM client for admin smoke probes.

    Uses the stored Code X OAuth credential through OAuthRefreshManager. No
    static OpenAI API key is required or accepted.
    """

    def __init__(self, db: Session, crypto_service: CredentialCryptoService | None = None):
        self.db = db
        self.crypto_service = crypto_service or CredentialCryptoService()

    async def call_codex_smoke_chat(
        self,
        *,
        prompt: str,
        nonce: str,
        credential: CodexLLMCredential,
        request_id: str,
    ) -> CodexLLMResult:
        endpoint = _configured_endpoint()
        if not endpoint:
            raise CodexLLMEndpointNotConfigured("codex_llm_endpoint_not_configured")

        access_token = await OAuthRefreshManager(self.db, self.crypto_service).get_valid_access_token(credential.tenant_id, credential.id)
        if not access_token:
            raise CodexLLMCredentialRefreshRequired("credential_refresh_required")

        style = _api_style(endpoint)
        started = time.monotonic()
        timeout = _timeout_seconds()
        last_error: Exception | None = None
        for attempt in range(_retries() + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    if style == "codex_app_server":
                        response_text, provider_status = await self._call_codex_app_server(
                            client,
                            endpoint=endpoint,
                            access_token=access_token,
                            credential=credential,
                            prompt=prompt,
                            nonce=nonce,
                            request_id=request_id,
                        )
                    else:
                        response_text, provider_status = await self._call_bearer_chat(
                            client,
                            endpoint=endpoint,
                            access_token=access_token,
                            prompt=prompt,
                            nonce=nonce,
                            request_id=request_id,
                            style=style,
                        )
                return CodexLLMResult(
                    response_text=response_text,
                    provider_status=provider_status,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    api_style=style,
                )
            except CodexLLMCredentialRefreshRequired:
                raise
            except CodexLLMBridgeNotReady:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= _retries():
                    break
        raise CodexLLMProviderCallFailed("codex_provider_call_failed") from last_error

    async def _call_codex_app_server(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        access_token: str,
        credential: CodexLLMCredential,
        prompt: str,
        nonce: str,
        request_id: str,
    ) -> tuple[str, int]:
        login_payload = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": credential.account_id,
            "chatgptPlanType": credential.chatgpt_plan_type,
        }
        headers = _bridge_headers()
        login_url = os.getenv("CODEX_APP_SERVER_LOGIN_URL", "").strip()
        if login_url:
            login_response = await client.post(login_url, json={"login": login_payload}, headers=headers)
            if login_response.status_code in {401, 403}:
                raise CodexLLMCredentialRefreshRequired("credential_refresh_required")
            _raise_bridge_not_ready(login_response)
            login_response.raise_for_status()
            payload = _reply_payload(prompt=prompt, nonce=nonce, request_id=request_id)
        else:
            payload = {"login": login_payload, **_reply_payload(prompt=prompt, nonce=nonce, request_id=request_id)}
        response = await client.post(endpoint, json=payload, headers=headers)
        if response.status_code in {401, 403}:
            raise CodexLLMCredentialRefreshRequired("credential_refresh_required")
        _raise_bridge_not_ready(response)
        response.raise_for_status()
        return _extract_response_text(response.json()), response.status_code

    async def _call_bearer_chat(
        self,
        client: httpx.AsyncClient,
        *,
        endpoint: str,
        access_token: str,
        prompt: str,
        nonce: str,
        request_id: str,
        style: str,
    ) -> tuple[str, int]:
        payload = _openai_payload(prompt=prompt, nonce=nonce, request_id=request_id) if style == "openai_chat" else _responses_payload(prompt=prompt, nonce=nonce, request_id=request_id)
        response = await client.post(
            endpoint,
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {access_token}",
                "X-Nexus-Request-Id": request_id,
            },
        )
        if response.status_code in {401, 403}:
            raise CodexLLMCredentialRefreshRequired("credential_refresh_required")
        response.raise_for_status()
        return _extract_response_text(response.json()), response.status_code


def _configured_endpoint() -> str | None:
    return (
        os.getenv("CODEX_LLM_ENDPOINT")
        or os.getenv("CODEX_SMOKE_ENDPOINT")
        or os.getenv("CODEX_APP_SERVER_BRIDGE_URL")
        or ""
    ).strip() or None


def _api_style(endpoint: str) -> str:
    configured = (os.getenv("CODEX_LLM_API_STYLE") or "").strip().lower()
    if configured in {"codex_app_server", "openai_chat", "responses"}:
        return configured
    if os.getenv("CODEX_LLM_ENDPOINT") or os.getenv("CODEX_SMOKE_ENDPOINT"):
        if endpoint.rstrip("/").endswith("/v1/responses") or endpoint.rstrip("/").endswith("/responses"):
            return "responses"
        return "openai_chat"
    return "codex_app_server"


def _timeout_seconds() -> float:
    raw = os.getenv("CODEX_LLM_TIMEOUT_SECONDS") or os.getenv("CODEX_SMOKE_TIMEOUT_MS") or os.getenv("CODEX_APP_SERVER_TIMEOUT_MS") or "15"
    try:
        value = float(raw)
    except ValueError:
        value = 15.0
    if value > 1000:
        value = value / 1000.0
    return max(1.0, min(value, 60.0))


def _retries() -> int:
    try:
        return max(0, min(int(os.getenv("CODEX_LLM_RETRIES", "0")), 3))
    except ValueError:
        return 0


def _bridge_headers() -> dict[str, str]:
    headers = {"X-Nexus-Provider-Runtime": "codex-app-server-v1"}
    shared_token = _read_shared_token()
    if shared_token:
        headers["Authorization"] = f"Bearer {shared_token}"
    return headers


def _read_shared_token() -> str:
    token_file = os.getenv("CODEX_APP_SERVER_TOKEN_FILE", "").strip() or os.getenv("CODEX_REPLY_BRIDGE_TOKEN_FILE", "").strip()
    if token_file:
        try:
            value = Path(token_file).read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    else:
        app_env = os.getenv("APP_ENV", os.getenv("ENV", "development")).strip().lower()
        value = os.getenv("CODEX_APP_SERVER_TOKEN", "").strip() if app_env in {"development", "test", "local"} else ""
    if value.lower().startswith("bearer "):
        return value.split(None, 1)[1].strip()
    return value


def _raise_bridge_not_ready(response: httpx.Response) -> None:
    if response.status_code != 503:
        return
    try:
        payload = response.json()
    except ValueError:
        return
    if not isinstance(payload, dict):
        return
    reason = payload.get("error") or payload.get("reason")
    if not isinstance(reason, str) or not reason:
        readiness = payload.get("readiness")
        if isinstance(readiness, dict) and isinstance(readiness.get("reason"), str):
            reason = readiness["reason"]
    if reason in {
        "codex_app_server_real_upstream_not_configured",
        "codex_app_server_real_upstream_unreachable",
        "bridge_token_not_configured",
        "codex_app_server_bridge_not_real",
    }:
        raise CodexLLMBridgeNotReady(reason)


def _reply_payload(*, prompt: str, nonce: str, request_id: str) -> dict[str, Any]:
    return {
        "messages": [
            {"role": "system", "content": "Nexus admin Code X smoke test. Reply with the supplied nonce exactly once."},
            {"role": "user", "content": f"Echo this nonce exactly: {nonce}\n\nOperator prompt: {prompt}"},
        ],
        "body": f"Echo this nonce exactly: {nonce}\n\nOperator prompt: {prompt}",
        "contract": "codex_smoke_nonce_echo_v1",
        "metadata": {"request_id": request_id, "purpose": "nexus_codex_smoke_chat"},
    }


def _openai_payload(*, prompt: str, nonce: str, request_id: str) -> dict[str, Any]:
    payload = {
        "messages": [
            {"role": "system", "content": "Nexus admin Code X smoke test. Reply with the supplied nonce exactly once."},
            {"role": "user", "content": f"Echo this nonce exactly: {nonce}\n\nOperator prompt: {prompt}"},
        ],
        "metadata": {"request_id": request_id, "purpose": "nexus_codex_smoke_chat"},
    }
    model = os.getenv("CODEX_LLM_MODEL", "").strip() or os.getenv("CODEX_SMOKE_MODEL", "").strip()
    if model:
        payload["model"] = model
    return payload


def _responses_payload(*, prompt: str, nonce: str, request_id: str) -> dict[str, Any]:
    payload = {
        "instructions": "Nexus admin Code X smoke test. Reply with the supplied nonce exactly once.",
        "input": f"Echo this nonce exactly: {nonce}\n\nOperator prompt: {prompt}",
        "metadata": {"request_id": request_id, "purpose": "nexus_codex_smoke_chat"},
    }
    model = os.getenv("CODEX_LLM_MODEL", "").strip() or os.getenv("CODEX_SMOKE_MODEL", "").strip()
    if model:
        payload["model"] = model
    return payload


def _extract_response_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            message = first.get("message")
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
            if isinstance(first.get("text"), str):
                return first["text"]
    for key in ("response_text", "output_text", "reply", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    output = payload.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        parts.append(block["text"])
            elif isinstance(content, str):
                parts.append(content)
        return "\n".join(parts)
    return ""
