from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.db import Base
from app.settings import get_settings
from app import models  # noqa: F401
from app import tool_models  # noqa: F401
from app import operator_models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
settings = get_settings()
config.set_main_option('sqlalchemy.url', settings.database_url)

IGNORED_INDEXES = {
    'ix_background_jobs_claim',
    'ix_background_jobs_job_type_status',
    'ix_integration_request_logs_client_created_at',
    'ix_integration_request_logs_window',
    'ix_market_bulletins_active_window',
    'ix_openclaw_attachment_refs_remote_attachment_id',
    'ix_openclaw_attachment_refs_storage_status',
    'ix_openclaw_attachment_refs_ticket_id',
    'ix_openclaw_links_session_updated',
    'ix_openclaw_links_ticket_updated',
    'ix_openclaw_transcript_ticket_received',
    'ix_ticket_outbound_messages_claim',
    'ix_ticket_outbound_messages_status_next_retry',
    'ix_tickets_market_country_status',
    'ix_tickets_status_updated_at',
    'ix_tickets_team_status',
    'ix_user_capability_overrides_lookup',
    'ix_user_capability_overrides_user_capability',
}

IGNORED_UNIQUE_CONSTRAINTS = {
    'uq_auth_throttle_entries_throttle_key',
    'uq_integration_clients_key_id',
    'uq_integration_clients_name',
    'uq_markets_code',
    'uq_markets_name',
    'uq_sla_policies_name',
    'uq_sla_policies_priority',
    'uq_tags_name',
    'uq_teams_name',
    'uq_tickets_ticket_no',
    'uq_users_username',
    'uq_users_email',
}


def _compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    if context.dialect.name == 'sqlite' and inspected_type.__class__.__name__.upper().startswith('VARCHAR') and metadata_type.__class__.__name__ == 'Enum':
        return False
    return None


def _include_object(object_, name, type_, reflected, compare_to):
    dialect_name = context.get_context().dialect.name
    if type_ == 'index' and name in IGNORED_INDEXES:
        return False
    if type_ == 'unique_constraint' and name in IGNORED_UNIQUE_CONSTRAINTS:
        return False
    if dialect_name == 'sqlite' and type_ == 'foreign_key_constraint':
        return False
    return True



def run_migrations_offline() -> None:
    url = config.get_main_option('sqlalchemy.url')
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=_compare_type, include_object=_include_object)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix='sqlalchemy.',
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=_compare_type, include_object=_include_object)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
