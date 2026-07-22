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
- Operator language: `webapp/design/operator-language.v1.json`
- HTTP transport: `webapp/src/lib/apiClient.ts`

## Runtime model

Nexus has one operator frontend:

- `/workspace`: queue, case, evidence, ownership, action and communication;
- `/knowledge`: one capability-aware Knowledge implementation;
- `/agent-control`: automatic-handling plans, reply style, business rules, tools, integrations, model limits and governed diagnostics;
- `/channels`, `/runtime`, `/control-tower`, `/administration` and `/account`: supporting routes in the same AppShell;
- `/webchat`: compatibility redirect only.

Generic controls use MUI directly. Product-specific components may compose MUI but may not recreate generic controls.

## Operator language model

Primary surfaces are organized by the operator's task, not by backend implementation objects.

- show the section or task, current state, relevant facts, blocking reason, recovery step and explicit action;
- keep raw identifiers, policy codes, payloads, traces, protocol details and configuration internals in named progressive disclosures;
- use business labels such as `自动处理`, `处理方案`, `回复风格`, `业务规则`, `生效范围` and `运行记录`;
- do not present queued, accepted or synchronizing work as completed work;
- errors state what failed and what the operator can do next without exposing raw authorization or deployment terminology.

The language register and its contract tests cover every canonical route, including account, administration and automatic-handling configuration.

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
- one automatic-handling configuration route;
- one account and administration control plane;
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

The candidate dependency tree must remain reproducible. Merge remains SHA-locked; deployment is a separate release operation.
