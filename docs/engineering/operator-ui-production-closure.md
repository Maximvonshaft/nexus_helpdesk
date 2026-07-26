# Operator UI Production Closure

## Authority

This delivery extends the existing canonical frontend only:

- application: `webapp/`;
- shell: `webapp/src/app/AppShell.tsx`;
- responsive layout: `webapp/src/app/OperatorLayoutProvider.tsx` and `webapp/src/app/useOperatorLayoutMode.tsx`;
- routes: `webapp/src/routes/`;
- navigation and route titles: `webapp/src/app/navigation.ts`;
- visual authority: MUI and `webapp/src/theme/nexusTheme.ts`;
- operator state presentation: `webapp/src/app/OperatorPresentation.tsx` and `webapp/src/lib/operatorWorkspacePresentation.ts`;
- HTTP transport: `webapp/src/lib/apiClient.ts`;
- business closure truth: the server-issued ticket closure receipt;
- browser acceptance: the existing Playwright configuration and canonical `webapp/e2e/` suite.

No second frontend, shell, design system, responsive state source, route registry, HTTP transport, case truth, closure truth, telemetry client, voice product, screenshot baseline, image-signature transport or parallel browser product is permitted.

## Production contracts

1. Customer, operator and evidence-authored free text is displayed verbatim. Only exact repository-owned enum values may be translated.
2. Source `closed`, a completed Job, a sent message or a dispatched request never becomes safe closure through frontend inference.
3. Case Spine closure, outcome, notification, repair and observation states consume the server closure receipt.
4. Destructive or capacity-releasing actions require explicit user intent, a review step and a server-side recheck where applicable.
5. Dynamic announcements are bounded. Timelines are not live regions; only concise status messages are announced.
6. Every authenticated route title, timezone, focus treatment and responsive presentation is owned by the single AppShell/navigation/layout authority.
7. Slow, unavailable and stale dependencies preserve the last server-confirmed information and identify it as degraded.
8. WebSocket delivery is the primary operator event path where supported; bounded HTTP polling is a fail-safe fallback, not a parallel truth.
9. Heavy route-specific runtimes such as LiveKit remain dynamically reachable through the canonical route graph and outside the initial static bundle closure.
10. Primary operator surfaces present business language. Raw scenario, policy and closure codes remain available only in named technical disclosures.
11. Bundle, browser, semantic accessibility, text contrast, target size, representative volume and Web Vitals evidence is part of the canonical acceptance gate.

## Responsive and accessibility floor

- The shell and Workspace consume one layout state derived from viewport capacity and effective root text scale.
- The release floor includes 320 CSS-pixel reflow and 200% text enlargement.
- Enlarged text must not produce label/value overlap, clipped identity text, hidden current actions or root horizontal overflow.
- Keyboard focus remains visible in normal and Windows Forced Colors presentation.
- Required interactive targets remain at least 44 CSS pixels.
- Normal and large text meet the WCAG AA contrast floor.
- Reduced motion preserves all content and task behavior.

## Playwright acceptance authority

The canonical Playwright suite is the sole browser acceptance authority. It validates rendered behavior and geometry directly rather than transporting or committing screenshots.

Release-blocking browser contracts cover:

- authenticated route readiness and one-main-landmark structure;
- 320 CSS-pixel reflow and 200% text enlargement;
- label/value overlap, clipped identity text and root horizontal overflow;
- keyboard reachability, visible focus, Forced Colors and reduced motion;
- accessible names, duplicate IDs, nested controls and positive `tabindex`;
- WCAG AA text contrast and 44 CSS-pixel target size;
- loading, empty, degraded, conflict and repair-required state behavior;
- last-server-confirmed data preservation;
- 500-row cursor pagination and task operability;
- dynamic LiveKit route isolation and the real initial bundle closure.

A browser check may be changed only with the corresponding product or code change. Disabling an assertion, replacing it with a screenshot artifact or adding a second browser workflow is not an accepted fix.

## Browser support

The authenticated operator product is certified against the Playwright-pinned Chromium engine and current enterprise Chrome/Edge releases. Other engines are not represented as certified without exact browser evidence. The public WebChat widget has a separate browser contract.

## Release condition

Production UI acceptance requires one unchanged exact Head with:

- architecture, lint, TypeScript, unit contracts and production build;
- manifest-derived bundle budget and dynamic-route isolation;
- Playwright journeys at representative desktop, tablet, mobile and 320-pixel reflow sizes;
- semantic landmarks, accessible names, natural focus order and keyboard journeys;
- text contrast, target size, Forced Colors, reduced motion and 200% text evidence;
- loading, empty, degraded, conflict and repair-required behavior;
- representative-volume queue and timeline evidence;
- backend and PostgreSQL regression;
- static authority, image, security and required-gate success;
- current code review with no unresolved factual finding.
