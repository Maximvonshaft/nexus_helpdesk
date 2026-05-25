# v1.2 Business Value Closure Changelog

## Why v1.2 exists

v1.1 solved critical backend guardrails but did not fully guarantee that the business value was closed from admin configuration to agent usage. In particular, the previous pack mentioned frontend/admin UI work but did not define enough concrete contracts for engineers to implement a usable backend configuration surface.

## v1.2 changes

1. Adds explicit admin configuration APIs for Email accounts.
2. Adds a concrete admin UI specification for backend configuration.
3. Adds a concrete agent reply composer specification for Email.
4. Adds separation of DevOps-only items from backend-admin-configurable items.
5. Adds test-send and health-check requirements.
6. Adds queue/event observability requirements in admin console.
7. Adds end-to-end business acceptance gates.
8. Updates Codex prompt to make frontend + backend closure mandatory.

## New merge rule

A backend-only PR is not enough for final acceptance. Email is not production-ready until both of the following are true:

- Admin can configure and validate Email channel accounts from the backend/admin UI.
- Agent can select Email on a ticket and send a customer reply through the same production queue path.
