# Historical Delivery Evidence

## Authority

Point-in-time delivery reports, audits, implementation plans and demo scripts are not current release, production-readiness, architecture, security or product authority.

Current governance and delivery authority is maintained through:

- Issue-only delivery index: #489
- Repository audit remediation: #545
- Legacy-surface convergence: #650
- Root-report retirement: #652
- Round B document retirement: #656
- Sandbox overlay and stale audit retirement: #694
- Evidence-backed codebase rationalization and permanent deletion: #744
- Domain-specific Work Items and their accepted Pull Requests
- Runtime release metadata and exact-head CI evidence

## Retired root reports

The following historical reports were removed from the active tree under #652:

- `ROUND_A_VERIFY_RESULTS.md`
- `ROUND_A_DELIVERY_REPORT.md`
- `ROUND_B_VERIFY_RESULTS.md`
- `ROUND_B_MOBILE_APPLY.md`
- `ROUND24_HARDENING_REPORT.md`
- `ROUND25_HARDENING_REPORT.md`
- `NEXT_PHASE_MAX_PUSH_REPORT.md`
- `PRODUCTION_HARDENING_FIX_REPORT.md`
- `PRODUCTION_SIGNOFF_REPORT.md`
- `PATCH_NOTES.md`

## Retired Round B documents

The following point-in-time Round B documents were removed from the active tree under #656:

- `docs/round-b-delivery-report.md`
- `docs/round-b-self-audit.md`
- `docs/round-b-readonly-audit.md`
- `docs/round-b-implementation-plan.md`
- `docs/round-b-operator-demo-script.md`
- `docs/round-b-post-push-audit.md`

## Retired sandbox overlay and stale audit artifacts

The following point-in-time artifacts were removed from the active tree under #694:

- `APPLY_PATCH.md`
- `PATCH_MANIFEST.md`
- `VERIFY_RESULTS.md`
- `docs/audit/FINAL_REPORT.md`

The first three described a one-time ChatGPT-sandbox source-overlay package and were not a supported current deployment or release path. The audit report was bound to an old baseline and branch, recorded a then-failing repository test state, and explicitly did not establish production readiness.

## Retired overlay limitation note

The following point-in-time root artifact was removed under #744:

- `README_LIMITATION.md`

`README_LIMITATION.md` described a one-time overlay-kit delivery constraint rather than a supported repository, build, deployment or recovery contract. It had no supported runtime, build, workflow, release or operator consumer.

## Retired orphan backend prototypes

The following backend modules were removed under #744 after complete tracked-entry analysis and repository-wide code search found no current runtime, script, test, workflow, documentation or open-PR consumer:

- `backend/app/services/ai_runtime/safety_contract.py`
- `backend/app/services/pubsub.py`
- `backend/app/services/tracking_fact_redactor.py`
- `backend/app/services/webchat_handoff_policy.py`
- `backend/app/services/webchat_handoff_policy_config.py`
- `backend/app/services/webchat_intent_service.py`

These files were former prototypes, parallel implementations or once-active policies whose callers had already been removed. Their continued presence falsely implied current runtime authority. Reintroduction requires a current owner, a real entry-point consumer and the rationalization discovery gate to pass.

## Retired compatibility-only path

The following compatibility path was removed under #744:

- `backend/app/services/outbound_message_semantics.py`

`backend/app/services/outbound_message_semantics.py` was an explicit wildcard re-export whose own source named `backend/app/services/outbound_semantics.py` as canonical. Its tests were migrated to assert that the compatibility path stays absent and all application imports use the canonical module.

## Retired test-only tracking prototype

- `backend/tests/test_tracking_fact_no_trace.py`

The dedicated test file and two legacy-normalizer cases in `backend/tests/test_webchat_tracking_fact_mvp.py` were retired together with `tracking_fact_redactor.py`. Current Speedaf payload redaction and tracking-fact construction remain covered through the canonical `speedaf/redactor.py`, `speedaf/formatter.py`, and their contract tests.

Round-named reports, tests and smoke scripts remain governed by #574 and are not deleted through #744 while that Work Item is blocked.

Their contents remain available through Git history and the commits that originally introduced or modified them. Restoring any retired artifact to the active tree requires a current owner, current consumer, explicit retention rationale and an update to the retirement regression.

## Retrieval

Use Git history rather than copying historical report bodies back into `main`:

```bash
git log --all -- <path>
git show <commit>:<path>
```

Do not treat an old report statement such as “verified”, “approved”, “production-ready” or “signoff” as current evidence. Re-run the applicable exact-head checks and consult the current owning Work Item.
