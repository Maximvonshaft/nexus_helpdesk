from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "build_controlled_candidate_manifest.py"
)
SPEC = importlib.util.spec_from_file_location(
    "build_controlled_candidate_manifest",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ControlledCandidateManifestTests(unittest.TestCase):
    source = "a" * 40
    image_id = "sha256:" + "b" * 64
    registry_digest = "sha256:" + "c" * 64
    sidecar_image_id = "sha256:" + "1" * 64
    sidecar_registry_digest = "sha256:" + "2" * 64
    migration = "20260713_0059"
    build_time = "20260713T190000Z"
    app_version = "controlled-aaaaaaaaaaaa"
    sidecar_build_time = "2026-07-13T19:00:00Z"
    sidecar_app_version = "controlled-" + source
    local_tag = "nexusdesk/helpdesk:rc-test-" + source
    sidecar_local_tag = "nexusdesk/whatsapp-sidecar:controlled-" + source
    registry_image = "ghcr.io/maximvonshaft/nexus_helpdesk"
    sidecar_registry_image = (
        "ghcr.io/maximvonshaft/nexus_helpdesk-whatsapp-sidecar"
    )

    def _write(self, root: Path, name: str, payload: dict) -> Path:
        path = root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _inputs(self, root: Path) -> dict[str, Path]:
        rc = {
            "schema": "nexus.osr.rc-test-candidate.v1",
            "decision": "RC0_TEST_DEPLOYABLE",
            "candidate": {
                "source_sha": self.source,
                "frontend_build_sha": self.source,
                "image_tag": self.local_tag,
                "image_id": self.image_id,
                "migration_revision": self.migration,
                "config_profile": "rc-test-isolated-v1",
                "config_digest": "sha256:" + "d" * 64,
                "postgres_image_digest": (
                    "pgvector/pgvector@sha256:" + "e" * 64
                ),
                "nginx_image_digest": "nginx@sha256:" + "f" * 64,
            },
            "checks": {"image_build": "pass", "browser_smoke": "pass"},
            "safety": {
                "production_data_used": False,
                "production_network_joined": False,
                "provider_candidate_enabled": False,
                "real_outbound_enabled": False,
                "whatsapp_enabled": False,
                "speedaf_write_enabled": False,
                "operations_dispatch_enabled": False,
                "production_ready": False,
                "full_osr_automation": "NO_GO",
                "test_environment_isolated": True,
            },
        }
        assurance = {
            "schema_version": "nexus_release_image_assurance_v1",
            "status": "pass",
            "source_sha": self.source,
            "image_id": self.image_id,
            "critical_count": 0,
            "high_count": 0,
            "unresolved_license_count": 0,
            "image_pushed": False,
            "deployment_performed": False,
        }
        binding = {
            "schema_version": "nexus_release_image_compliance_binding_v1",
            "status": "pass",
            "source_sha": self.source,
            "image_id": self.image_id,
            "image_pushed": False,
            "deployment_performed": False,
        }
        sidecar_assurance = {
            "schema": "nexus.whatsapp-sidecar.image-assurance.v1",
            "status": "pass",
            "source_sha": self.source,
            "image_id": self.sidecar_image_id,
            "image_tag": self.sidecar_local_tag,
            "revision": self.source,
            "build_time": self.sidecar_build_time,
            "app_version": self.sidecar_app_version,
            "runtime_user": "node",
            "critical_count": 0,
            "high_count": 0,
            "sbom_digest": "sha256:" + "3" * 64,
            "image_pushed": False,
            "deployment_performed": False,
        }
        recovery = {
            "schema_version": "nexus_postgres_recovery_qualification_v1",
            "status": "pass",
            "source_sha": self.source,
            "alembic_head": self.migration,
            "reasons": [],
            "foreign_key_definitions_match": True,
            "foreign_keys_validated": True,
            "synthetic_marker_restored": True,
            "production_data_used": False,
            "production_mutation_performed": False,
        }
        receipt = {
            "schema": "nexus.osr.registry-publish-receipt.v1",
            "status": "pass",
            "source_sha": self.source,
            "frontend_build_sha": self.source,
            "build_time": self.build_time,
            "app_version": self.app_version,
            "embedded_image_tag": self.local_tag,
            "registry_image": self.registry_image,
            "registry_digest": self.registry_digest,
            "registry_reference": (
                self.registry_image + "@" + self.registry_digest
            ),
            "local_image_id": self.image_id,
            "pulled_image_id": self.image_id,
            "image_pushed": True,
            "deployment_performed": False,
        }
        sidecar_receipt = {
            "schema": "nexus.whatsapp-sidecar.registry-publish-receipt.v1",
            "status": "pass",
            "source_sha": self.source,
            "build_time": self.sidecar_build_time,
            "app_version": self.sidecar_app_version,
            "runtime_user": "node",
            "registry_image": self.sidecar_registry_image,
            "registry_digest": self.sidecar_registry_digest,
            "registry_reference": (
                self.sidecar_registry_image
                + "@"
                + self.sidecar_registry_digest
            ),
            "local_image_id": self.sidecar_image_id,
            "pulled_image_id": self.sidecar_image_id,
            "image_pushed": True,
            "deployment_performed": False,
        }
        return {
            "rc": self._write(root, "candidate-manifest.json", rc),
            "assurance": self._write(
                root,
                "release-image-manifest.json",
                assurance,
            ),
            "binding": self._write(
                root,
                "release-image-compliance-binding.json",
                binding,
            ),
            "sidecar_assurance": self._write(
                root,
                "sidecar-image-manifest.json",
                sidecar_assurance,
            ),
            "recovery": self._write(
                root,
                "recovery-evidence.json",
                recovery,
            ),
            "receipt": self._write(
                root,
                "registry-publish-receipt.json",
                receipt,
            ),
            "sidecar_receipt": self._write(
                root,
                "whatsapp-sidecar-registry-publish-receipt.json",
                sidecar_receipt,
            ),
        }

    def _build(self, root: Path, paths: dict[str, Path], **overrides) -> int:
        values = {
            "source_sha": self.source,
            "registry_image": self.registry_image,
            "registry_digest": self.registry_digest,
            "local_image_id": self.image_id,
            "pulled_image_id": self.image_id,
            "migration_head": self.migration,
            "frontend_sha": self.source,
            "attestation_id": "attestation-123",
            "attestation_url": (
                "https://github.com/Maximvonshaft/nexus_helpdesk/"
                "attestations/123"
            ),
            "sidecar_registry_image": self.sidecar_registry_image,
            "sidecar_registry_digest": self.sidecar_registry_digest,
            "sidecar_local_image_id": self.sidecar_image_id,
            "sidecar_pulled_image_id": self.sidecar_image_id,
            "sidecar_attestation_id": "attestation-sidecar-123",
            "sidecar_attestation_url": (
                "https://github.com/Maximvonshaft/nexus_helpdesk/"
                "attestations/sidecar-123"
            ),
            "rc_manifest_path": paths["rc"],
            "release_image_manifest_path": paths["assurance"],
            "compliance_binding_path": paths["binding"],
            "sidecar_image_manifest_path": paths["sidecar_assurance"],
            "recovery_evidence_path": paths["recovery"],
            "publish_receipt_path": paths["receipt"],
            "sidecar_publish_receipt_path": paths["sidecar_receipt"],
            "output_path": root / "controlled-candidate-manifest.json",
        }
        values.update(overrides)
        return MODULE.build_manifest(**values)

    def test_builds_exact_digest_bound_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            self.assertEqual(self._build(root, paths), 0)
            payload = json.loads(
                (root / "controlled-candidate-manifest.json").read_text()
            )
            self.assertEqual(payload["status"], "pass")
            self.assertEqual(
                payload["candidate"]["registry_reference"],
                self.registry_image + "@" + self.registry_digest,
            )
            sidecar = payload["candidate"]["whatsapp_sidecar"]
            self.assertEqual(
                sidecar["registry_reference"],
                self.sidecar_registry_image
                + "@"
                + self.sidecar_registry_digest,
            )
            self.assertEqual(sidecar["runtime_user"], "node")
            self.assertEqual(sidecar["source_sha"], self.source)
            self.assertTrue(
                payload["whatsapp_sidecar_attestation"][
                    "registry_provenance_pushed"
                ]
            )
            self.assertEqual(
                payload["candidate"]["build_time"],
                self.build_time,
            )
            self.assertEqual(
                payload["candidate"]["app_version"],
                self.app_version,
            )
            self.assertFalse(payload["safety"]["production_ready"])
            self.assertFalse(
                payload["safety"]["external_effects_authorized"]
            )
            self.assertEqual(
                payload["safety"]["full_osr_automation"],
                "NO_GO",
            )

    def test_rejects_registry_pull_binary_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "registry_pull_image_id_mismatch",
            ):
                self._build(
                    root,
                    paths,
                    pulled_image_id="sha256:" + "9" * 64,
                )

    def test_rejects_sidecar_registry_pull_binary_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "sidecar_registry_pull_image_id_mismatch",
            ):
                self._build(
                    root,
                    paths,
                    sidecar_pulled_image_id="sha256:" + "9" * 64,
                )

    def test_rejects_sidecar_source_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            sidecar = json.loads(paths["sidecar_assurance"].read_text())
            sidecar["source_sha"] = "9" * 40
            paths["sidecar_assurance"].write_text(json.dumps(sidecar))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "sidecar_assurance_source_mismatch",
            ):
                self._build(root, paths)

    def test_rejects_sidecar_high_vulnerability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            sidecar = json.loads(paths["sidecar_assurance"].read_text())
            sidecar["high_count"] = 1
            paths["sidecar_assurance"].write_text(json.dumps(sidecar))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "sidecar_assurance_high_findings",
            ):
                self._build(root, paths)

    def test_rejects_stale_recovery_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            recovery = json.loads(paths["recovery"].read_text())
            recovery["source_sha"] = "9" * 40
            paths["recovery"].write_text(json.dumps(recovery))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "recovery_source_mismatch",
            ):
                self._build(root, paths)

    def test_rejects_external_effect_authority_in_rc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            rc = json.loads(paths["rc"].read_text())
            rc["safety"]["real_outbound_enabled"] = True
            paths["rc"].write_text(json.dumps(rc))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "rc_safety_invalid:real_outbound_enabled",
            ):
                self._build(root, paths)

    def test_rejects_unbound_build_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            receipt = json.loads(paths["receipt"].read_text())
            receipt["build_time"] = "unknown"
            paths["receipt"].write_text(json.dumps(receipt))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "publish_receipt_build_time_invalid",
            ):
                self._build(root, paths)

    def test_rejects_embedded_image_tag_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = self._inputs(root)
            receipt = json.loads(paths["receipt"].read_text())
            receipt["embedded_image_tag"] = "nexusdesk/helpdesk:other"
            paths["receipt"].write_text(json.dumps(receipt))
            with self.assertRaisesRegex(
                MODULE.ManifestError,
                "publish_receipt_embedded_tag_mismatch",
            ):
                self._build(root, paths)


if __name__ == "__main__":
    unittest.main()
