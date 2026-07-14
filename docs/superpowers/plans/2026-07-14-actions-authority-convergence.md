# GitHub Actions Authority Convergence

## Authority

- Work Item: #574
- Parent integration authority: #747 / PR #748
- Governance dependency: #744 / PR #746
- Stacked branch: `stack/574-actions-convergence-on-748`
- Merge target: `work/744-canonical-operator-console-consolidation` only
- Direct main merge: forbidden

## Target workflow graph

Exactly six authoritative validation workflows:

1. `frontend` — contracts, type/lint, production build, route/size and browser evidence;
2. `backend` — focused contract matrices plus full regression;
3. `migration` — Alembic/schema/model/PostgreSQL parity;
4. `security` — secret, dependency/SBOM, CodeQL and Actions supply-chain controls;
5. `release` — immutable image and exact-main candidate orchestration;
6. `governance` — coordination, tracked-tree rationalization, disposition and anti-reintroduction.

Other files may remain only as reusable workflow components, bounded job matrices, explicit Release/Publication workflows, or Historical-delete records. They cannot be independent overlapping authorities.

## Security invariants

- every `uses:` reference is a full 40-character commit SHA with a readable version comment;
- PR workflows are read-only, use exact proposed Head, and set `persist-credentials: false`;
- no PR workflow commits, pushes, tags or writes repository content;
- `contents: write` exists only in explicitly classified release/publication workflows;
- privileged triggers never execute untrusted PR code;
- attacker-controlled event values are not interpolated directly into shell or script;
- artifacts are bounded, identity-bound and security-scanned.

## Convergence rules

- one locked dependency installation per ecosystem per authority run;
- no duplicated `npm ci/test/build` chains across independent workflows;
- specialized suites execute through reusable workflows or matrices;
- downstream release jobs consume exact artifacts rather than rebuilding divergent candidates;
- changed-path routing and required-check names are machine-readable and fail closed;
- `generate-radix-lockfile.yml` and any branch-specific auto-commit workflow are deleted;
- Round-number workflows/reports cannot remain current release authority.

## Coordination

This RED slice must be rebased on the latest #748 Head and consume #746's final governance entrypoint before implementation. It is not a second product branch. No workflow deletion is accepted without inventory, current consumer mapping and exact-head replacement proof.

## Verification

- focused Actions inventory tests;
- full repository inventory with zero unclassified workflows;
- action-SHA and permissions audit;
- required-check graph test;
- representative frontend/backend/migration/security/release/governance runs;
- independent GitHub Actions hardening review.