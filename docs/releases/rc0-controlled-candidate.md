# Controlled Deployment Candidate

- Parent: #707
- RC qualification: #708
- Recovery authority: #532
- Server deployment: #709
- Independent acceptance: #710
- Base main: `1d48ee935c55949837f87bee361718b725586918`
- Alembic Head: `20260713_0059`
- Candidate class: controlled server deployment

This marker creates one exact candidate containing the merged RC side-effect proof and Recovery Foundation. The pull-request Head is the candidate identity for RC and Recovery qualification.

Required posture:

- Provider traffic disabled;
- real outbound disabled;
- production data excluded;
- external Tool and TTS/provider output execution must be zero;
- backup, restore and bounded rollback evidence must pass;
- production readiness and #533 GO remain false.

No code or runtime behavior is changed by this marker.
