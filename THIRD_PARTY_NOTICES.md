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

## LiveKit local inference model

The exact candidate includes this unmodified runtime dependency required by
`livekit-agents==1.6.6`:

- `pkg:pypi/livekit-local-inference@0.2.6` —
  `Apache-2.0 AND LicenseRef-LiveKit-Model`

Upstream package, framework and model-license authorities:

- <https://pypi.org/project/livekit-local-inference/0.2.6/>
- <https://github.com/livekit/agents/tree/livekit-agents%401.6.6>
- <https://huggingface.co/livekit/turn-detector/blob/main/LICENSE>

The package source is Apache-2.0. Its bundled local VAD/end-of-turn model is
subject to the LiveKit Model License. Nexus may use and distribute the exact
component only as part of the LiveKit Agents runtime. The following controls are
mandatory:

1. the component is not exposed or executed as a standalone model service;
2. it is not connected to, embedded in, or redistributed for use with another
   agent or inference framework;
3. neither the model nor its output is used to train, fine-tune, evaluate for
   improvement, or otherwise develop a non-LiveKit model;
4. model files, inference-enabling code and license notices are not stripped,
   extracted for separate distribution or modified;
5. the SBOM PURL and version must match the exact expiring compliance record;
6. any version, package-license, model-license or runtime-use change invalidates
   the approval and blocks release pending a new review.

This record does not authorize broader use of LiveKit proprietary models and
does not convert the LiveKit Model License into an open-source license.

## Other license materials

Python distributions are installed without deleting their package metadata or
license files. Debian and Python base-image components remain governed by their
upstream licenses. The exact candidate SBOM and package metadata must be used to
identify the applicable copyright notices and license texts for distribution.

No project contributor may remove upstream notices, replace a review-required
license with an allowlisted label, or use this file to bypass the candidate
license policy.
