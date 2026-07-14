# Frontend engineering authority

## Current authority

Work Item #742 completes the single-frontend convergence authorized by #573 and the frontend audit #611.

- Production frontend: `webapp/`.
- Canonical case route: `/workspace`.
- Compatibility route: `/webchat` redirects only.
- Shared tokens: `webapp/src/styles/tokens.css`.
- Shared components: `webapp/src/components/ui/`.
- Customer-service product register: `webapp/PRODUCT.md`.
- Design authority: `webapp/DESIGN.md`.
- Machine contract: `webapp/design/frontend-product-foundation.v1.json`.

The legacy static `frontend/`, duplicate Support Console, duplicate `shared/ui`, and parallel feature palettes are not supported sources.

## Architecture boundaries

1. Routes may load only the canonical customer-service domains.
2. Feature pages compose shared `components/ui` primitives.
3. Feature CSS consumes semantic tokens and contains no raw palette.
4. API authentication, timeout, request IDs, and error mapping use `src/lib/apiClient.ts`.
5. Ordinary customer-service screens do not expose internal automation, model, provider, prompt, inference, runtime, or job terminology.
6. `/workspace` owns customer case work; management pages do not create a second queue or case truth.
7. Architecture tests fail if legacy frontend sources, competing consoles, duplicate Button/Badge authorities, raw feature colors, or prohibited visible terminology return.

## Quality gates

Required before merge:

- `npm test`
- `npm run typecheck`
- `npm run lint`
- `npm run build`
- `npm run size-report`
- `npm run e2e`
- `node scripts/assert-frontend-convergence.mjs`

Representative evidence covers 375, 768, 1024 and 1440 pixel viewports, keyboard navigation, protected drafts, customer queue scale, slow/unavailable requests, and action-result truth.

## Remaining authority

Work Item #564 continues representative-volume, accessibility, and degraded-state hardening. It must extend this single authority rather than restore a second frontend or design system.