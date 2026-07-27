from __future__ import annotations

import importlib.util
import unittest
from copy import deepcopy
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "whatsapp_e2e_evidence.py"
SPEC = importlib.util.spec_from_file_location("whatsapp_e2e_evidence", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WhatsAppEvidenceTests(unittest.TestCase):
    source_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    signing_key = b"nexus-whatsapp-evidence-test-key-0123456789abcdef"

    def _direction(self, prefix: str) -> dict:
        return {
            "provider_message_id": f"{prefix}.provider.message",
            "sent_at": "2026-07-27T10:00:00Z",
            "delivered_at": "2026-07-27T10:00:05Z",
            "read_at": "2026-07-27T10:00:10Z",
        }

    def _transport(self, transport: str, suffix: str) -> dict:
        return {
            "transport": transport,
            "connection_id": 11 if transport == "meta_cloud_api" else 12,
            "account_id": f"wa-{transport}",
            "phone_suffix": suffix,
            "binding": {
                "observed_state": "connected",
                "authentication_state": "linked",
                "listener_state": "active",
                "desired_generation": 4,
                "observed_generation": 4,
            },
            "inbound": {
                "provider_message_id": f"{transport}.inbound.message",
                "received_at": "2026-07-27T09:59:00Z",
                "stored": True,
                "idempotent_replay": True,
            },
            "outbound": self._direction(f"{transport}.outbound"),
            "restart": {
                "initiated_at": "2026-07-27T10:01:00Z",
                "reconnected_at": "2026-07-27T10:01:08Z",
                "credentials_persisted": True,
                "listener_active": True,
                "reconnected_without_reauthentication": True,
                "desired_generation": 4,
                "observed_generation": 4,
            },
            "media": {
                "inbound": {
                    "provider_message_id": f"{transport}.media.inbound",
                    "asset_id": 21,
                    "attachment_id": 31,
                    "scan_status": "clean",
                    "storage_status": "available",
                    "sha256": "c" * 64,
                    "byte_size": 1024,
                },
                "outbound": self._direction(f"{transport}.media.outbound"),
            },
        }

    def _observation(self) -> dict:
        return {
            "schema": "nexus.whatsapp-live-observation.v1",
            "candidate": {
                "source_sha": self.source_sha,
                "image_digest": self.image_digest,
            },
            "observed_at": "2026-07-27T10:05:00Z",
            "transports": {
                "meta_cloud_api": self._transport("meta_cloud_api", "1234"),
                "baileys_sidecar": self._transport("baileys_sidecar", "5678"),
            },
        }

    def test_dual_provider_media_evidence_is_signed_and_verifiable(self):
        evidence = MODULE.compile_evidence(
            self._observation(),
            expected_source_sha=self.source_sha,
            expected_image_digest=self.image_digest,
            signing_key=self.signing_key,
            require_media=True,
        )
        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(
            set(evidence["transports"]),
            {"meta_cloud_api", "baileys_sidecar"},
        )
        self.assertTrue(
            MODULE.verify_compiled_evidence(
                evidence,
                signing_key=self.signing_key,
            )
        )
        tampered = deepcopy(evidence)
        tampered["transports"]["baileys_sidecar"]["phone_suffix"] = "0000"
        self.assertFalse(
            MODULE.verify_compiled_evidence(
                tampered,
                signing_key=self.signing_key,
            )
        )

    def test_missing_transport_or_media_fails_closed(self):
        observation = self._observation()
        del observation["transports"]["baileys_sidecar"]
        with self.assertRaisesRegex(
            MODULE.EvidenceError,
            "dual_transport_observations_required",
        ):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )
        observation = self._observation()
        del observation["transports"]["meta_cloud_api"]["media"]
        with self.assertRaisesRegex(
            MODULE.EvidenceError,
            "meta_cloud_api_media_missing",
        ):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )

    def test_secrets_and_full_phone_numbers_are_rejected(self):
        observation = self._observation()
        observation["transports"]["meta_cloud_api"]["access_token"] = "secret"
        with self.assertRaisesRegex(MODULE.EvidenceError, "forbidden_evidence_field"):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )
        observation = self._observation()
        observation["operator_note"] = "called +15550001234"
        with self.assertRaisesRegex(MODULE.EvidenceError, "full_phone_number_forbidden"):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )

    def test_delivery_order_and_restart_proof_are_mandatory(self):
        observation = self._observation()
        observation["transports"]["baileys_sidecar"]["outbound"]["read_at"] = (
            "2026-07-27T09:59:59Z"
        )
        with self.assertRaisesRegex(MODULE.EvidenceError, "delivery_order_invalid"):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )
        observation = self._observation()
        observation["transports"]["meta_cloud_api"]["restart"][
            "reconnected_without_reauthentication"
        ] = False
        with self.assertRaisesRegex(
            MODULE.EvidenceError,
            "restart_reconnected_without_reauthentication_unproven",
        ):
            MODULE.compile_evidence(
                observation,
                expected_source_sha=self.source_sha,
                expected_image_digest=self.image_digest,
                signing_key=self.signing_key,
                require_media=True,
            )


if __name__ == "__main__":
    unittest.main()
