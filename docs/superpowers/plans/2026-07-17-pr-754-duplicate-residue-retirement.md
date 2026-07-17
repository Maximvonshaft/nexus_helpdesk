# PR #754 Duplicate Residue Retirement

## Authority

- Source PR: #754, merged into `main` at `5f2922e9beba95faedf70023d426012bbde0b4ec`.
- Sole repair branch: `work/754-remove-ui-duplication-residue`.
- Scope: frontend implementation and anti-reintroduction governance only.
- GitHub Actions remain retired and must not be restored.

## Root cause

PR #754 physically removed the former custom component and CSS directories, but several generic responsibilities were recreated under new local names. The original architecture gate relied heavily on known paths and symbol names, so semantically equivalent replacements could bypass it.

Confirmed residue classes:

1. duplicated Workspace presentation type and local generic presentation layer;
2. duplicated status-line, tone-map and count-marker implementations;
3. duplicated route/full-page loading boundaries;
4. duplicated technical-disclosure Accordion compositions;
5. duplicated safe record/string/number conversion helpers;
6. duplicated channel-label projection;
7. competing `main` landmark ownership between AppShell and route pages.

## Final ownership model

- Generic operator presentation: `webapp/src/app/OperatorPresentation.tsx`.
- Operational status truth: `webapp/src/domain/operationalPresentation.ts`.
- Workspace-specific presentation semantics: `webapp/src/lib/operatorWorkspacePresentation.ts`.
- Safe value boundaries: `webapp/src/lib/format.ts`.
- Channel presentation semantics: `webapp/src/lib/supportStatus.ts`.
- Workspace state and capability helpers: `webapp/src/features/operator-workspace/operatorWorkspaceState.ts`.
- Governed Workspace actions: `webapp/src/features/operator-workspace/OperatorWorkspaceActions.tsx`.
- Route pages own their `main` landmark; `AppShell` does not.

## Physical retirement

Deleted:

- `webapp/src/features/operator-workspace/OperatorWorkspaceCommon.tsx`.

Removed from local pages:

- `WorkspacePresentation` duplicate type;
- `WorkspaceStatusLine` and `WorkspaceSectionHeading`;
- `WorkspaceLoading` and `FullPageBoundary`;
- `TechnicalDisclosure` and direct route-level Accordion disclosure;
- `StatusCount`, `toneColor`, `safeTone`;
- `providerLabel`, `channelLabel`;
- `safeRecord`, `safeRecordArray`, `safeWorkspaceRecord`, `workspaceText`, `workspaceNumber`, `textValue`, `numberValue`;
- stale `className: 'is-ai'` residue;
- AppShell-owned nested `main`.

## Permanent enforcement

`webapp/scripts/assert-frontend-architecture.mjs` and the frontend contract tests now reject:

- retired paths and parallel UI routes;
- direct Accordion technical disclosure outside the one presentation owner;
- route-private fact grids;
- generic circular status markers outside the canonical owner, with only the Case Spine business-progress marker allowed;
- route-private full-page layouts;
- renamed versions of retired presentation and safe-value helpers;
- duplicate theme/provider/CssBaseline or generic HTTP transport;
- stale dependencies, lock roots, raw colors, route CSS and unreachable production files.

## Verification state

Completed in the isolated authoring environment:

- branch remains ahead of current `main` and zero behind;
- rewritten TS/TSX files parse without syntax diagnostics using TypeScript `transpileModule`;
- JavaScript test and architecture scripts pass `node --check`;
- governance YAML parses successfully;
- exact branch diff and changed-file inventory reviewed.

Not executed and not claimed as passing:

```bash
cd webapp
npm ci
npm run architecture
npm run lint
npm run typecheck
npm test
npm run build
npm run e2e

cd ..
python scripts/verify_repository.py --static-only
python scripts/verify_repository.py --focused-backend --skip-browser
python scripts/verify_repository.py
```

The repair PR must remain Draft until these commands run against one unchanged exact Head and an independent review confirms no duplicate implementation residue remains.

## Safety

This work authorizes no deployment, production data mutation, provider enablement, customer outbound, schema migration or GitHub Actions restoration.
