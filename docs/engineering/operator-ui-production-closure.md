# Operator UI Production Closure

## Authority

This delivery extends the existing canonical frontend only:

- application: `webapp/`;
- shell: `webapp/src/app/AppShell.tsx`;
- routes: `webapp/src/routes/`;
- navigation and route titles: `webapp/src/app/navigation.ts`;
- visual authority: MUI and `webapp/src/theme/nexusTheme.ts`;
- operator state presentation: `webapp/src/app/OperatorPresentation.tsx`;
- HTTP transport: `webapp/src/lib/apiClient.ts`;
- business closure truth: the server-issued ticket closure receipt.

No second frontend, shell, design system, route registry, HTTP transport, case truth, closure truth, telemetry client or realtime product is permitted.

## Production contracts

1. Customer, operator and evidence-authored free text is displayed verbatim. Only exact repository-owned enum values may be translated.
2. Source `closed`, a completed Job, a sent message or a dispatched request never becomes safe closure through frontend inference.
3. Case Spine closure, outcome, notification, repair and observation states consume the server closure receipt.
4. Destructive or capacity-releasing actions require explicit user intent, a review step and a server-side recheck where applicable.
5. Dynamic announcements are bounded. Timelines are not live regions; only concise status messages are announced.
6. Every authenticated route title, timezone, focus treatment and responsive presentation is owned by the single AppShell/navigation authority.
7. Slow, unavailable and stale dependencies preserve the last server-confirmed information and identify it as degraded.
8. WebSocket delivery is the primary operator event path where supported; bounded HTTP polling is a fail-safe fallback, not a parallel truth.
9. Bundle, browser, accessibility, representative-volume, visual and Web Vitals evidence is part of the canonical acceptance gate.

## Browser support

The authenticated operator product is certified against the Playwright-pinned Chromium engine and current enterprise Chrome/Edge releases. Other engines are not represented as certified without exact browser evidence. The public WebChat widget has a separate browser contract.

## Release condition

Production UI acceptance requires one unchanged exact Head with:

- architecture, lint, TypeScript, unit contracts and production build;
- bundle budget;
- browser journeys at representative desktop, tablet and mobile sizes;
- deterministic visual evidence for normal, loading, empty, degraded, conflict and repair-required states;
- keyboard, focus, target-size, reduced-motion and 200% text evidence;
- representative-volume queue and timeline evidence;
- backend and PostgreSQL regression;
- static authority, image, security and required-gate success.
