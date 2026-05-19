#!/usr/bin/env bash
set -Eeuo pipefail
exec /opt/nexus_helpdesk/deploy/nexus-compose-prod "$@"
