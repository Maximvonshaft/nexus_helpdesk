# Nexus visual authority

## Decision

Material UI is the sole generic visual component authority for the authenticated Nexus operator product.

- `@mui/material@9.2.0`
- `@mui/icons-material@9.2.0`
- `@emotion/react@11.14.0`
- `@emotion/styled@11.14.1`
- `react-is@18.3.1`

The sole theme and provider are:

- `webapp/src/theme/nexusTheme.ts`
- `webapp/src/theme/NexusThemeProvider.tsx`

`ThemeProvider` and `CssBaseline` are mounted once at the application root. This document is an architecture decision, not a mutable delivery-status ledger. Current build, test, pull-request and deployment state must be read from the exact GitHub object and commit evidence.

## Product boundary

The authenticated operator product is `webapp/`. The public customer WebChat widget under `backend/app/static/webchat/` is a separate channel surface and is not a second operator product.

The operator product has exactly one application shell, navigation authority, route hierarchy, MUI theme, presentation helper authority and frontend HTTP transport.

## Retired visual authorities

The following paths must remain physically absent:

- `frontend/`;
- `webapp/src/features/support-console/`;
- `webapp/src/shared/ui/`;
- `webapp/src/shared/api/`;
- `webapp/src/components/ui/`;
- `webapp/src/styles/tokens.css`;
- `webapp/src/styles/components.css`;
- route-private visual stylesheets;
- `webapp/src/features/knowledge/KnowledgeReadOnlyPage.tsx`;
- V2, redesign, new-workspace or parallel shell routes.

Only two source stylesheets are allowed:

- `webapp/src/styles.css` for document and browser foundations;
- `webapp/src/a11y.css` for the screen-reader utility.

Colors, spacing, typography, shape, elevation, focus, motion and component overrides belong in `nexusTheme.ts`.

## Canonical composition

- Application shell: `webapp/src/app/AppShell.tsx`
- Navigation: `webapp/src/app/navigation.ts`
- Operator presentation: `webapp/src/app/OperatorPresentation.tsx`
- Workspace orchestration: `webapp/src/features/operator-workspace/OperatorWorkspacePage.tsx`
- Knowledge: `webapp/src/features/knowledge/KnowledgePage.tsx`
- HTTP transport: `webapp/src/lib/apiClient.ts`

Business vocabulary remains owned by domain mapping modules. Generic rendering remains owned by MUI and the single Nexus theme.

## Permanent enforcement

`webapp/scripts/assert-frontend-architecture.mjs` rejects retired paths, unreachable production modules, another shell or navigation, another UI framework, another theme/provider/baseline owner, route-private generic presentation helpers, raw visual authorities outside the theme, extra source CSS, V2 routes, unused runtime dependencies and a second GitHub Actions workflow.

`webapp/scripts/assert-http-transport-authority.mjs` rejects any second ownership of fetch lifecycle, API base URL, authentication headers, token storage or global 401 handling.

## Verification

Use one unchanged exact commit:

```bash
cd webapp
npm ci --ignore-scripts --no-audit --no-fund
npm run verify
npm run e2e
```

Repository-wide verification remains:

```bash
python scripts/verify_repository.py --expected-sha <exact-sha>
```

No unexecuted check, historical pull request or document statement is acceptance evidence.
