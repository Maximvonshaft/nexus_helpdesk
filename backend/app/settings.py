from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}



class Settings:
    def __init__(self) -> None:
        self.project_root = Path(__file__).resolve().parents[2]
        self.backend_root = self.project_root / "backend"
        self.app_env = os.getenv("APP_ENV", "development").strip().lower() or "development"
        self.nexus_osr_release_profile = os.getenv("NEXUS_OSR_RELEASE_PROFILE", "development").strip().lower() or "development"
        self.expected_migration_head = os.getenv("EXPECTED_MIGRATION_HEAD", "").strip() or None
        self.legacy_frontend_root = self.project_root / "frontend"
        self.frontend_dist_root = self.project_root / "frontend_dist"
        self.frontend_dist_index = self.frontend_dist_root / "index.html"
        self.frontend_dist_available = self.frontend_dist_index.exists()
        self.frontend_root = self.frontend_dist_root if self.frontend_dist_available else self.legacy_frontend_root
        self.frontend_uses_legacy_fallback = not self.frontend_dist_available

        self.database_url = os.getenv("DATABASE_URL", "sqlite:///./helpdesk.db").strip()
        self.database_echo = os.getenv("DATABASE_ECHO", "false").strip().lower() == "true"
        self.jwt_secret_key = os.getenv("SECRET_KEY")
        self.jwt_issuer = os.getenv("JWT_ISSUER", "helpdesk-suite")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "helpdesk-suite-users")
        self.access_token_expire_hours = int(os.getenv("ACCESS_TOKEN_EXPIRE_HOURS", "12"))
        self.allow_dev_auth_raw = os.getenv("ALLOW_DEV_AUTH", "false")
        self.allow_dev_auth_requested = self._is_truthy(self.allow_dev_auth_raw)
        self.allow_dev_auth = self.allow_dev_auth_requested and self.app_env in {"development", "test", "local"}

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
                "image/jpeg,image/png,image/webp,application/pdf,text/plain,text/markdown,text/csv,text/html,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        )
        self.allowed_upload_extensions = {ext.lower() for ext in self._parse_csv(os.getenv("ALLOWED_UPLOAD_EXTENSIONS", ".jpg,.jpeg,.png,.webp,.pdf,.txt,.md,.markdown,.csv,.html,.htm,.docx,.xlsx"))}
        self.max_upload_bytes = int(os.getenv("MAX_UPLOAD_BYTES", str(10 * 1024 * 1024)))

        self.dashscope_api_key = os.getenv("DASHSCOPE_API_KEY")
        self.external_channel_bin = os.getenv("EXTERNAL_CHANNEL_BIN")
        self.external_channel_transport = os.getenv("EXTERNAL_CHANNEL_TRANSPORT", "disabled").strip().lower() or "disabled"
        self.external_channel_deployment_mode = os.getenv("EXTERNAL_CHANNEL_DEPLOYMENT_MODE", "disabled").strip().lower() or "disabled"
        self.external_channel_mcp_command = os.getenv("EXTERNAL_CHANNEL_MCP_COMMAND", self.external_channel_bin or "").strip()
        self.external_channel_extra_paths = self._parse_paths(os.getenv("EXTERNAL_CHANNEL_EXTRA_PATHS", ""))
        self.external_channel_mcp_url = os.getenv("EXTERNAL_CHANNEL_MCP_URL")
        self.external_channel_mcp_token_file = os.getenv("EXTERNAL_CHANNEL_MCP_TOKEN_FILE")
        self.external_channel_mcp_password_file = os.getenv("EXTERNAL_CHANNEL_MCP_PASSWORD_FILE")
        self.external_channel_mcp_claude_channel_mode = os.getenv("EXTERNAL_CHANNEL_MCP_CLAUDE_CHANNEL_MODE", "off").strip().lower() or "off"
        self.external_channel_cli_fallback_enabled = os.getenv("EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED", "false").strip().lower() == "true"
        self.external_channel_bridge_enabled = os.getenv("EXTERNAL_CHANNEL_BRIDGE_ENABLED", "false").strip().lower() == "true"
        self.external_channel_bridge_url = (os.getenv("EXTERNAL_CHANNEL_BRIDGE_URL", "http://127.0.0.1:18792").strip() or "http://127.0.0.1:18792").rstrip("/")
        self.external_channel_bridge_timeout_seconds = int(os.getenv("EXTERNAL_CHANNEL_BRIDGE_TIMEOUT_SECONDS", "20"))
        self.enable_outbound_dispatch = os.getenv("ENABLE_OUTBOUND_DISPATCH", "false").strip().lower() == "true"
        self.outbound_provider = os.getenv("OUTBOUND_PROVIDER", "disabled").strip().lower() or "disabled"
        self.whatsapp_native_enabled = _env_bool("WHATSAPP_NATIVE_ENABLED", False)
        self.whatsapp_dispatch_mode = os.getenv("WHATSAPP_DISPATCH_MODE", "disabled").strip().lower() or "disabled"
        self.whatsapp_sidecar_url = os.getenv("WHATSAPP_SIDECAR_URL", "http://127.0.0.1:18793").strip().rstrip("/")
        self.whatsapp_sidecar_token = os.getenv("WHATSAPP_SIDECAR_TOKEN", "").strip() or None
        self.whatsapp_sidecar_timeout_seconds = int(os.getenv("WHATSAPP_SIDECAR_TIMEOUT_SECONDS", "8"))
        self.whatsapp_connector_key = os.getenv("WHATSAPP_CONNECTOR_KEY", "").strip() or None
        self.whatsapp_connector_hmac_secret = os.getenv("WHATSAPP_CONNECTOR_HMAC_SECRET", "").strip() or None
        self.whatsapp_connector_timestamp_tolerance_seconds = int(os.getenv("WHATSAPP_CONNECTOR_TIMESTAMP_TOLERANCE_SECONDS", "300"))
        self.outbound_email_production_pilot_enabled = _env_bool("OUTBOUND_EMAIL_PRODUCTION_PILOT_ENABLED", False)
        self.outbound_email_test_send_max_age_hours = int(os.getenv("OUTBOUND_EMAIL_TEST_SEND_MAX_AGE_HOURS", "24"))
        self.outbox_batch_size = int(os.getenv("OUTBOX_BATCH_SIZE", "50"))
        self.outbox_lock_seconds = int(os.getenv("OUTBOX_LOCK_SECONDS", "300"))
        self.outbox_max_retries = int(os.getenv("OUTBOX_MAX_RETRIES", "3"))
        self.allow_legacy_originless_outbound = _env_bool("ALLOW_LEGACY_ORIGINLESS_OUTBOUND", False)
        self.email_mailbox_sync_enabled = _env_bool("EMAIL_MAILBOX_SYNC_ENABLED", True)
        self.email_mailbox_sync_interval_seconds = int(os.getenv("EMAIL_MAILBOX_SYNC_INTERVAL_SECONDS", "60"))
        self.email_mailbox_sync_batch_size = int(os.getenv("EMAIL_MAILBOX_SYNC_BATCH_SIZE", "20"))

        self.job_batch_size = int(os.getenv("JOB_BATCH_SIZE", "25"))
        self.job_lock_seconds = int(os.getenv("JOB_LOCK_SECONDS", "300"))
        self.job_max_retries = int(os.getenv("JOB_MAX_RETRIES", "3"))
        self.worker_poll_seconds = float(os.getenv("WORKER_POLL_SECONDS", "2"))
        self.nexus_osr_required_workers = tuple(self._parse_csv(os.getenv("NEXUS_OSR_REQUIRED_WORKERS", "background_worker,outbound_worker,webchat_ai_worker,handoff_snapshot_worker,operations_dispatch_worker")))
        self.nexus_osr_worker_stale_seconds = int(os.getenv("NEXUS_OSR_WORKER_STALE_SECONDS", "90"))
        self.nexus_osr_queue_warn_age_seconds = int(os.getenv("NEXUS_OSR_QUEUE_WARN_AGE_SECONDS", "120"))
        self.nexus_osr_queue_fail_age_seconds = int(os.getenv("NEXUS_OSR_QUEUE_FAIL_AGE_SECONDS", "600"))
        self.nexus_osr_dispatch_warn_age_seconds = int(os.getenv("NEXUS_OSR_DISPATCH_WARN_AGE_SECONDS", "120"))
        self.nexus_osr_dispatch_fail_age_seconds = int(os.getenv("NEXUS_OSR_DISPATCH_FAIL_AGE_SECONDS", "600"))
        self.webchat_ai_worker_poll_seconds = float(os.getenv("WEBCHAT_AI_WORKER_POLL_SECONDS", "0.35"))
        self.webchat_ai_worker_busy_poll_seconds = float(os.getenv("WEBCHAT_AI_WORKER_BUSY_POLL_SECONDS", "0.05"))
        self.external_channel_sync_enabled = os.getenv("EXTERNAL_CHANNEL_SYNC_ENABLED", "false").strip().lower() == "true"
        self.external_channel_sync_batch_size = int(os.getenv("EXTERNAL_CHANNEL_SYNC_BATCH_SIZE", "50"))
        self.external_channel_sync_stale_seconds = int(os.getenv("EXTERNAL_CHANNEL_SYNC_STALE_SECONDS", "120"))
        self.external_channel_sync_transcript_limit = int(os.getenv("EXTERNAL_CHANNEL_SYNC_TRANSCRIPT_LIMIT", "100"))
        self.external_channel_sync_poll_timeout_seconds = int(os.getenv("EXTERNAL_CHANNEL_SYNC_POLL_TIMEOUT_SECONDS", "10"))
        self.external_channel_inbound_auto_sync_enabled = os.getenv("EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED", "false").strip().lower() == "true"
        self.external_channel_inbound_sync_limit = int(os.getenv("EXTERNAL_CHANNEL_INBOUND_SYNC_LIMIT", "10"))
        self.external_channel_inbound_sync_message_limit = int(os.getenv("EXTERNAL_CHANNEL_INBOUND_SYNC_MESSAGE_LIMIT", str(self.external_channel_sync_transcript_limit)))
        self.external_channel_inbound_sync_include_groups = os.getenv("EXTERNAL_CHANNEL_INBOUND_SYNC_INCLUDE_GROUPS", "false").strip().lower() == "true"
        self.external_channel_inbound_auto_sync_interval_seconds = int(os.getenv("EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_INTERVAL_SECONDS", "30"))
        self.external_channel_session_dm_scope = os.getenv("EXTERNAL_CHANNEL_SESSION_DM_SCOPE", "per-account-channel-peer").strip()
        self.external_channel_event_driver_enabled = os.getenv("EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED", "false").strip().lower() == "true"
        self.external_channel_sync_daemon_stale_seconds = int(os.getenv("EXTERNAL_CHANNEL_SYNC_DAEMON_STALE_SECONDS", "90"))
        self.require_prometheus_client_in_production = os.getenv("REQUIRE_PROMETHEUS_CLIENT_IN_PRODUCTION", "false").strip().lower() == "true"
        self.runtime_contract_signing_secret = os.getenv("RUNTIME_CONTRACT_SIGNING_SECRET", "").strip()

        self.login_max_failures = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
        self.login_lock_minutes = int(os.getenv("LOGIN_LOCK_MINUTES", "15"))
        self.trusted_proxy_ips = self._parse_csv(os.getenv("TRUSTED_PROXY_IPS", ""))

        self.allow_legacy_integration_api_key = os.getenv("ALLOW_LEGACY_INTEGRATION_API_KEY", "false").strip().lower() == "true"
        self.integration_api_key = os.getenv("INTEGRATION_API_KEY")
        self.integration_default_rate_limit_per_minute = int(os.getenv("INTEGRATION_DEFAULT_RATE_LIMIT_PER_MINUTE", "60"))
        self.integration_require_idempotency_key = os.getenv("INTEGRATION_REQUIRE_IDEMPOTENCY_KEY", "true").strip().lower() == "true"

        self.webchat_allowed_origins = self._parse_csv(os.getenv("WEBCHAT_ALLOWED_ORIGINS", ""))
        self.webchat_allow_no_origin = os.getenv("WEBCHAT_ALLOW_NO_ORIGIN", "false").strip().lower() == "true"
        self.webchat_allow_legacy_token_transport = os.getenv("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT", "false").strip().lower() == "true"
        self.webchat_rate_limit_backend = os.getenv("WEBCHAT_RATE_LIMIT_BACKEND", "database" if self.app_env == "production" else "memory").strip().lower() or "database"
        self.webchat_rate_limit_window_seconds = int(os.getenv("WEBCHAT_RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.webchat_rate_limit_max_requests = int(os.getenv("WEBCHAT_RATE_LIMIT_MAX_REQUESTS", "20"))
        self.admin_action_rate_limit_enabled = os.getenv("ADMIN_ACTION_RATE_LIMIT_ENABLED", "true").strip().lower() == "true"
        self.admin_action_rate_limit_window_seconds = int(os.getenv("ADMIN_ACTION_RATE_LIMIT_WINDOW_SECONDS", "60"))
        self.admin_action_rate_limit_single_max = int(os.getenv("ADMIN_ACTION_RATE_LIMIT_SINGLE_MAX", "20"))
        self.admin_action_rate_limit_batch_max = int(os.getenv("ADMIN_ACTION_RATE_LIMIT_BATCH_MAX", "3"))
        self.admin_action_rate_limit_consume_once_max = int(os.getenv("ADMIN_ACTION_RATE_LIMIT_CONSUME_ONCE_MAX", "5"))
        self.webchat_ai_auto_reply_mode = os.getenv("WEBCHAT_AI_AUTO_REPLY_MODE", "safe_ai").strip().lower() or "safe_ai"
        self.osr_escalation_orchestration_enabled = _env_bool("OSR_ESCALATION_ORCHESTRATION_ENABLED", False)
        self.webchat_ai_turn_debounce_seconds = float(os.getenv("WEBCHAT_AI_TURN_DEBOUNCE_SECONDS", "0.15"))
        self.webchat_ai_reconciler_enabled = _env_bool("WEBCHAT_AI_RECONCILER_ENABLED", True)
        try:
            self.webchat_ai_reconciler_interval_seconds = max(
                5,
                int(os.getenv("WEBCHAT_AI_RECONCILER_INTERVAL_SECONDS", "30")),
            )
        except ValueError:
            self.webchat_ai_reconciler_interval_seconds = 30
        self.webchat_knowledge_reply_mode = os.getenv("WEBCHAT_KNOWLEDGE_REPLY_MODE", "ai_grounded").strip().lower() or "ai_grounded"
        self.webchat_knowledge_no_evidence_fallback_enabled = _env_bool("WEBCHAT_KNOWLEDGE_NO_EVIDENCE_FALLBACK_ENABLED", True)
        self.knowledge_runtime_version = os.getenv("KNOWLEDGE_RUNTIME_VERSION", "v2").strip().lower() or "v2"
        self.knowledge_embeddings_enabled = _env_bool("KNOWLEDGE_EMBEDDINGS_ENABLED", self.app_env == "production")
        self.knowledge_embedding_provider = os.getenv("KNOWLEDGE_EMBEDDING_PROVIDER", "deterministic_hash").strip().lower() or "deterministic_hash"
        self.knowledge_embedding_model = os.getenv("KNOWLEDGE_EMBEDDING_MODEL", "nexus-deterministic-hash-v1").strip()
        self.knowledge_embedding_dim = int(os.getenv("KNOWLEDGE_EMBEDDING_DIM", "384"))
        self.knowledge_embedding_batch_size = int(os.getenv("KNOWLEDGE_EMBEDDING_BATCH_SIZE", "32"))
        self.knowledge_embedding_timeout_seconds = int(os.getenv("KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS", "20"))
        self.knowledge_vector_fallback_allowed = _env_bool("KNOWLEDGE_VECTOR_FALLBACK_ALLOWED", True)
        self.knowledge_embedding_base_url = os.getenv("KNOWLEDGE_EMBEDDING_BASE_URL", "https://api.openai.com/v1").strip().rstrip("/")
        self.knowledge_embedding_api_key = os.getenv("KNOWLEDGE_EMBEDDING_API_KEY", "").strip() or None
        self.knowledge_embedding_api_key_file = os.getenv("KNOWLEDGE_EMBEDDING_API_KEY_FILE", "").strip() or None
        self.webchat_tracking_fact_lookup_enabled = os.getenv("WEBCHAT_TRACKING_FACT_LOOKUP_ENABLED", "false").strip().lower() == "true"
        self.webchat_tracking_fact_source = os.getenv("WEBCHAT_TRACKING_FACT_SOURCE", "speedaf_api").strip().lower() or "speedaf_api"
        self.webchat_tracking_fact_timeout_seconds = int(os.getenv("WEBCHAT_TRACKING_FACT_TIMEOUT_SECONDS", "8"))
        self.webchat_tracking_fact_redaction_enabled = os.getenv("WEBCHAT_TRACKING_FACT_REDACTION_ENABLED", "true").strip().lower() == "true"
        self.webchat_tracking_fact_card_enabled = os.getenv("WEBCHAT_TRACKING_FACT_CARD_ENABLED", "false").strip().lower() == "true"
        self.webchat_ai_session_ttl_hours = int(os.getenv("WEBCHAT_AI_SESSION_TTL_HOURS", "24"))
        self.webchat_ai_session_max_messages = int(os.getenv("WEBCHAT_AI_SESSION_MAX_MESSAGES", "40"))
        self.webchat_ai_session_summary_messages = int(os.getenv("WEBCHAT_AI_SESSION_SUMMARY_MESSAGES", "8"))
        self.webchat_ws_enabled = _env_bool("WEBCHAT_WS_ENABLED", False)
        self.webchat_ws_public_enabled = _env_bool("WEBCHAT_WS_PUBLIC_ENABLED", self.webchat_ws_enabled)
        self.webchat_ws_admin_enabled = _env_bool("WEBCHAT_WS_ADMIN_ENABLED", self.webchat_ws_enabled)
        self.webchat_ws_broker = os.getenv("WEBCHAT_WS_BROKER", "database").strip().lower() or "database"
        self.webchat_ws_replay_poll_ms = int(os.getenv("WEBCHAT_WS_REPLAY_POLL_MS", "500"))
        self.webchat_ws_fallback_poll_ms = int(os.getenv("WEBCHAT_WS_FALLBACK_POLL_MS", "4000"))
        self.webchat_ws_heartbeat_ms = int(os.getenv("WEBCHAT_WS_HEARTBEAT_MS", "25000"))
        self.webchat_ws_hello_timeout_ms = int(os.getenv("WEBCHAT_WS_HELLO_TIMEOUT_MS", "5000"))
        self.webchat_ws_max_connections = int(os.getenv("WEBCHAT_WS_MAX_CONNECTIONS", "1000"))
        self.webchat_ws_max_connections_per_user = int(os.getenv("WEBCHAT_WS_MAX_CONNECTIONS_PER_USER", "10"))

        self.request_id_header = os.getenv("REQUEST_ID_HEADER", "X-Request-Id")
        self.log_json = os.getenv("LOG_JSON", "true").strip().lower() == "true"
        self.metrics_enabled = os.getenv("METRICS_ENABLED", "false").strip().lower() == "true"
        self.metrics_token = os.getenv("METRICS_TOKEN")

        self.external_channel_attachment_url_fetch_enabled = os.getenv("EXTERNAL_CHANNEL_ATTACHMENT_URL_FETCH_ENABLED", "false").strip().lower() == "true"
        self.external_channel_attachment_allowed_hosts = [host.lower() for host in self._parse_csv(os.getenv("EXTERNAL_CHANNEL_ATTACHMENT_ALLOWED_HOSTS", ""))]
        self.external_channel_attachment_fetch_timeout_seconds = int(os.getenv("EXTERNAL_CHANNEL_ATTACHMENT_FETCH_TIMEOUT_SECONDS", "10"))
        self.external_channel_attachment_max_download_bytes = int(os.getenv("EXTERNAL_CHANNEL_ATTACHMENT_MAX_DOWNLOAD_BYTES", str(self.max_upload_bytes)))
        self.external_channel_attachment_allowed_mime_types = set(self._parse_csv(os.getenv("EXTERNAL_CHANNEL_ATTACHMENT_ALLOWED_MIME_TYPES", ",".join(self.allowed_upload_mime_types + ["application/octet-stream"]))))

        self._normalize()

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://"))

    def _normalize(self) -> None:
        self.upload_root.mkdir(parents=True, exist_ok=True)
        if self.webchat_rate_limit_backend not in {"memory", "database"}:
            raise RuntimeError("WEBCHAT_RATE_LIMIT_BACKEND must be memory or database")
        if self.admin_action_rate_limit_window_seconds < 1 or self.admin_action_rate_limit_window_seconds > 3600:
            raise RuntimeError("ADMIN_ACTION_RATE_LIMIT_WINDOW_SECONDS must be between 1 and 3600")
        if self.admin_action_rate_limit_single_max < 1 or self.admin_action_rate_limit_single_max > 1000:
            raise RuntimeError("ADMIN_ACTION_RATE_LIMIT_SINGLE_MAX must be between 1 and 1000")
        if self.admin_action_rate_limit_batch_max < 1 or self.admin_action_rate_limit_batch_max > 1000:
            raise RuntimeError("ADMIN_ACTION_RATE_LIMIT_BATCH_MAX must be between 1 and 1000")
        if self.admin_action_rate_limit_consume_once_max < 1 or self.admin_action_rate_limit_consume_once_max > 1000:
            raise RuntimeError("ADMIN_ACTION_RATE_LIMIT_CONSUME_ONCE_MAX must be between 1 and 1000")
        if self.webchat_ai_auto_reply_mode not in {"off", "safe_ai"}:
            raise RuntimeError("WEBCHAT_AI_AUTO_REPLY_MODE must be off or safe_ai")
        if self.worker_poll_seconds < 0.1 or self.worker_poll_seconds > 60:
            raise RuntimeError("WORKER_POLL_SECONDS must be between 0.1 and 60")
        if self.webchat_ai_worker_poll_seconds < 0.05 or self.webchat_ai_worker_poll_seconds > 10:
            raise RuntimeError("WEBCHAT_AI_WORKER_POLL_SECONDS must be between 0.05 and 10")
        if self.webchat_ai_worker_busy_poll_seconds < 0.01 or self.webchat_ai_worker_busy_poll_seconds > 5:
            raise RuntimeError("WEBCHAT_AI_WORKER_BUSY_POLL_SECONDS must be between 0.01 and 5")
        if self.webchat_ai_turn_debounce_seconds < 0 or self.webchat_ai_turn_debounce_seconds > 10:
            raise RuntimeError("WEBCHAT_AI_TURN_DEBOUNCE_SECONDS must be between 0 and 10")
        if self.webchat_knowledge_reply_mode not in {"ai_grounded", "deterministic_direct_answer"}:
            raise RuntimeError("WEBCHAT_KNOWLEDGE_REPLY_MODE must be ai_grounded or deterministic_direct_answer")
        if self.knowledge_runtime_version not in {"v2", "legacy"}:
            raise RuntimeError("KNOWLEDGE_RUNTIME_VERSION must be v2 or legacy")
        if self.knowledge_embedding_dim < 8 or self.knowledge_embedding_dim > 4096:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_DIM must be between 8 and 4096")
        if self.knowledge_embedding_batch_size < 1 or self.knowledge_embedding_batch_size > 512:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_BATCH_SIZE must be between 1 and 512")
        if self.knowledge_embedding_timeout_seconds < 1 or self.knowledge_embedding_timeout_seconds > 120:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_TIMEOUT_SECONDS must be between 1 and 120")
        if self.knowledge_embedding_provider not in {"deterministic_hash", "hash", "test", "openai_compatible"}:
            raise RuntimeError("KNOWLEDGE_EMBEDDING_PROVIDER must be deterministic_hash, hash, test, or openai_compatible")
        if self.webchat_tracking_fact_source not in {"speedaf_api", "speedaf_track_query", "speedaf_hybrid"}:
            raise RuntimeError("WEBCHAT_TRACKING_FACT_SOURCE must be speedaf_api, speedaf_track_query, or speedaf_hybrid")
        if self.webchat_tracking_fact_timeout_seconds < 1 or self.webchat_tracking_fact_timeout_seconds > 30:
            raise RuntimeError("WEBCHAT_TRACKING_FACT_TIMEOUT_SECONDS must be between 1 and 30")
        if self.webchat_ai_session_ttl_hours < 1 or self.webchat_ai_session_ttl_hours > 168:
            raise RuntimeError("WEBCHAT_AI_SESSION_TTL_HOURS must be between 1 and 168")
        if self.webchat_ai_session_max_messages < 4 or self.webchat_ai_session_max_messages > 200:
            raise RuntimeError("WEBCHAT_AI_SESSION_MAX_MESSAGES must be between 4 and 200")
        if self.webchat_ai_session_summary_messages < 1 or self.webchat_ai_session_summary_messages > 20:
            raise RuntimeError("WEBCHAT_AI_SESSION_SUMMARY_MESSAGES must be between 1 and 20")
        if self.webchat_ws_broker not in {"database", "memory"}:
            raise RuntimeError("WEBCHAT_WS_BROKER must be database or memory")
        if self.webchat_ws_replay_poll_ms < 100 or self.webchat_ws_replay_poll_ms > 10000:
            raise RuntimeError("WEBCHAT_WS_REPLAY_POLL_MS must be between 100 and 10000")
        if self.webchat_ws_fallback_poll_ms < 1000 or self.webchat_ws_fallback_poll_ms > 60000:
            raise RuntimeError("WEBCHAT_WS_FALLBACK_POLL_MS must be between 1000 and 60000")
        if self.webchat_ws_heartbeat_ms < 5000 or self.webchat_ws_heartbeat_ms > 120000:
            raise RuntimeError("WEBCHAT_WS_HEARTBEAT_MS must be between 5000 and 120000")
        if self.webchat_ws_hello_timeout_ms < 1000 or self.webchat_ws_hello_timeout_ms > 30000:
            raise RuntimeError("WEBCHAT_WS_HELLO_TIMEOUT_MS must be between 1000 and 30000")
        if self.webchat_ws_max_connections < 1 or self.webchat_ws_max_connections > 100000:
            raise RuntimeError("WEBCHAT_WS_MAX_CONNECTIONS must be between 1 and 100000")
        if self.webchat_ws_max_connections_per_user < 1 or self.webchat_ws_max_connections_per_user > 1000:
            raise RuntimeError("WEBCHAT_WS_MAX_CONNECTIONS_PER_USER must be between 1 and 1000")
        if self.outbound_email_test_send_max_age_hours < 1 or self.outbound_email_test_send_max_age_hours > 168:
            raise RuntimeError("OUTBOUND_EMAIL_TEST_SEND_MAX_AGE_HOURS must be between 1 and 168")
        if self.whatsapp_dispatch_mode not in {"disabled", "native_sidecar", "cloud_api_future"}:
            raise RuntimeError("WHATSAPP_DISPATCH_MODE must be disabled, native_sidecar, or cloud_api_future")
        if self.whatsapp_sidecar_timeout_seconds < 1 or self.whatsapp_sidecar_timeout_seconds > 60:
            raise RuntimeError("WHATSAPP_SIDECAR_TIMEOUT_SECONDS must be between 1 and 60")
        if self.whatsapp_connector_timestamp_tolerance_seconds < 30 or self.whatsapp_connector_timestamp_tolerance_seconds > 3600:
            raise RuntimeError("WHATSAPP_CONNECTOR_TIMESTAMP_TOLERANCE_SECONDS must be between 30 and 3600")
        if self.email_mailbox_sync_interval_seconds < 5 or self.email_mailbox_sync_interval_seconds > 3600:
            raise RuntimeError("EMAIL_MAILBOX_SYNC_INTERVAL_SECONDS must be between 5 and 3600")
        if self.email_mailbox_sync_batch_size < 1 or self.email_mailbox_sync_batch_size > 100:
            raise RuntimeError("EMAIL_MAILBOX_SYNC_BATCH_SIZE must be between 1 and 100")
        if self.webchat_tracking_fact_lookup_enabled and not self.webchat_tracking_fact_redaction_enabled:
            raise RuntimeError("WEBCHAT_TRACKING_FACT_REDACTION_ENABLED must be true when tracking lookup is enabled")
        if self.nexus_osr_release_profile not in {"development", "shadow", "pilot", "full_osr"}:
            raise RuntimeError("NEXUS_OSR_RELEASE_PROFILE must be development, shadow, pilot, or full_osr")
        if not self.nexus_osr_required_workers:
            raise RuntimeError("NEXUS_OSR_REQUIRED_WORKERS must declare at least one worker")
        if self.nexus_osr_worker_stale_seconds < 10 or self.nexus_osr_worker_stale_seconds > 3600:
            raise RuntimeError("NEXUS_OSR_WORKER_STALE_SECONDS must be between 10 and 3600")
        if self.nexus_osr_queue_warn_age_seconds < 10 or self.nexus_osr_queue_fail_age_seconds < self.nexus_osr_queue_warn_age_seconds:
            raise RuntimeError("NEXUS_OSR queue age thresholds are invalid")
        if self.nexus_osr_dispatch_warn_age_seconds < 10 or self.nexus_osr_dispatch_fail_age_seconds < self.nexus_osr_dispatch_warn_age_seconds:
            raise RuntimeError("NEXUS_OSR dispatch age thresholds are invalid")
        if self.external_channel_transport != "disabled":
            raise RuntimeError("EXTERNAL_CHANNEL_TRANSPORT has been retired; set EXTERNAL_CHANNEL_TRANSPORT=disabled")
        if self.external_channel_deployment_mode != "disabled":
            raise RuntimeError("EXTERNAL_CHANNEL_DEPLOYMENT_MODE has been retired; set EXTERNAL_CHANNEL_DEPLOYMENT_MODE=disabled")
        if self.external_channel_cli_fallback_enabled:
            raise RuntimeError("EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED has been retired; set EXTERNAL_CHANNEL_CLI_FALLBACK_ENABLED=false")
        if self.external_channel_bridge_enabled:
            raise RuntimeError("EXTERNAL_CHANNEL_BRIDGE_ENABLED has been retired; set EXTERNAL_CHANNEL_BRIDGE_ENABLED=false")
        if self.external_channel_sync_enabled:
            raise RuntimeError("EXTERNAL_CHANNEL_SYNC_ENABLED has been retired; set EXTERNAL_CHANNEL_SYNC_ENABLED=false")
        if self.external_channel_inbound_auto_sync_enabled:
            raise RuntimeError("EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED has been retired; set EXTERNAL_CHANNEL_INBOUND_AUTO_SYNC_ENABLED=false")
        if self.external_channel_event_driver_enabled:
            raise RuntimeError("EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED has been retired; set EXTERNAL_CHANNEL_EVENT_DRIVER_ENABLED=false")
        if self.app_env == "production":
            if not self.expected_migration_head:
                raise RuntimeError("EXPECTED_MIGRATION_HEAD must be set in production")
            if self.nexus_osr_release_profile == "development":
                raise RuntimeError("NEXUS_OSR_RELEASE_PROFILE=development is not allowed in production")
            if self.nexus_osr_release_profile in {"shadow", "pilot", "full_osr"} and not self.osr_escalation_orchestration_enabled:
                raise RuntimeError("OSR_ESCALATION_ORCHESTRATION_ENABLED=true is required for governed production profiles")
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
            if self.allow_dev_auth_requested:
                raise RuntimeError("ALLOW_DEV_AUTH must be disabled in production")
            if self.allow_legacy_integration_api_key:
                raise RuntimeError("ALLOW_LEGACY_INTEGRATION_API_KEY must be disabled in production")
            localhost_origins = {"http://localhost", "http://127.0.0.1"}
            if set(self.allowed_origins) & localhost_origins:
                raise RuntimeError("Production ALLOWED_ORIGINS must not include localhost defaults")
            if self.storage_backend not in {"local", "s3"}:
                raise RuntimeError("STORAGE_BACKEND must be local or s3")
            if self.external_channel_session_dm_scope not in {"per-account-channel-peer", "per-channel-peer", "per-peer"}:
                raise RuntimeError("EXTERNAL_CHANNEL_SESSION_DM_SCOPE must be a supported session dm scope")
            if self.metrics_enabled and not self.metrics_token:
                raise RuntimeError("METRICS_TOKEN must be set in production when METRICS_ENABLED=true")
            if self.webchat_allow_legacy_token_transport:
                raise RuntimeError("WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in production")
            if self.webchat_ws_enabled and self.webchat_ws_broker == "memory":
                raise RuntimeError("WEBCHAT_WS_BROKER=memory is not allowed in production when WEBCHAT_WS_ENABLED=true")
            if self.external_channel_attachment_url_fetch_enabled and not self.external_channel_attachment_allowed_hosts:
                raise RuntimeError("EXTERNAL_CHANNEL_ATTACHMENT_ALLOWED_HOSTS must be set when EXTERNAL_CHANNEL_ATTACHMENT_URL_FETCH_ENABLED=true in production")
            if self.whatsapp_dispatch_mode == "native_sidecar":
                if not self.whatsapp_native_enabled:
                    raise RuntimeError("WHATSAPP_NATIVE_ENABLED=true is required when WHATSAPP_DISPATCH_MODE=native_sidecar")
                if not self.whatsapp_sidecar_url.startswith(("http://", "https://")):
                    raise RuntimeError("WHATSAPP_SIDECAR_URL must be an http(s) URL")
                if not self.whatsapp_sidecar_token:
                    raise RuntimeError("WHATSAPP_SIDECAR_TOKEN is required when WHATSAPP_DISPATCH_MODE=native_sidecar")
                if not self.whatsapp_connector_key:
                    raise RuntimeError("WHATSAPP_CONNECTOR_KEY is required when WHATSAPP_DISPATCH_MODE=native_sidecar")
                if not self.whatsapp_connector_hmac_secret:
                    raise RuntimeError("WHATSAPP_CONNECTOR_HMAC_SECRET is required when WHATSAPP_DISPATCH_MODE=native_sidecar")
            if not self.frontend_dist_available:
                raise RuntimeError("frontend_dist/index.html must exist in production; refusing legacy frontend fallback")
            if self.knowledge_runtime_version == "v2":
                if not self.knowledge_embeddings_enabled:
                    raise RuntimeError("KNOWLEDGE_EMBEDDINGS_ENABLED=true is required in production for Knowledge Runtime v2")
                if self.knowledge_embedding_provider in {"deterministic_hash", "hash", "test"}:
                    raise RuntimeError("Production Knowledge Runtime v2 requires a real embedding provider")
                if self.knowledge_embedding_provider == "openai_compatible" and not (self.knowledge_embedding_api_key or self.knowledge_embedding_api_key_file):
                    raise RuntimeError("KNOWLEDGE_EMBEDDING_API_KEY_FILE or KNOWLEDGE_EMBEDDING_API_KEY is required for openai_compatible embeddings")
            if self.require_prometheus_client_in_production:
                try:
                    import prometheus_client  # noqa: F401
                except Exception as exc:
                    raise RuntimeError("prometheus_client must be installed in production when REQUIRE_PROMETHEUS_CLIENT_IN_PRODUCTION=true") from exc
        if not self.jwt_secret_key:
            self.jwt_secret_key = f"dev-only-{secrets.token_urlsafe(24)}"

    @staticmethod
    def _is_truthy(raw: str | None) -> bool:
        return (raw or "").strip().lower() in {"1", "true", "yes", "on"}

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
