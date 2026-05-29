# Today Workbench role-home real API evidence

## Template requirement

The uploaded v1.7.8 template keeps the business Workbench group as `今日工作台`, `WebChat`, `WebCall`, `Email` and defines the default screen as `今日工作台 / 我的优先事项`.

Its Role Home gap states that role task cards must move from fixture counts to real `/api/auth/me`, queue summary, and SLA-risk data.

## Main branch gap closed in this PR

Before this PR, the real main frontend loaded `/api/lite/cases` on the homepage and calculated several "today" counts in the browser. That made the homepage visually usable but not a closed backend contract for role-home data.

This PR adds `GET /api/workbench/today`:

- Requires the current authenticated user and effective `ticket.read` capability.
- Applies the same ticket visibility model as the ticket workbench: admin/manager/auditor see all; agent/lead see assigned tickets or their team.
- Aggregates real tickets, WebChat handoff requests, source channels, customer-waiting state, urgent priority, unassigned queue, and 30-minute SLA risk.
- Returns task cards, metrics, and SLA-risk ticket rows for the homepage.
- Keeps `/auth/me` as the identity/capability source and exposes the backend source contracts in the response for contract review.

## Verification hooks

- Backend: `backend/tests/test_today_workbench_api.py`
- Frontend contract: `webapp/tests/operator-console-contract.test.mjs`
- Runtime route: `backend/app/api/today_workbench.py`
- Aggregation service: `backend/app/services/today_workbench_service.py`
- Frontend API client: `webapp/src/lib/api.ts`
- Homepage: `webapp/src/routes/index.tsx`
