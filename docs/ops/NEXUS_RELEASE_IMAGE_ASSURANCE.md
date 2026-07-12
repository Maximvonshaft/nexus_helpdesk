# Nexus release image assurance

The assurance workflow builds a local candidate image and never pushes, tags or deploys it.

Release evidence is accepted only when:

- the candidate image is built from the exact GitHub source SHA;
- Trivy reports no unresolved Critical or High vulnerability;
- every vulnerability exception matches vulnerability ID, package and installed version, has an owner and reason, and expires within 180 days;
- the CycloneDX image SBOM passes the machine-readable license policy;
- license exceptions match package, version and license and expire within 180 days;
- the bounded manifest binds source SHA, local image ID and evidence digests;
- the complete evidence bundle passes the repository artifact leak scanner;
- the manifest states that no image was pushed and no deployment occurred.

A manual workflow run may attest the bounded manifest. The assurance workflow does not grant permission to publish an image. Image publication and deployment require a later release-candidate decision with exact digest, restore/rollback proof and an explicit GO record.
