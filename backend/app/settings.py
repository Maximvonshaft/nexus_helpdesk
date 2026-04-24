from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.backend_root = self.project_root / "backend"
        self.legacy_frontend_root = self.project_root / "frontend"
        self.frontend_dist_root = self.project_root / "frontend_dist"
        self.frontend_root = self.frontend_dist_root if self.frontend_dist_root.exists() else self.legacy_frontend_root

        self.app_env = os.getenv("APP_ENV", "development").strip().lower() or "development"
        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./helpdesk.db").strip()
        self.database_echo = os.getenv("DATABASE_ECHO", "false").strip().lower() == "true"
        self.jwt_secret_key = os.getenv("SECRET_KEY")
        self.jwt_issuer = os.getenv("JWT_ISSUER", "helpdesk-suite")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "helpdesk-suite-users")
        self.access_token_expire_hours = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "12"))
        self.allow_dev_auth = os.getenv("ALLOW_DEV_AUTH", "false").strip().lower() == "true" and self.app_env != "production"

        self.auto_init_db = os.getenv("AUTO_INIT_DB", "false").strip().lower() == "true" and self.app_env != "production"
        self.seed_demo_data = os.getenv("SEED_DEMO_DATA", "false").strip().lower() == "true" and self.app_env != "production"

        self.storage_backend = os.getenv("STORAGE_BACKEND", "local").strip().lower() or "local"
        default_upload_root = self.backend_root / "uploads"
        self.upload_root = Path(os.getenv("UPLOAD_ROOT", str(default_upload_root))).resolve()
        self.s3_bucket = os.getenv("S3_BUCKET")
        self.s3_endpoint_url = os.getenv("S3_ENDPOINT_URL")
        self.s3_region = os.getenv("S3_REGION")
        self.s3_access_key = os.getenv("S3_ACCESS_KEY")
        self.s3_secret_key = os.getenv("S3_SECRET_KEY")
        self.s3_presign_expiry_seconds = int(os.getenv("S3_PRESIGN_EXPIRY_SECONDS", "900"))

        self.allowed_origins = self._parse_origins(os.getenv("ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1"))
        self.allowed_upload_mime_types = self._parse_csv(
            os.getenv(
                "ALLOWED_UPLOAD_MIME_TYPES",
                "image/jpeg,image/png,image/webp,application/pdf,text/plain",
            )
        )
        self.allowed_upload_extensions = {ext.lower() for ext in self._parse_csv(os.getenv("ALLOWED_UPLOAD_EXTENSIONS", ".jpg,.jpeg,.png,.webp,.pdf,.txt"))}
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.openclaw_bin = os.getenv("OPENCLAW_BIN")
        self.openclaw_transport = os.getenv("OPENCLAW_TRANSPORT", "mcp").strip().lower() or "mcp"
        self.openclaw_deployment_mode = os.getenv("OPENCLAW_DEPLOYMENT_MODE", "local_gateway").strip().lower() or "local_gateway"
        self.openclaw_mcp_command = os.getenv("OPENCLAW_MCP_COMMAND", self.openclaw_bin or "openclaw").strip()
        self.openclaw_extra_paths = self._parse_paths(os.getenv("OPENCLAW_EXTRA_PATHS", ""))
        self.openclaw_mcp_url = os.getenv("OPENCLAW_MCP_URL")
        self.openclaw_mcp_token_file = os.getenv("OPENCLAW_MCP_TOKEN_FILE")
        self.openclaw_mcp_password_file = os.getenv("OPENCLAW_MCP_PASSWORD_FILE")
        self.openclaw_mcp_claude_channel_mode = os.getenv("OPENCLAW_MCP_CLAUDE_CHANNEL_MODE", "off").strip().lower() or "off"
        self.openclaw_cli_fallback_enabled = os.getenv("OPENCLAW_CLI_FALLBACK_ENABLED", "true").strip().lower() == "true"
        self.openclaw_bridge_enabled = os.getenv("OPENCLAW_BRIDGE_ENABLED", "false").strip().lower() == "true"
        self.openclaw_bridge_url = (os.getenv("OPENCLAW_BRIDGE_URL", "http://127.0.0.1:18792").strip() or "http://127.0.0.1:18792").rstrip("/")
        self.openclaw_bridge_timeout_seconds = int(os.getenv("OPENCLAW_BRIDGE_TIMEOUT_SECONDS", "20"))
        self.enable_outbound_dispatch = os.getenv("ENABLE_OUTBOUND_DISPATCH", "false").strip().lower() == "true"
        self.outbound_provider = os.getenv("OUTBOUND_PROVIDER", "disabled").strip().lower() or "disabled"
        self.outbox_batch_size = int(os.getenv("OUTBOX_BATCH_SIZE", "50"))
        self.outbox_lock_seconds = int(os.getenv("OUTBOX_LOCK_SECONDS", "300"))
        self.outbox_max_retries = int(os.getenv("OUTBOX_MAX_RETRIES", "3"))

        self.job_batch_size = int(os.getenv("JOB_BATCH_SIZE", "25"))
        self.job_lock_seconds = int(os.getenv("JOB_LOCK_SECONDS", "300"))
        self.job_max_retries = int(os.getenv("JOB_MAX_RETRIES", "3"))
        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "2"))
        self.openclaw_sync_enabled = os.getenv("OPENCLAW_SYNC_ENABLED", "true").strip().lower() == "true"
        self.openclaw_sync_batch_size = int(os.getenv("OPENCLAW_SYNC_BATCH_SIZE", "50"))
        self.openclaw_sync_stale_seconds = int(os.getenv("OPENCLAW_SYNC_STALE_SECONDS", "120"))
        self.openclaw_sync_transcript_limit = int(os.getenv("OPENCLAW_SYNC_TRANSCRIPT_LIMIT", "100"))
        self.openclaw_sync_poll_timeout_seconds = int(os.getenv("OPENCLAW_SYNC_POLL_TIMEOUT_SECONDS", "10"))
        self.openclaw_session_dm_scope = os.getenv("OPENCLAW_SESSION_DM_SCOPE", "per-account-channel-peer").strip()
        self.openclaw_event_driver_enabled = os.getenv("OPENCLAW_EVENT_DRIVER_ENABLED", "true").strip().lower() == "true"
        self.openclaw_sync_daemon_stale_seconds = int(os.getenv("OPENCLAW_SYNC_DAEMON_STALE_SECONDS", "90"))
        self.require_prometheus_client_in_production = os.getenv("REQUIRE_PROMETHEUS_CLIENT_IN_PRODUCTION", "false").strip().lower() == "true"

        self.login_max_failures = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
        self.login_lock_minutes = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
        self.trusted_proxy_ips = self._parse_csv(os.getenv("TRUSTED_PROXY_IPS", ""))

        self.allow_legacy_integration_api_key = os.getenv("ALLOW_LEGACY_INTEGRATION_API_KEY", "false").strip().lower() == "true"
        self.integration_api_key = os.getenv("INTEGRATION_API_KEY")
        self.integration_default_rate_limit_per_minute = int(os.getenv("INTEGRATION_DEFAULT_RATE_LIMIT_PER_MINUTE", "60"))
        self.integration_require_idempotency_key = os.getenv("INTEGRATION_REQUIRE_IDEMPOTENCY_KEY", "true").strip().lower() == "true"

        self.request_id_header = os.getenv("REQUEST_ID_HEADER", "X-Request-Id")
        self.log_json = os.getenv("LOG_JSON", "true").strip().lower() == "true"
        self.metrics_enabled = os.getenv("METRICS_ENABLED", "false").strip().lower() == "true"
        self.metrics_token = os.getenv("METRICS_TOKEN")

        self.openclaw_attachment_url_fetch_enabled = os.getenv("OPENCLAW_ATTACHMENT_URL_FETCH_ENABLED", "false").strip().lower() == "true"
        self.openclaw_attachment_allowed_hosts = [host.lower() for host in self._parse_csv(os.getenv("OPENCLAW_ATTACHMENT_ALLOWED_HOSTS", ""))]
        self.openclaw_attachment_fetch_timeout_seconds = int(os.getenv("OPENCLAW_ATTACHMENT_FETCH_TIMEOUT_SECONDS", "10"))
        self.openclaw_attachment_max_download_bytes = int(os.getenv("OPENCLAW_ATTACHMENT_MAX_DOWNLOAD_BYTES", str(self.max_upload_bytes)))
        self.openclaw_attachment_allowed_mime_types = set(self._parse_csv(os.getenv("OPENCLAW_ATTACHMENT_ALLOWED_MIME_TYPES", ",".join(self.allowed_upload_mime_types + ["application/octet-stream"]))))

        self._normalize()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://"))

    def _normalize(self) -> None:
        self.upload_root.mkdir(parents=True, exist_ok=True)
        if self.app_env == "production":
            if not self.jwt_secret_key:
                raise RuntimeError("SECRET_KEY must be set in production")
            weak_secrets = {"change-me", "changeme", "replace-me", "replace_this", "secret", "default"}
            normalized_secret = self.jwt_secret_key.strip().lower()
            if normalized_secret in weak_secrets or self.jwt_secret_key.startswith("dev-only-"):
                raise RuntimeError("SECRET_KEY must be a non-placeholder production secret")
            if not self.is_postgres:
                raise RuntimeError("Production requires a PostgreSQL DATABASE_URL")
            if self.auto_init_db or self.seed_demo_data:
                raise RuntimeError("AUTO_INIT_DB and SEED_DEMO_DATA must be disabled in production")
            if self.allow_dev_auth:
                raise RuntimeError("ALLOW_DEV_AUTH must be disabled in production")
            if self.allow_legacy_integration_api_key:
                raise RuntimeError("ALLOW_LEGACY_INTEGRATION_API_KEY must be disabled in production")
            localhost_origins = {"http://localhost", "http://127.0.0.1"}
            if set(self.allowed_origins) & localhost_origins:
                raise RuntimeError("Production ALLOWED_ORIGINS must not include localhost defaults")
            if self.storage_backend not in {"local", "s3"}:
                raise RuntimeError("STORAGE_BACKEND must be local or s3")
            if self.openclaw_transport not in {"mcp", "cli"}:
                raise RuntimeError("OPENCLAW_TRANSPORT must be mcp or cli")
            if self.openclaw_deployment_mode not in {"local_gateway", "remote_gateway", "disabled"}:
                raise RuntimeError("OPENCLAW_DEPLOYMENT_MODE must be local_gateway, remote_gateway, or disabled")
            if self.openclaw_session_dm_scope not in {"per-account-channel-peer", "per-channel-peer", "per-peer"}:
                raise RuntimeError("OPENCLAW_SESSION_DM_SCOPE must be a supported session dm scope")
            if self.metrics_enabled and not self.metrics_token:
                raise RuntimeError("METRICS_TOKEN must be set in production when METRICS_ENABLED=true")
            if self.openclaw_attachment_url_fetch_enabled and not self.openclaw_attachment_allowed_hosts:
                raise RuntimeError("OPENCLAW_ATTACHMENT_ALLOWED_HOSTS must be set when OPENCLAW_ATTACHMENT_URL_FETCH_ENABLED=true in production")
            if self.require_prometheus_client_in_production:
                try:
                    import prometheus_client  # noqa: F401
                except Exception as exc:
                    raise RuntimeError("prometheus_client must be installed in production when REQUIRE_PROMETHEUS_CLIENT_IN_PRODUCTION=true") from exc
        if not self.jwt_secret_key:
            self.jwt_secret_key = f"dev-only-{secrets.token_urlsafe(24)}"

    @staticmethod
    def _parse_origins(raw: str) -> list[str]:
        values = [item.strip() for item in raw.split(",") if item.strip()]
        return values or ["http://localhost", "http://127.0.0.1"]

    @staticmethod
    def _parse_csv(raw: str) -> list[str]:
        return [item.strip() for item in raw.split(",") if item.strip()]

    @staticmethod
    def _parse_paths(raw: str) -> list[str]:
        if not raw:
            return []
        parts: list[str] = []
        for item in raw.replace(";", os.pathsep).split(os.pathsep):
            cleaned = item.strip()
            if cleaned:
                parts.append(cleaned)
        return parts


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
