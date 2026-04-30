from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..settings import get_settings


def _localhost_origin_present(origins: list[str]) -> bool:
    localhost_origins = {'http://localhost', 'http://127.0.0.1'}
    return bool(set(origins or []) & localhost_origins)


def evaluate_production_readiness(db: Session | None = None) -> dict[str, Any]:
    """Single source of truth for production readiness checks.

    This service is intentionally deterministic and side-effect free except for an
    optional SELECT 1 when a DB session is provided. It is shared by CLI and admin
    API to prevent readiness/signoff drift.
    """
    settings = get_settings()
    warnings: list[str] = []
    failures: list[str] = []

    checks: dict[str, bool] = {
        'postgres_configured': settings.is_postgres,
        'database_connected': True if db is None else False,
        'secret_key_configured': bool(settings.jwt_secret_key),
        'allowed_origins_configured': bool(settings.allowed_origins),
        'allowed_origins_not_localhost': not _localhost_origin_present(settings.allowed_origins),
        'webchat_origins_configured': bool(settings.webchat_allowed_origins),
        'legacy_webchat_token_disabled': not settings.webchat_allow_legacy_token_transport,
        'dev_auth_disabled': not settings.allow_dev_auth,
        'legacy_integration_key_disabled': not settings.allow_legacy_integration_api_key,
        'cli_fallback_disabled': not settings.openclaw_cli_fallback_enabled,
        'storage_ready': settings.storage_backend in {'local', 's3'},
        'metrics_config_valid': (not settings.metrics_enabled) or bool(settings.metrics_token),
        'openclaw_mode_valid': settings.openclaw_deployment_mode in {'local_gateway', 'remote_gateway', 'disabled'},
        'openclaw_transport_mcp': settings.openclaw_transport == 'mcp',
        'webchat_rate_limit_database_in_production': settings.webchat_rate_limit_backend == 'database' or settings.app_env != 'production',
        'webchat_ai_mode_safe_in_production': settings.webchat_ai_auto_reply_mode in {'off', 'safe_ack'} or settings.app_env != 'production',
    }

    if db is not None:
        try:
            db.execute(text('SELECT 1'))
            checks['database_connected'] = True
        except Exception:
            checks['database_connected'] = False

    if settings.app_env == 'production':
        if not checks['postgres_configured']:
            failures.append('Production DATABASE_URL must be PostgreSQL')
        if not checks['secret_key_configured']:
            failures.append('Production SECRET_KEY must be configured')
        if not checks['allowed_origins_configured']:
            failures.append('Production ALLOWED_ORIGINS must be configured')
        if not checks['allowed_origins_not_localhost']:
            failures.append('Production ALLOWED_ORIGINS must not include localhost')
        if not checks['webchat_origins_configured']:
            failures.append('Production WEBCHAT_ALLOWED_ORIGINS must be configured')
        if not checks['legacy_webchat_token_disabled']:
            failures.append('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT must be false in production')
        if not checks['dev_auth_disabled']:
            failures.append('ALLOW_DEV_AUTH must be disabled in production')
        if not checks['legacy_integration_key_disabled']:
            failures.append('ALLOW_LEGACY_INTEGRATION_API_KEY must be disabled in production')
        if not checks['cli_fallback_disabled']:
            failures.append('OPENCLAW_CLI_FALLBACK_ENABLED must be false in production')
        if not checks['metrics_config_valid']:
            failures.append('METRICS_TOKEN must be set when METRICS_ENABLED=true')
        if not checks['webchat_rate_limit_database_in_production']:
            failures.append('WEBCHAT_RATE_LIMIT_BACKEND should be database in production')
        if not checks['webchat_ai_mode_safe_in_production']:
            failures.append('WEBCHAT_AI_AUTO_REPLY_MODE should be off or safe_ack in production')

        if settings.storage_backend == 'local':
            warnings.append('STORAGE_BACKEND=local is acceptable only for controlled pilots with backup discipline')
        if settings.storage_backend == 's3':
            for attr, env_name in [
                ('s3_bucket', 'S3_BUCKET'),
                ('s3_region', 'S3_REGION'),
                ('s3_access_key', 'S3_ACCESS_KEY'),
                ('s3_secret_key', 'S3_SECRET_KEY'),
            ]:
                if not getattr(settings, attr):
                    failures.append(f'{env_name} must be set when STORAGE_BACKEND=s3')
        if settings.openclaw_deployment_mode == 'remote_gateway':
            if not settings.openclaw_mcp_url:
                failures.append('OPENCLAW_MCP_URL must be set when OPENCLAW_DEPLOYMENT_MODE=remote_gateway')
            if not settings.openclaw_mcp_token_file and not settings.openclaw_mcp_password_file:
                failures.append('OPENCLAW_MCP_TOKEN_FILE or OPENCLAW_MCP_PASSWORD_FILE must be set for remote gateway')
    else:
        if not settings.is_postgres:
            warnings.append('DATABASE_URL is not PostgreSQL')
        if settings.storage_backend == 'local':
            warnings.append('STORAGE_BACKEND is local')
        if not settings.webchat_allowed_origins:
            warnings.append('WEBCHAT_ALLOWED_ORIGINS is empty; production browser origins would be rejected')
        if settings.openclaw_transport != 'mcp':
            warnings.append('OPENCLAW_TRANSPORT is not mcp')
        if settings.webchat_allow_legacy_token_transport:
            warnings.append('WEBCHAT_ALLOW_LEGACY_TOKEN_TRANSPORT should remain false')

    if not checks['database_connected']:
        failures.append('Database connectivity check failed')

    return {
        'status': 'ready' if not failures and not warnings else 'not_ready',
        'checks': checks,
        'warnings': warnings,
        'failures': failures,
        'app_env': settings.app_env,
        'database_url_scheme': settings.database_url.split(':', 1)[0],
        'is_postgres': settings.is_postgres,
        'storage_backend': settings.storage_backend,
        'openclaw_transport': settings.openclaw_transport,
        'metrics_enabled': settings.metrics_enabled,
        'openclaw_sync_enabled': settings.openclaw_sync_enabled,
    }
