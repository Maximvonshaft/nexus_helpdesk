# Local OpenClaw readiness push

## What this push adds
- Supervisor-facing **OpenClaw connectivity check** API and runtime page panel
- A direct CLI probe script: `backend/scripts/check_openclaw_connectivity.py`
- A local-first deployment env template: `backend/.env.local-openclaw.example`
- A local docker compose topology for PostgreSQL + app + worker + OpenClaw daemons: `deploy/docker-compose.local-openclaw.yml`
- A bootstrap script for local bring-up: `scripts/deploy/bootstrap_local_openclaw.sh`
- Local-openclaw smoke verification: `backend/scripts/smoke_verify_local_openclaw_ready.py`

## Why it matters
The current highest-value next step is not more UI polish but making local OpenClaw bring-up and联调 simpler and less ambiguous. This push turns that into a first-class path in the source tree.

## What is now easier
- Running the helpdesk stack locally with PostgreSQL
- Pointing the app at a local paired OpenClaw install
- Verifying MCP bridge startup before deeper same-route testing
- Letting supervisors inspect bridge reachability from the runtime screen without digging into logs first

## What still requires real OpenClaw staging proof
- live event streaming over a real gateway
- attachment fetch with real channel media
- same-route reply success on real routed sessions
- 24h daemon heartbeat stability
