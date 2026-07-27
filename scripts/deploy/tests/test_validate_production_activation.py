from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "validate_production_activation.py"
SPEC = importlib.util.spec_from_file_location(
    "validate_production_activation",
    MODULE_PATH,
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class WhatsAppProductionActivationTests(unittest.TestCase):
    source_sha = "a" * 40
    image_digest = "sha256:" + "b" * 64
    image = "ghcr.io/maximvonshaft/nexus_helpdesk@" + image_digest
    clamav_image = "docker.io/clamav/clamav@sha256:" + "c" * 64
    sidecar_image = "ghcr.io/maximvonshaft/nexus-whatsapp-sidecar@sha256:" + "d" * 64

    def _values(self) -> dict[str, str]:
        return {
            "PRODUCTION_PROFILE": "full",
            "CONTROLLED_IMAGE": self.image,
            "GIT_SHA": self.source_sha,
            "ACTIVATION_EVIDENCE_SOURCE_SHA": self.source_sha,
            "ACTIVATION_EVIDENCE_IMAGE_DIGEST": self.image_digest,
            "PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/production",
            "OUTBOUND_PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/outbound",
            "WHATSAPP_PRODUCTION_E2E_EVIDENCE_URL": "https://evidence.example/whatsapp",
            "PROVIDER_RUNTIME_ENABLED": "true",
            "PROVIDER_RUNTIME_TRAFFIC_MODE": "full",
            "PROVIDER_RUNTIME_KILL_SWITCH": "false",
            "PROVIDER_RUNTIME_CANARY_PERCENT": "100",
            "WEBCHAT_AI_ENABLED": "false",
            "WEBCHAT_HUMAN_CALL_ENABLED": "false",
            "WEBCHAT_LIVE_AI_VOICE_ENABLED": "false",
            "ENABLE_OUTBOUND_DISPATCH": "true",
            "OUTBOUND_PROVIDER": "native",
            "WHATSAPP_ENABLED": "true",
            "WHATSAPP_META_WEBHOOK_PUBLIC_URL": "https://support.example/api/integrations/whatsapp/meta/webhook",
            "WHATSAPP_SIDECAR_IMAGE": self.sidecar_image,
            "WHATSAPP_EMBEDDED_SIGNUP_ENABLED": "false",
            "WHATSAPP_MEDIA_ENABLED": "false",
            "WHATSAPP_MEDIA_SCANNER": "disabled",
            "COMPOSE_PROFILES": "whatsapp-baileys",
            "OPERATIONS_DISPATCH_MODE": "disabled",
            "OPERATIONS_DISPATCH_ADAPTER": "disabled",
        }

    def test_whatsapp_requires_capability_specific_evidence(self):
        values = self._values()
        values["WHATSAPP_PRODUCTION_E2E_EVIDENCE_URL"] = ""
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "activation_evidence_missing:whatsapp_production_e2e_evidence_url",
        ):
            MODULE.validate(values)

    def test_whatsapp_must_use_canonical_outbound_authority(self):
        values = self._values()
        values["OUTBOUND_PROVIDER"] = "disabled"
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "whatsapp_outbound_authority_invalid",
        ):
            MODULE.validate(values)

    def test_meta_only_activation_does_not_require_baileys(self):
        values = self._values()
        values["COMPOSE_PROFILES"] = ""
        values["WHATSAPP_SIDECAR_IMAGE"] = ""
        result = MODULE.validate(values)
        self.assertTrue(result["capabilities"]["whatsapp_meta"])
        self.assertFalse(result["capabilities"]["whatsapp_baileys"])

    def test_baileys_only_activation_does_not_require_meta(self):
        values = self._values()
        values["WHATSAPP_META_WEBHOOK_PUBLIC_URL"] = ""
        result = MODULE.validate(values)
        self.assertFalse(result["capabilities"]["whatsapp_meta"])
        self.assertTrue(result["capabilities"]["whatsapp_baileys"])

    def test_baileys_requires_digest_pinned_sidecar(self):
        values = self._values()
        values["WHATSAPP_META_WEBHOOK_PUBLIC_URL"] = ""
        values["WHATSAPP_SIDECAR_IMAGE"] = "ghcr.io/example/sidecar:latest"
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "configuration_digest_image_required:WHATSAPP_SIDECAR_IMAGE",
        ):
            MODULE.validate(values)

    def test_whatsapp_requires_at_least_one_transport(self):
        values = self._values()
        values["WHATSAPP_META_WEBHOOK_PUBLIC_URL"] = ""
        values["COMPOSE_PROFILES"] = ""
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "whatsapp_transport_missing",
        ):
            MODULE.validate(values)

    def test_embedded_signup_requires_meta_and_complete_assets(self):
        values = self._values()
        values.update(
            {
                "WHATSAPP_EMBEDDED_SIGNUP_ENABLED": "true",
                "WHATSAPP_META_APP_ID": "1234567890",
                "WHATSAPP_META_APP_SECRET_FILE": "/run/nexus/whatsapp_meta_app_secret",
                "WHATSAPP_META_CONFIGURATION_ID": "9876543210",
                "WHATSAPP_META_GRAPH_API_VERSION": "v23.0",
                "WHATSAPP_EMBEDDED_SIGNUP_ALLOWED_ORIGIN": "https://support.example",
            }
        )
        result = MODULE.validate(values)
        self.assertTrue(result["capabilities"]["whatsapp_embedded_signup"])
        values["WHATSAPP_META_WEBHOOK_PUBLIC_URL"] = ""
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "embedded_signup_requires_meta_transport",
        ):
            MODULE.validate(values)

    def test_media_requires_clamav_profile_and_digest_image(self):
        values = self._values()
        values.update(
            {
                "WHATSAPP_MEDIA_ENABLED": "true",
                "WHATSAPP_MEDIA_SCANNER": "clamav",
                "WHATSAPP_CLAMAV_HOST": "clamav-controlled",
                "WHATSAPP_CLAMAV_PORT": "3310",
                "WHATSAPP_CLAMAV_IMAGE": self.clamav_image,
                "COMPOSE_PROFILES": "whatsapp-baileys",
            }
        )
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "whatsapp_media_profile_missing",
        ):
            MODULE.validate(values)
        values["COMPOSE_PROFILES"] = "whatsapp-baileys,whatsapp-media"
        values["WHATSAPP_CLAMAV_IMAGE"] = "docker.io/clamav/clamav:latest"
        with self.assertRaisesRegex(
            MODULE.ActivationError,
            "configuration_digest_image_required:WHATSAPP_CLAMAV_IMAGE",
        ):
            MODULE.validate(values)

    def test_valid_dual_transport_media_activation_passes(self):
        values = self._values()
        values.update(
            {
                "WHATSAPP_MEDIA_ENABLED": "true",
                "WHATSAPP_MEDIA_SCANNER": "clamav",
                "WHATSAPP_CLAMAV_HOST": "clamav-controlled",
                "WHATSAPP_CLAMAV_PORT": "3310",
                "WHATSAPP_CLAMAV_TIMEOUT_SECONDS": "20",
                "WHATSAPP_MEDIA_MAX_TOTAL_BYTES": str(100 * 1024 * 1024),
                "WHATSAPP_CLAMAV_IMAGE": self.clamav_image,
                "COMPOSE_PROFILES": "whatsapp-baileys,whatsapp-media",
            }
        )
        result = MODULE.validate(values)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["capabilities"]["whatsapp"])
        self.assertTrue(result["capabilities"]["whatsapp_meta"])
        self.assertTrue(result["capabilities"]["whatsapp_baileys"])
        self.assertTrue(result["capabilities"]["whatsapp_media"])
        self.assertEqual(
            result["evidence"]["whatsapp"],
            "https://evidence.example/whatsapp",
        )


if __name__ == "__main__":
    unittest.main()
