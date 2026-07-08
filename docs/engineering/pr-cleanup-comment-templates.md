# PR Cleanup Comment Templates

Generated: 2026-07-07

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

These templates are copy-ready GitHub PR comments. They do not close, merge, approve, deploy, or tag anything by themselves.

## 1. Superseded by main

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR appears to be superseded by the current main line and later governance work. It should not be merged directly because the branch is stale against the current baseline and may reintroduce behavior that has already been replaced or governed elsewhere.

Recommended next step: verify whether any specific test, assertion, or design note is still missing on current `main`. If something is still useful, extract that piece into a new small PR based on the current baseline. Otherwise, this PR can remain as historical reference and be considered for closure by a human maintainer.

## 2. Split required

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR is too broad to merge directly. It mixes multiple implementation lanes, which makes review, CI ownership, rollback, and production risk control difficult.

Recommended next step: split the useful parts into small PRs based on the current baseline. Each replacement PR should have one clear scope, focused tests, and an explicit rollback boundary. This comment is not closing the PR; it only marks the safer path forward.

## 3. Extract small patch only

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR contains a useful idea or patch, but the branch itself should not be merged directly because it is stale, stacked, or mixed with unrelated changes.

Recommended next step: extract only the still-relevant minimal patch into a new branch from the current baseline. Keep the replacement PR narrow, include focused tests, and avoid cherry-picking unrelated files from this branch.

## 4. Close/archive candidate

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR is a close/archive candidate because it is stale, superseded, too broad, or no longer aligned with the current release boundary. It should not be merged directly.

Recommended next step: preserve any useful design notes in the relevant issue or documentation, then have a human maintainer decide whether to close it. This comment does not close the PR and does not imply that any replacement has already been merged.

## 5. Keep as design reference

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR is useful as a design or investigation reference, but it is not a production merge candidate. It should not be merged directly because it does not match the current baseline and release boundary.

Recommended next step: link this PR from the relevant roadmap, design note, or follow-up issue. If implementation is still needed, create a fresh, narrow PR from the current baseline.

## 6. Needs security review

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR touches a security-sensitive area or introduces a vendor, file handling, outbound, or runtime boundary concern. It should not be merged directly until the security scope is reviewed and the required checks are green.

Recommended next step: document the security boundary, confirm the changed files are minimal, run the focused regression tests, and request a security review. If the branch is stale or broad, extract a smaller current-baseline PR first.

## 7. Needs product decision

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR changes product behavior, operational workflow, customer-facing capability, or a business process that requires product ownership before engineering merge review. It should not be merged directly without that decision.

Recommended next step: capture the product decision needed, define the intended rollout and rollback boundary, then either close/archive this branch or recreate a smaller PR from the current baseline.

## 8. Needs rebase

Current main baseline: `bcec9cba93103b4fa71e523d0b3ca7c0a8f8c1e4`

This PR is stale against the current baseline and should not be merged directly in its current form. Historical local validation or old CI results are not enough for the current release line.

Recommended next step: rebase or recreate the PR from the current baseline, then rerun the required CI and focused regression tests. If conflicts are substantial, prefer extracting a smaller replacement PR instead of repairing the full branch.
