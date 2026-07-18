#!/usr/bin/env bash
set -Eeuo pipefail

required=(
  POSTGRES_DB
  POSTGRES_USER
  NEXUS_DB_MIGRATION_USER
  NEXUS_DB_MIGRATION_PASSWORD
  NEXUS_DB_APP_USER
  NEXUS_DB_APP_PASSWORD
  NEXUS_DB_OUTBOUND_USER
  NEXUS_DB_OUTBOUND_PASSWORD
  NEXUS_DB_BACKGROUND_USER
  NEXUS_DB_BACKGROUND_PASSWORD
  NEXUS_DB_WEBCHAT_AI_USER
  NEXUS_DB_WEBCHAT_AI_PASSWORD
  NEXUS_DB_HANDOFF_USER
  NEXUS_DB_HANDOFF_PASSWORD
)

for name in "${required[@]}"; do
  value="${!name:-}"
  if [[ -z "$value" || "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "controlled_postgres_bootstrap_invalid:${name}" >&2
    exit 2
  fi
done

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set ON_ERROR_STOP=1 \
  --set migration_user="$NEXUS_DB_MIGRATION_USER" \
  --set migration_password="$NEXUS_DB_MIGRATION_PASSWORD" \
  --set app_user="$NEXUS_DB_APP_USER" \
  --set app_password="$NEXUS_DB_APP_PASSWORD" \
  --set outbound_user="$NEXUS_DB_OUTBOUND_USER" \
  --set outbound_password="$NEXUS_DB_OUTBOUND_PASSWORD" \
  --set background_user="$NEXUS_DB_BACKGROUND_USER" \
  --set background_password="$NEXUS_DB_BACKGROUND_PASSWORD" \
  --set webchat_ai_user="$NEXUS_DB_WEBCHAT_AI_USER" \
  --set webchat_ai_password="$NEXUS_DB_WEBCHAT_AI_PASSWORD" \
  --set handoff_user="$NEXUS_DB_HANDOFF_USER" \
  --set handoff_password="$NEXUS_DB_HANDOFF_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'migration_user', :'migration_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'migration_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'app_user', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'outbound_user', :'outbound_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'outbound_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'background_user', :'background_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'background_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'webchat_ai_user', :'webchat_ai_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'webchat_ai_user')
\gexec
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'handoff_user', :'handoff_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'handoff_user')
\gexec

SELECT format('GRANT ALL PRIVILEGES ON DATABASE %I TO %I', current_database(), :'migration_user')
\gexec
SELECT format('ALTER SCHEMA public OWNER TO %I', :'migration_user')
\gexec

SELECT format('GRANT CONNECT ON DATABASE %I TO %I', current_database(), runtime_user)
FROM (VALUES
  (:'app_user'),
  (:'outbound_user'),
  (:'background_user'),
  (:'webchat_ai_user'),
  (:'handoff_user')
) AS runtime_roles(runtime_user)
\gexec

SELECT format('GRANT USAGE ON SCHEMA public TO %I', runtime_user)
FROM (VALUES
  (:'app_user'),
  (:'outbound_user'),
  (:'background_user'),
  (:'webchat_ai_user'),
  (:'handoff_user')
) AS runtime_roles(runtime_user)
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
  :'migration_user',
  runtime_user
)
FROM (VALUES
  (:'app_user'),
  (:'outbound_user'),
  (:'background_user'),
  (:'webchat_ai_user'),
  (:'handoff_user')
) AS runtime_roles(runtime_user)
\gexec

SELECT format(
  'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO %I',
  :'migration_user',
  runtime_user
)
FROM (VALUES
  (:'app_user'),
  (:'outbound_user'),
  (:'background_user'),
  (:'webchat_ai_user'),
  (:'handoff_user')
) AS runtime_roles(runtime_user)
\gexec
SQL

printf 'controlled_postgres_roles_initialized=true\n'
