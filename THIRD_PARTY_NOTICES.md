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
- exact, accountable and expiring exceptions, when any are approved;
- a release-image manifest containing the evidence digests;
- provenance for the final accepted candidate.

A candidate with missing, unknown, denied, unreviewed or identity-mismatched
license evidence is not releasable. This notice does not override that gate and
does not itself constitute a legal approval.

## License materials

Python distributions are installed without deleting their package metadata or
license files. Alpine and Python base-image components remain governed by their
upstream licenses. The exact candidate SBOM and package metadata must be used to
identify the applicable copyright notices and license texts for distribution.

No project contributor may remove upstream notices, replace a review-required
license with an allowlisted label, or use this file to bypass the candidate
license policy.
