# Third-Party Software Notices

Nexus Helpdesk includes unmodified third-party runtime components distributed
under their respective licenses.

## Authoritative candidate inventory

This repository does not treat a manually maintained package list as release
authority. For every candidate image, the `release-image-assurance` workflow
must produce and retain all of the following against the same immutable source
SHA and image ID:

- a minimal CycloneDX application SBOM;
- a Critical/High vulnerability summary;
- a machine-evaluated license summary;
- exact, accountable and expiring review records;
- a release-image manifest containing the evidence digests;
- provenance for the final accepted candidate.

A candidate with missing, unknown, denied, unreviewed or identity-mismatched
license evidence is not releasable. This notice does not override that gate and
does not itself constitute a legal approval.

## LGPL-reviewed Python components

The exact candidate currently includes these unmodified Python distributions:

- `pkg:pypi/psycopg@3.2.6` — `LGPL-3.0-only`
- `pkg:pypi/psycopg-binary@3.2.6` — `LGPL-3.0-only`

Upstream source and license authority:

- <https://github.com/psycopg/psycopg/tree/3.2.6>

For these components the release gate requires all of the following:

1. the installed distribution license/COPYING files are retained and hashed;
2. the components are distributed unmodified;
3. the upstream source reference is retained;
4. recipients are not prevented from replacing the LGPL-covered component;
5. any version or license change invalidates the exact compliance record;
6. the review record has an accountable owner and expiry date.

The Nexus application code is not relicensed by this notice. Any modification
to an LGPL-covered component requires a new review and corresponding source
obligations before release.

## Other license materials

Python distributions are installed without deleting their package metadata or
license files. Alpine and Python base-image components remain governed by their
upstream licenses. The exact candidate SBOM and package metadata must be used to
identify the applicable copyright notices and license texts for distribution.

No project contributor may remove upstream notices, replace a review-required
license with an allowlisted label, or use this file to bypass the candidate
license policy.
