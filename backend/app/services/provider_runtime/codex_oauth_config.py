from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin


CODEX_PROVIDER = "openai-codex"
DEFAULT_TENANT_ID = "default"
OPENCLAW_CODEX_AUTHORIZATION_URL = "https://auth.openai.com/oauth/authorize"
OPENCLAW_CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENCLAW_CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENCLAW_CODEX_REDIRECT_URI = "http://localhost:1455/auth/callback"
OPENCLAW_CODEX_SCOPE = "openid profile email offline_access"


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.environ.get(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def _split_scopes(value: str | None) -> list[str]:
    if not value:
        return []
    # Accept either OAuth's space separated form or comma separated env values.
    normalized = value.replace(",", " ")
    return [item.strip() for item in normalized.split() if item.strip()]


def _read_secret_file(path_value: str | None) -> str | None:
    if not path_value:
        return None
    try:
        value = Path(path_value).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _app_env() -> str:
    return os.environ.get("APP_ENV", os.environ.get("ENV", "development")).strip().lower() or "development"


@dataclass(frozen=True)
class CodexOAuthConfig:
    authorization_code_enabled: bool
    device_flow_enabled: bool
    auth_base_url: str | None
    authorization_url: str | None
    token_url: str | None
    revoke_url: str | None
    redirect_uri: str | None
    client_id: str | None
    client_secret: str | None
    default_scopes: tuple[str, ...]
    allowed_scopes: tuple[str, ...]
    state_ttl_seconds: int
    include_nonce: bool
    authorize_prompt: str | None
    success_redirect_url: str | None
    failure_redirect_url: str | None
    tenant_id: str

    @classmethod
    def from_env(cls) -> "CodexOAuthConfig":
        auth_base = os.environ.get("CODEX_OAUTH_AUTH_BASE_URL", "").strip().rstrip("/") or None
        token_value = (
            os.environ.get("CODEX_OAUTH_TOKEN_URL")
            or os.environ.get("CODEX_OAUTH_TOKEN_PATH")
            or ""
        ).strip()
        revoke_value = (
            os.environ.get("CODEX_OAUTH_REVOKE_URL")
            or os.environ.get("CODEX_OAUTH_REVOKE_PATH")
            or ""
        ).strip()
        authorization_value = (
            os.environ.get("CODEX_OAUTH_AUTHORIZATION_URL")
            or os.environ.get("CODEX_OAUTH_AUTHORIZE_URL")
            or os.environ.get("CODEX_OAUTH_AUTHORIZATION_PATH")
            or ""
        ).strip()

        client_secret_file = os.environ.get("CODEX_OAUTH_CLIENT_SECRET_FILE", "").strip() or None
        client_secret = _read_secret_file(client_secret_file)
        raw_client_secret = os.environ.get("CODEX_OAUTH_CLIENT_SECRET", "").strip() or None
        if raw_client_secret:
            if _app_env() == "production":
                raise RuntimeError("CODEX_OAUTH_CLIENT_SECRET is forbidden in production; use CODEX_OAUTH_CLIENT_SECRET_FILE")
            client_secret = raw_client_secret

        return cls(
            authorization_code_enabled=_env_bool("CODEX_OAUTH_AUTHORIZATION_CODE_ENABLED", False),
            device_flow_enabled=_env_bool("CODEX_OAUTH_DEVICE_FLOW_ENABLED", False),
            auth_base_url=auth_base,
            authorization_url=_absolute_url(auth_base, authorization_value),
            token_url=_absolute_url(auth_base, token_value),
            revoke_url=_absolute_url(auth_base, revoke_value),
            redirect_uri=os.environ.get("CODEX_OAUTH_REDIRECT_URI", "").strip() or None,
            client_id=os.environ.get("CODEX_OAUTH_CLIENT_ID", "").strip() or None,
            client_secret=client_secret,
            default_scopes=tuple(_split_scopes(os.environ.get("CODEX_OAUTH_DEFAULT_SCOPES"))),
            allowed_scopes=tuple(_split_scopes(os.environ.get("CODEX_OAUTH_ALLOWED_SCOPES"))),
            state_ttl_seconds=_env_int("CODEX_OAUTH_STATE_TTL_SECONDS", 600, minimum=60, maximum=3600),
            include_nonce=_env_bool("CODEX_OAUTH_INCLUDE_NONCE", True),
            authorize_prompt=os.environ.get("CODEX_OAUTH_AUTHORIZE_PROMPT", "").strip() or None,
            success_redirect_url=os.environ.get("CODEX_OAUTH_CALLBACK_SUCCESS_REDIRECT_URL", "").strip() or None,
            failure_redirect_url=os.environ.get("CODEX_OAUTH_CALLBACK_FAILURE_REDIRECT_URL", "").strip() or None,
            tenant_id=os.environ.get("PROVIDER_RUNTIME_DEFAULT_TENANT_ID", DEFAULT_TENANT_ID).strip() or DEFAULT_TENANT_ID,
        )

    def require_authorization_code_flow(self) -> None:
        if not self.authorization_code_enabled:
            raise ValueError("Codex authorization-code flow is disabled")
        missing = []
        if not self.authorization_url:
            missing.append("CODEX_OAUTH_AUTHORIZATION_URL")
        if not self.token_url:
            missing.append("CODEX_OAUTH_TOKEN_URL or CODEX_OAUTH_TOKEN_PATH")
        if not self.redirect_uri:
            missing.append("CODEX_OAUTH_REDIRECT_URI")
        if not self.client_id:
            missing.append("CODEX_OAUTH_CLIENT_ID")
        if missing:
            raise ValueError("Codex authorization-code flow missing configuration: " + ", ".join(missing))

    def require_token_endpoint(self) -> None:
        missing = []
        if not self.token_url:
            missing.append("CODEX_OAUTH_TOKEN_URL or CODEX_OAUTH_TOKEN_PATH")
        if not self.client_id:
            missing.append("CODEX_OAUTH_CLIENT_ID")
        if missing:
            raise ValueError("Codex token endpoint missing configuration: " + ", ".join(missing))

    def openclaw_manual_authorization_url(self) -> str:
        return os.environ.get("CODEX_OPENCLAW_AUTHORIZATION_URL", "").strip() or OPENCLAW_CODEX_AUTHORIZATION_URL

    def openclaw_manual_token_url(self) -> str:
        return os.environ.get("CODEX_OPENCLAW_TOKEN_URL", "").strip() or OPENCLAW_CODEX_TOKEN_URL

    def openclaw_manual_redirect_uri(self) -> str:
        return os.environ.get("CODEX_OPENCLAW_REDIRECT_URI", "").strip() or OPENCLAW_CODEX_REDIRECT_URI

    def openclaw_manual_scope(self) -> str:
        return os.environ.get("CODEX_OPENCLAW_SCOPE", "").strip() or OPENCLAW_CODEX_SCOPE

    def normalize_scope(self, requested_scopes: list[str] | tuple[str, ...] | None = None) -> str:
        scopes = list(requested_scopes or self.default_scopes)
        deduped: list[str] = []
        for scope in scopes:
            cleaned = scope.strip()
            if cleaned and cleaned not in deduped:
                deduped.append(cleaned)
        if self.allowed_scopes:
            unknown = [scope for scope in deduped if scope not in self.allowed_scopes]
            if unknown:
                raise ValueError("Requested Codex OAuth scope is not allowlisted: " + ", ".join(sorted(unknown)))
        return " ".join(deduped)


def _absolute_url(auth_base: str | None, path_or_url: str | None) -> str | None:
    value = (path_or_url or "").strip()
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if not auth_base:
        return value
    return urljoin(auth_base.rstrip("/") + "/", value.lstrip("/"))


def resolve_provider_tenant_id(current_user=None) -> str:
    tenant_from_user = getattr(current_user, "tenant_id", None)
    if tenant_from_user:
        return str(tenant_from_user)
    return CodexOAuthConfig.from_env().tenant_id
