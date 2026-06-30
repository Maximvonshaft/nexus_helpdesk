# Local ExternalChannel readiness push

## What this push adds
- Supervisor-facing **ExternalChannel connectivity check** API and runtime page panel
- A direct CLI probe script: `backend/scripts/check_external_channel_connectivity.py`
- A local-first deployment env template: `backend/.env.local-external_channel.example`
- A local docker compose topology for PostgreSQL + app + worker + ExternalChannel daemons: `deploy/docker-compose.local-external_channel.yml`
- A bootstrap script for local bring-up: `scripts/deploy/bootstrap_local_external_channel.sh`
- Local-external_channel smoke verification: `backend/scripts/smoke_verify_local_external_channel_ready.py`

## Why it matters
The current highest-value next step is not more UI polish but making local ExternalChannel bring-up and联调 simpler and less ambiguous. This push turns that into a first-class path in the source tree.

## What is now easier
- Running the helpdesk stack locally with PostgreSQL
- Pointing the app at a local paired ExternalChannel install
- Verifying MCP bridge startup before deeper same-route testing
- Letting supervisors inspect bridge reachability from the runtime screen without digging into logs first

## What still requires real ExternalChannel staging proof
- live event streaming over a real gateway
- attachment fetch with real channel media
- same-route reply success on real routed sessions
- 24h daemon heartbeat stability
