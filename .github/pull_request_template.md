# Pull Request

## Summary

Describe what this PR changes.

## Scope

- [ ] Backend runtime
- [ ] Frontend runtime
- [ ] WebChat widget
- [ ] OpenClaw / Bridge
- [ ] Database migration
- [ ] Deployment / Docker / Nginx
- [ ] GitHub Actions / CI
- [ ] Security / permissions
- [ ] Documentation only
- [ ] Governance only

## Risk Level

- [ ] P0 - production blocking / security critical
- [ ] P1 - high risk / production relevant
- [ ] P2 - medium risk
- [ ] P3 - low risk / docs only

## Changed Modules

List the main changed modules and file paths.

## Database Migration

- [ ] No database migration
- [ ] Migration added
- [ ] Migration modified
- [ ] Migration required before deploy

Details:

- Alembic revision:
- Backward compatibility:
- Rollback impact:

## Deploy Impact

- [ ] No deploy impact
- [ ] App rebuild required
- [ ] Worker restart required
- [ ] Migration required
- [ ] Environment variable change required
- [ ] Docker / Compose / Nginx change
- [ ] Production smoke required

## Security Impact

- [ ] No security impact
- [ ] Auth / permission changed
- [ ] Secret / token handling changed
- [ ] PII handling changed
- [ ] Rate limit / abuse control changed
- [ ] External provider / webhook changed

## OpenClaw / Bridge Impact

- [ ] No OpenClaw / Bridge impact
- [ ] OpenClaw inbound sync changed
- [ ] Bridge ai-reply changed
- [ ] Bridge send-message / write path changed
- [ ] MCP / tool contract changed
- [ ] Gateway / session routing changed

## WebChat Impact

- [ ] No WebChat impact
- [ ] Public widget changed
- [ ] WebChat admin changed
- [ ] Visitor token/session changed
- [ ] AI auto-reply changed
- [ ] Tracking fact / tool fact changed

## AI Runtime Impact

- [ ] No AI runtime impact
- [ ] Prompt changed
- [ ] Tool calling changed
- [ ] AI provider changed
- [ ] Fact gate changed
- [ ] Fallback behavior changed
- [ ] Safety / review gate changed

## Tests Run

List the exact commands that were run. If not run, write `Not run` and explain why.

```bash
# backend
cd backend
python3 -m compileall app scripts
pytest -q

# frontend
cd webapp
npm run typecheck
npm run build
npm run lint
```

## CI Status

List the GitHub Actions result for the latest PR head:

- backend-ci:
- webapp-build / frontend-ci:
- postgres-migration:
- round-a-smoke:
- integration-contracts:
- production-readiness:

## Rollback Plan

Explain how to revert this PR safely.

## Production Smoke Required

- [ ] No
- [ ] Yes

If yes, list the required smoke scenarios.

## Screenshots / Evidence

Frontend or UX-related PRs must include screenshots. If there is no visual change, state that explicitly.

## Checklist

- [ ] I branched from latest main.
- [ ] I did not commit real secrets.
- [ ] I did not modify deploy/.env.prod.
- [ ] I did not modify production runtime data.
- [ ] I checked database migration impact.
- [ ] I checked deploy impact.
- [ ] I checked WebChat impact.
- [ ] I checked OpenClaw / Bridge impact.
- [ ] I checked AI runtime impact.
- [ ] I added or updated tests when needed.
- [ ] I included rollback instructions.
- [ ] I confirmed this PR does not bypass safety gates.
