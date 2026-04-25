# NexusDesk Round B Webchat Closure

This branch is prepared for the Round B Webchat Widget / Ticket Intake / Human Reply / Safety Gate closure work.

Base commit: `c1672d5aa183320574dcef1c75efdf25cc5776f3`

## Current mobile-safe status

A GitHub branch has been created so the work can be continued without needing a desktop computer:

- Branch: `round-b-webchat-closure`
- Protected production files must not be overwritten:
  - `deploy/.env.prod`
  - `deploy/docker-compose.server.yml`
  - `data/`
  - server-local `Dockerfile` differences

## Round B target closure

The intended closure is:

1. `/webchat/widget.js` embedded into a customer website.
2. Visitor sends a message.
3. Backend creates/restores a Webchat conversation.
4. Backend creates/links a NexusDesk ticket.
5. Admin `/webchat` page shows Webchat conversations and messages.
6. Agent replies from admin UI.
7. Backend runs `backend/app/services/outbound_safety.py` before writing/sending the reply.
8. Visitor polling sees the agent reply.

## Required files from the generated patch ZIP

The generated ZIP is named:

`nexusdesk-round-b-webchat-closure.zip`

It contains an overlay directory with the Round B files:

```text
backend/app/api/webchat.py
backend/app/services/webchat_service.py
backend/app/webchat_models.py
backend/app/static/webchat/widget.js
backend/app/static/webchat/demo.html
backend/alembic/versions/20260425_round_b_webchat.py
backend/tests/test_webchat_round_b.py
webapp/src/routes/webchat.tsx
webapp/src/lib/webchatTypes.ts
scripts/smoke/smoke_webchat_round_b.sh
docs/webchat-widget.md
docs/round-b-delivery-report.md
docs/round-b-operator-demo-script.md
docs/round-b-readonly-audit.md
docs/round-b-implementation-plan.md
docs/round-b-self-audit.md
```

It also modifies:

```text
backend/app/main.py
webapp/src/router.tsx
webapp/src/layouts/AppShell.tsx
webapp/src/lib/api.ts
```

## Validation commands

Run after applying the patch:

```bash
python -m compileall backend/app backend/scripts
cd backend && alembic upgrade head
cd backend && pytest -q tests/test_outbound_safety.py tests/test_webchat_round_b.py
cd ../webapp && npm ci && npm run typecheck && npm run build
cd .. && bash -n scripts/smoke/*.sh
BASE_URL=http://127.0.0.1:18081 NEXUSDESK_DEV_USER_ID=1 bash scripts/smoke/smoke_webchat_round_b.sh
curl -fsS http://127.0.0.1:18081/healthz
curl -fsS http://127.0.0.1:18081/readyz
```

## Notes

Do not merge this branch until the actual source files are present and the above validation is green.
