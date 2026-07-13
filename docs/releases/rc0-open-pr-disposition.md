# RC0 open pull-request disposition

Snapshot authority: Work Item #626. Re-read GitHub before final candidate
selection because PR state can change after this document is merged.

The RC0 rule is: start from current `main`; include an open PR only when fresh
candidate evidence proves that the PR fixes a release blocker and the PR is
current-main reconciled, exact-head accepted, non-RED, and scope-bounded.

| PR | RC0 disposition | Reason |
|---:|---|---|
| #622 | **Exclude / defer** | Explicit intentional RED frontend slice. It cannot enter a deployable candidate until GREEN, reviewed, and accepted. |
| #618 | **Close temporary after stack use** | Stack synchronization only; never a product or release candidate. |
| #609 | **Defer** | Tenant migration preflight/planning evidence, not a runtime blocker fix by itself. |
| #608 | **Defer; rerun after RC0** | Recovery qualification is useful evidence but must be rerun against the exact selected candidate. |
| #605 | **Close temporary** | Temporary integration PR; explicitly prohibited from merge to `main`. |
| #604 | **Close temporary** | Stack synchronization PR; explicitly not an independent product PR. |
| #603 | **Defer** | Git-history secret assurance is important for production exposure control but does not change the isolated RC runtime chain. |
| #599 | **Defer** | Knowledge quarantine is production-supply-chain hardening. RC0 disables external Knowledge embeddings and does not test publication. |
| #597 | **Defer** | ExternalChannel inventory/reintroduction control is independent; RC0 explicitly disables all ExternalChannel runtime paths. |
| #596 | **Defer unless RC evidence fails on event persistence** | Typed TicketEvent persistence is important product hardening but is not assumed to block isolated build/start/smoke. |
| #595 | **Defer** | Provider canary authority is not required because RC0 disables Provider Runtime, sets canary to zero, and enables the kill switch. |
| #593 | **Defer; rerun on exact candidate** | Resilience qualification should consume the selected candidate rather than define it. |
| #580 | **Included through main** | Release-image assurance merged to `main@05d1e75ce9fd852ea724decb5e3d2faafc870895` and was synchronized into the RC branch through temporary PR #631. |
| #578 | **Exclude / reconstruct later** | Known blocking defects, old architecture assumptions, and scope expansion make it unsuitable for urgent candidate convergence. |
| #631 | **Completed temporary sync** | Branch-only synchronization of current `main` into #629. It is not an independent release deliverable and must not be merged to `main` separately. |

## Mainline inclusion

The candidate branch is reconciled with `main@05d1e75ce9fd852ea724decb5e3d2faafc870895`.
It includes the business-scenario catalog merged through #594 and release-image
assurance merged through #580. No other open implementation or intentional RED
PR is included by default.

## Cleanup policy

Do not close or supersede an active implementation PR merely to reduce the open
PR count. Close only PRs that are explicitly temporary, obsolete, or replaced,
after confirming their Work Item comments and claims.

Do not cherry-pick old PRs into RC0. Reconstruct a demonstrated blocker on the
current RC branch with focused tests and exact-head evidence.
