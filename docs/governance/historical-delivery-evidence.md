# Historical Delivery Evidence

## Authority

Point-in-time root delivery reports are not current release, production-readiness, architecture, security or product authority.

Current governance and delivery authority is maintained through:

- Issue-only delivery index: #489
- Repository audit remediation: #545
- Legacy-surface convergence: #650
- Root-report retirement: #652
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

Their contents remain available through Git history and the commits that originally introduced or modified them. Restoring any report to the active tree requires a current owner, current consumer, explicit retention rationale and an update to the retirement regression.

## Retrieval

Use Git history rather than copying historical report bodies back into `main`:

```bash
git log --all -- <path>
git show <commit>:<path>
```

Do not treat an old report statement such as “verified”, “approved”, “production-ready” or “signoff” as current evidence. Re-run the applicable exact-head checks and consult the current owning Work Item.
