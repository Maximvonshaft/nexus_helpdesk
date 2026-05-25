from __future__ import annotations

import hashlib
import ipaddress
import os
import socket
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..credential_crypto import CredentialCryptoService
from ..oauth_refresh_manager import OAuthRefreshManager
from ..registry import ProviderAdapter
from ..schemas import ProviderCapabilities, ProviderRequest, ProviderResult

class CodexAppServerAdapter(ProviderAdapter):
    name = "codex_app_server"
    capabilities = ProviderCapabilities(
        fast_reply=True,
        structured_output=True,
        handoff_decision=True,
        safety_level="reply_only",
    )

    def __init__(self, crypto_service: CredentialCryptoService, bridge_url: str):
        self.crypto_service = crypto_service
        self.bridge_url = bridge_url
        self.app_env = os.environ.get("APP_ENV", os.environ.get("ENV", "development")).strip().lower()
        self.shared_token = self._load_shared_token()
        self.login_url = os.environ.get("CODEX_APP_SERVER_LOGIN_URL", "").strip()
        self.auth_mode = os.environ.get("CODEX_APP_SERVER_AUTH_MODE", "per_request").strip().lower() or "per_request"
        self.allow_combined_login_reply = os.environ.get("CODEX_APP_SERVER_ALLOW_COMBINED_LOGIN_REPLY", "false").lower() == "true"
        self.total_timeout_ms = _int_env("CODEX_APP_SERVER_TOTAL_TIMEOUT_MS", 10000, minimum=500, maximum=10000)
        self.connect_timeout_ms = _int_env("CODEX_APP_SERVER_CONNECT_TIMEOUT_MS", 250, minimum=50, maximum=2000)
        self._validate_bridge_url(bridge_url)
        if self.login_url:
            self._validate_bridge_url(self.login_url)
        if self.app_env == "production" and not self.shared_token:
            raise RuntimeError("CODEX_APP_SERVER_TOKEN_FILE is required for codex_app_server provider in production")

    def _load_shared_token(self) -> str:
        token_file = os.environ.get("CODEX_APP_SERVER_TOKEN_FILE", "").strip()
        if token_file:
            try:
                value = Path(token_file).read_text(encoding="utf-8").strip()
                if value.lower().startswith("bearer "):
                    value = value.split(None, 1)[1].strip()
                return value
            except OSError:
                return ""
        if self.app_env in {"development", "test", "local"}:
            value = os.environ.get("CODEX_APP_SERVER_TOKEN", "").strip()
            if value.lower().startswith("bearer "):
                value = value.split(None, 1)[1].strip()
            return value
        return ""

    def _validate_bridge_url(self, url: str) -> None:
        if not url:
            raise ValueError("Codex bridge URL is required")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Codex bridge URL must use http or https")
        hostname = parsed.hostname
        if not hostname:
            raise ValueError("Codex bridge URL host is required")

        if hostname in {"localhost", "127.0.0.1", "::1"}:
            return

        ips = self._resolve_host_ips(hostname)
        if not ips:
            if self.app_env == "production":
                raise ValueError(f"Codex bridge host could not be resolved safely: {hostname}")
            # Non-production Docker service names such as http://bridge are allowed only outside production.
            return

        for ip in ips:
            if not self._is_allowed_private_ip(ip):
                raise ValueError(f"Codex bridge resolved to a non-private address: {ip}")

    @staticmethod
    def _resolve_host_ips(hostname: str) -> set[ipaddress._BaseAddress]:
        ips: set[ipaddress._BaseAddress] = set()
        try:
            for item in socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP):
                address = item[4][0]
                ips.add(ipaddress.ip_address(address))
        except socket.gaierror:
            return set()
        return ips

    @staticmethod
    def _is_allowed_private_ip(ip: ipaddress._BaseAddress) -> bool:
        if ip.is_loopback or ip.is_private or ip.is_link_local:
            return True
        if ip.version == 4 and ipaddress.ip_address("100.64.0.0") <= ip <= ipaddress.ip_address("100.127.255.255"):
            return True
        return False

    def _headers(self, *, request_id: str, deadline_ms: int) -> dict[str, str]:
        headers: dict[str, str] = {
            "X-Nexus-Provider-Runtime": "codex-app-server-v1",
            "X-Nexus-Request-Id": request_id,
            "X-Nexus-Request-Deadline-Ms": str(deadline_ms),
        }
        if self.shared_token:
            headers["Authorization"] = f"Bearer {self.shared_token}"
        return headers

    def _readyz_url(self) -> str:
        parsed = urllib.parse.urlparse(self.login_url or self.bridge_url)
        return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "/readyz", "", "", ""))

    @staticmethod
    def _safe_readyz_summary(payload: dict[str, Any] | None) -> dict[str, Any]:
        payload = payload or {}
        return {
            "bridge_mode": payload.get("mode"),
            "real_upstream_configured": bool(payload.get("real_upstream_configured")),
            "accepts_oauth_login": bool(payload.get("accepts_oauth_login")),
            "reply_generation_backend": payload.get("reply_generation_backend"),
            "token_file_configured": bool(payload.get("token_file_configured")),
        }

    async def _get_bridge_readyz(self, client: httpx.AsyncClient) -> dict[str, Any] | None:
        response = await client.get(self._readyz_url(), headers=self._headers(request_id="readyz", deadline_ms=_deadline_ms(1000)))
        if response.status_code not in {200, 503}:
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("bridge_readyz_must_be_object")
        return payload

    async def generate(self, db: Session, request: ProviderRequest) -> ProviderResult:
        started = time.monotonic()
        budget_ms = min(max(int(request.timeout_ms or self.total_timeout_ms), 500), self.total_timeout_ms)
        deadline_ms = _deadline_ms(budget_ms)
        refresh_manager = OAuthRefreshManager(db, self.crypto_service)

        cred_row = db.execute(text("""
            SELECT id, account_id, chatgpt_plan_type
            FROM provider_credentials
            WHERE tenant_id = :tenant_id
              AND provider = 'openai-codex'
              AND provider_runtime = 'codex_app_server'
              AND credential_type = 'oauth'
              AND status = 'active'
              AND revoked_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
        """), {"tenant_id": request.tenant_id}).mappings().first()

        if not cred_row:
            return ProviderResult.unavailable(self.name, "no_active_credential", int((time.monotonic() - started) * 1000))

        access_token = await refresh_manager.get_valid_access_token(request.tenant_id, cred_row["id"])
        if not access_token:
            return ProviderResult.unavailable(self.name, "credential_error", int((time.monotonic() - started) * 1000))

        login_payload = {
            "type": "chatgptAuthTokens",
            "accessToken": access_token,
            "chatgptAccountId": cred_row["account_id"],
            "chatgptPlanType": cred_row["chatgpt_plan_type"],
        }

        try:
            timeout = self._http_timeout(deadline_ms)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                headers = self._headers(request_id=request.request_id, deadline_ms=deadline_ms)
                if self.auth_mode == "legacy_login" and self.login_url:
                    await self._post_login(client, login_payload)
                    reply_payload = self._reply_payload(request)
                else:
                    if self.app_env == "production" and self.auth_mode not in {"per_request", "legacy_login"}:
                        return ProviderResult.unavailable(self.name, "codex_auth_mode_invalid", int((time.monotonic() - started) * 1000))
                    if self.app_env == "production" and self.auth_mode != "per_request" and not self.allow_combined_login_reply:
                        return ProviderResult.unavailable(self.name, "codex_login_boundary_not_configured", int((time.monotonic() - started) * 1000))
                    reply_payload = {"login": login_payload, **self._reply_payload(request)}

                response = await client.post(self.bridge_url, json=reply_payload, headers=headers)
                response_headers = getattr(response, "headers", {}) or {}
                bridge_elapsed_ms = int(response_headers.get("X-Nexus-Codex-Elapsed-Ms") or 0)
                backend = response_headers.get("X-Nexus-Codex-Backend") or None
                if response.status_code >= 400:
                    error_code = _safe_error_code(response)
                    return ProviderResult(
                        ok=False,
                        provider=self.name,
                        elapsed_ms=int((time.monotonic() - started) * 1000),
                        error_code=error_code,
                        retryable=response.status_code in {408, 429, 500, 502, 503, 504},
                        fallback_allowed=True,
                        raw_payload_safe_summary=self._safe_reply_summary(
                            response_status=response.status_code,
                            bridge_elapsed_ms=bridge_elapsed_ms,
                            backend=backend,
                            deadline_ms=deadline_ms,
                            error_code=error_code,
                        ),
                    )
                response.raise_for_status()
                return ProviderResult(
                    ok=True,
                    provider=self.name,
                    elapsed_ms=int((time.monotonic() - started) * 1000),
                    structured_output=response.json(),
                    raw_payload_safe_summary=self._safe_reply_summary(
                        response_status=response.status_code,
                        bridge_elapsed_ms=bridge_elapsed_ms,
                        backend=backend,
                        deadline_ms=deadline_ms,
                        error_code=None,
                    ),
                )
        except httpx.TimeoutException:
            return ProviderResult.unavailable(self.name, "bridge_timeout", int((time.monotonic() - started) * 1000))
        except httpx.HTTPError:
            return ProviderResult.unavailable(self.name, "bridge_error", int((time.monotonic() - started) * 1000))
        except Exception:
            return ProviderResult.unavailable(self.name, "internal_error", int((time.monotonic() - started) * 1000))

    async def _post_login(self, client: httpx.AsyncClient, login_payload: dict) -> None:
        response = await client.post(self.login_url, json={"login": login_payload}, headers=self._headers(request_id="legacy-login", deadline_ms=_deadline_ms(1000)))
        response.raise_for_status()

    def _http_timeout(self, deadline_ms: int) -> httpx.Timeout:
        remaining = max(0.1, (deadline_ms - int(time.time() * 1000)) / 1000.0)
        connect = min(max(self.connect_timeout_ms / 1000.0, 0.05), remaining)
        return httpx.Timeout(timeout=remaining, connect=connect, read=remaining, write=remaining, pool=connect)

    def _safe_reply_summary(
        self,
        *,
        response_status: int,
        bridge_elapsed_ms: int,
        backend: str | None,
        deadline_ms: int,
        error_code: str | None,
    ) -> dict[str, Any]:
        return {
            "bridge_status": response_status,
            "bridge_host_hash": self._host_hash(self.bridge_url),
            "auth_mode": self.auth_mode,
            "hotpath_readyz": False,
            "bridge_elapsed_ms": bridge_elapsed_ms,
            "codex_deadline_ms": deadline_ms,
            "codex_error_code": error_code,
            "reply_generation_backend": backend,
        }

    @staticmethod
    def _reply_payload(request: ProviderRequest) -> dict:
        return {
            "messages": request.recent_context or [],
            "body": request.body,
            "contract": request.output_contract,
            "tracking_fact_summary": request.tracking_fact_summary,
            "tracking_fact_evidence_present": request.tracking_fact_evidence_present,
        }

    @staticmethod
    def _host_hash(url: str) -> str:
        host = urllib.parse.urlparse(url).hostname or ""
        return hashlib.sha256(host.encode("utf-8")).hexdigest()[:16]


def _int_env(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, min(value, maximum))


def _deadline_ms(budget_ms: int) -> int:
    return int(time.time() * 1000) + max(1, int(budget_ms))


def _safe_error_code(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"bridge_http_{response.status_code}"
    if isinstance(payload, dict):
        value = payload.get("error") or payload.get("reason")
        if isinstance(value, str) and value:
            return value[:120]
    return f"bridge_http_{response.status_code}"
