# WebChat v1.7.8 Template Block Parity Evidence

## Scope

This PR closes the WebChat workbench slice from the v1.7.8 template audit:

- Queue list and conversation message stream stay on the existing WebChat V5 production inbox.
- Reply/internal-note composer is explicit. Customer replies continue through `api.webchatReply`; internal notes use the existing backend `POST /api/tickets/{ticket_id}/internal-notes` contract and `note.write.internal` permission.
- Customer Profile, AI Suggestions, Handoff, and Session Actions are visible top-level right-rail blocks.
- WebCall context remains embedded through `AgentWebCallPanel`.

## Real Data Sources

- Customer Profile: `api.webchatThread`, `api.caseDetail`, `WebchatConversation`, and handoff queue rows.
- AI Suggestions: `thread.required_action`, `caseDetail.required_action`, `caseDetail.ai_summary`, `caseDetail.ai_classification`, `caseDetail.evidence_summary`, `thread.ai_turns`, and outbound reply capability gates.
- Handoff: `api.webchatAcceptHandoff`, `api.webchatDeclineHandoff`, `api.webchatForceTakeover`, `api.webchatReleaseHandoff`, and `api.webchatResumeAi`.
- Session Actions: `api.webchatReadState`, `api.escalateTicket`, clipboard link copy, and local composer insertion only.
- Internal note save: `api.addTicketInternalNote` mapped to backend `tickets.py` internal notes route.

## Local Verification

- `node --test tests/webchat-inbox-v5-contract.test.mjs`: 8 passed.
- `npm run typecheck`: passed.
- `npm run lint`: passed with 5 existing warnings.
- `npm test`: 67 passed.
- `npm run build`: passed; existing `vendor-livekit` chunk warning remains.
- `git diff --check`: passed.
- `python -m py_compile backend\app\api\tickets.py backend\app\services\ticket_service.py backend\app\api\ticket_perf.py`: passed.
- `python -m pytest -q backend\tests\test_channel_workbench_backend_contracts.py backend\tests\test_ticket_lightweight_contract.py backend\tests\test_ticket_timeline_pagination.py -p no:cacheprovider --tb=short --basetemp=C:\tmp\nexus_pytest_webchat_v178`: 14 passed.
- In-app browser smoke: `http://127.0.0.1:5174/webchat` redirected to `/login` unauthenticated, no Vite error overlay and no severe console errors.
