# 01 — Current State Audit

## Scope

This audit covers the current `main` branch frontend and adjacent WebChat assets. It is a planning artifact only and does not change runtime behavior.

## Repository and branch baseline

- Repository: `Maximvonshaft/nexus_helpdesk`
- Baseline commit used for this package: `d2cbc2012164f895d78f407381f5a103237e42b5`
- Planning branch: `planning/frontend-agentic-runtime-readiness`

## Current frontend stack

The main admin console is implemented under `webapp/`.

Observed stack from `webapp/package.json`:

- React `^19.2.0`
- React DOM `^19.2.0`
- Vite `^7.1.0`
- TypeScript `^5.8.3`
- TanStack Router `^1.130.0`
- TanStack Query `^5.90.0`
- Tailwind CSS `^4.1.0`
- ESLint 9 / TypeScript ESLint 8

## Current application entry

`webapp/src/main.tsx` mounts the app through:

- `ReactDOM.createRoot`
- `QueryClientProvider`
- `RouterProvider`
- global stylesheet import from `@/styles.css`

## Current build model

`webapp/vite.config.ts` uses:

- `@vitejs/plugin-react`
- alias `@ -> ./src`
- build output directory `../frontend_dist`
- dev server host `0.0.0.0`, port `5173`

This means the console is currently a Vite-built React SPA that is emitted into a backend-served static directory.

## Current route surface

`webapp/src/router.tsx` currently registers:

- `/login`
- `/`
- `/workspace`
- `/webchat`
- `/bulletins`
- `/ai-control`
- `/control-plane`
- `/accounts`
- `/users`
- `/runtime`

Router configuration uses:

- `defaultPreload: 'intent'`
- `scrollRestoration: true`

## Current shell model

`webapp/src/layouts/AppShell.tsx` currently owns:

- sidebar navigation
- authenticated session guard behavior
- runtime health badge for permitted users
- command palette toggle through `Cmd/Ctrl + K`
- auto-refresh control
- logout action
- role-based navigation filtering

Assessment:

- Good baseline for an enterprise console shell.
- The shell is already product-shaped but should be split into dedicated shell subcomponents before more global features are added.

## Current Workspace page

`webapp/src/routes/workspace.tsx` currently owns many responsibilities in one route module:

- queue filters
- case search
- status filter
- market filter
- ticket list
- selected ticket state
- dirty form protection
- ticket detail fetching
- workflow update mutation
- AI intake mutation
- customer context
- customer messages
- active bulletins
- attachments and OpenClaw attachment references
- internal notes
- auto-refresh and countdown sync

Assessment:

- The page is already operationally useful.
- It is now a high-risk growth point because route-level code mixes feature orchestration, forms, business UI, data loading, mutation logic, and rendering.
- This page should become the first major cockpit refactor after frontend foundation work.

## Current WebChat admin page

`webapp/src/routes/webchat.tsx` currently owns:

- WebChat conversation list
- selected ticket state
- thread fetching
- manual reply form
- safety-gate-related reply flags
- embed snippet display
- polling refresh intervals

Current refresh model:

- conversations polling interval: 10 seconds
- thread polling interval: 5 seconds

Assessment:

- Functional for Round B.
- Should evolve into a WebChat Control Center.
- Polling should eventually be replaced or supplemented by SSE with fallback polling.

## Current AI Control page

`webapp/src/routes/ai-control.tsx` currently owns:

- AI config resource list
- config types: `persona`, `knowledge`, `sop`, `policy`
- scope types: `global`, `market`, `team`, `channel`, `case_type`
- draft JSON editing
- create/update
- publish
- rollback
- published preview
- version history

Assessment:

- Strong early AI governance foundation.
- Still too engineering-centric because draft editing is JSON textarea based.
- Should evolve into AI Governance Studio with business form mode, schema validation, diff, sandbox test, and guardrail preview.

## Current API client

`webapp/src/lib/api.ts` currently owns:

- API base URL normalization through `VITE_API_BASE_URL`
- token storage key `helpdesk-webapp-token`
- token get/set/clear helpers
- public path detection
- request wrapper
- 401 handling and `AuthExpiredError`
- all frontend API methods

Assessment:

- Good centralized baseline.
- As the application grows, this should be split into typed feature clients and shared request infrastructure.
- Public WebChat visitor API and authenticated admin API should remain separated.

## Current style model

`webapp/src/styles.css` currently contains:

- Tailwind import
- design tokens through CSS variables
- global layout styles
- sidebar / shell styles
- card / metric / table / form styles
- queue card styles
- message timeline styles
- command palette styles
- toast styles
- skeleton styles
- responsive breakpoints

Assessment:

- The CSS already carries a coherent visual language.
- It is too centralized for long-term product scaling.
- It should be split into tokens, base, layout, utilities, and component-specific classes or component-owned styles.

## Current embedded WebChat widget

`backend/app/static/webchat/widget.js` is a browser-executed IIFE that:

- reads current script attributes
- builds API base from script origin or `data-api-base`
- supports tenant/channel/title/subtitle/assistant/localized welcome attributes
- persists conversation id and visitor token in localStorage
- injects global CSS into document head
- creates button and chat panel DOM nodes
- initializes conversation through `/api/webchat/init`
- sends messages through public WebChat API
- polls messages every 4 seconds while open

Assessment:

- Works as a first production-shaped widget.
- Not yet a formal SDK.
- It injects global document-level CSS rather than Shadow DOM isolation.
- It should be converted to TypeScript + Shadow DOM + library build while keeping the one-line script contract.

## Current WebChat documentation

`docs/webchat-widget.md` states Round B limitations:

- polling is used instead of WebSocket/SSE
- tenant origin allowlist is not yet persisted
- replies are delivered into the widget itself
- no external WhatsApp/Email/OpenClaw dispatch in Round B
- next phase should add configured widget channels, origin allowlists, OpenClaw suggested replies, and real-time push

Assessment:

- The documented next phase aligns with this upgrade package.

## Current production/network assumptions

From the deployment memo and current operational context:

- Cloud Nexus Helpdesk backend runs behind Nginx and app container.
- Local OpenClaw / local control plane can connect to cloud backend through Tailscale.
- Frontend API base is environment-configured through `VITE_API_BASE_URL`.
- Bridge/MCP direction uses `NEXUSDESK_API_URL` as the primary variable.

Assessment:

- Current deployment strategy is acceptable for this stage.
- Frontend upgrade must not break this local-control-plane-to-cloud-backend architecture.

## Key risks

1. Route modules are too large and carry too many responsibilities.
2. Global CSS will become harder to govern as UI complexity grows.
3. WebChat widget lacks Shadow DOM isolation and formal SDK packaging.
4. Polling model will become inefficient and less polished for realtime customer support.
5. AI Control is powerful but not yet business-user-friendly.
6. API client is centralized but not yet domain-sliced.
7. There is no visible frontend execution-readiness gate in the repository yet.

## Recommended next step

Do not implement features yet. Approve the target architecture RFC, product requirements, UX blueprint, WebChat runtime blueprint, AI governance blueprint, security threat model, test strategy, and execution epics first.
