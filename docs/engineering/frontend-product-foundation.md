# Frontend Product Foundation

## Active authority

- Product: `webapp/PRODUCT.md`
- Design: `webapp/DESIGN.md`
- Routes: `webapp/src/routes/`
- App shell: `webapp/src/app/AppShell.tsx`
- Navigation: `webapp/src/app/navigation.ts`
- UI framework: `@mui/material@9.2.0`
- Theme: `webapp/src/theme/nexusTheme.ts`
- Theme provider: `webapp/src/theme/NexusThemeProvider.tsx`
- Operational presentation: `webapp/src/app/OperatorPresentation.tsx`
- HTTP transport: `webapp/src/lib/apiClient.ts`

## Runtime model

Nexus has one operator frontend:

- `/workspace`: queue, case, evidence, ownership, action and communication;
- `/knowledge`: one capability-aware Knowledge implementation;
- `/channels`, `/runtime`, `/control-tower`: supporting routes in the same AppShell;
- `/webchat`: compatibility redirect only.

Generic controls use MUI directly. Product-specific components may compose MUI but may not recreate generic controls.

## Retired paths

The following must remain absent:

- `frontend/`;
- `webapp/src/components/ui/`;
- `webapp/src/shared/ui/`;
- custom token and shared-component CSS;
- route-private CSS;
- `KnowledgeReadOnlyPage.tsx`;
- `webapp/src/lib/cn.ts`;
- `.github/workflows/` files.

## Non-duplication rules

- one AppShell and navigation graph;
- one ThemeProvider and theme;
- one generic UI framework;
- one Workspace route, state graph and API adapter;
- one Knowledge page;
- one generic HTTP transport;
- no V2 or old/new parallel route;
- superseded code and documentation are deleted in the same delivery.

## State truth

The UI keeps requested, accepted, queued, technical completion, operational completion, customer notification, business result confirmation and repair-required states distinct. A technical success cannot be presented as safe business completion.

## Verification

The final candidate requires, on one unchanged tree:

```bash
cd webapp
npm run architecture
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e

cd ..
python scripts/verify_repository.py --static-only
```

The candidate dependency tree is reproducible. Architecture, lint, strict type checking, 49 contract tests, production build, route splitting and 44 browser journeys passed. One external RC journey was skipped because no RC environment was supplied. Merge remains SHA-locked; deployment is a separate release operation.
