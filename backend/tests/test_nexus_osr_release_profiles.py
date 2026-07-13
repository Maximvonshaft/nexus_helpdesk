from __future__ import annotations

import re
import unittest

from app.services.nexus_osr import release_profiles as profiles


EXPECTED_CAPABILITIES = {
    "database",
    "migration_identity",
    "storage",
    "runtime_signing",
    "tenant_authority",
    "tracking_truth",
    "knowledge_readiness",
    "escalation_policy",
    "worker_heartbeat",
    "worker_progress",
    "queue_health",
    "provider_runtime",
    "provider_canary_authority",
    "dispatch_execution",
    "dispatch_acknowledgement",
    "external_writes",
    "observability",
    "recovery",
    "resilience",
    "ai_runtime_contract",
    "rag_v2",
    "rag_sync_freshness",
    "runtime_deployment_identity",
    "voice_runtime",
}


def _ready_evidence(profile_name: str) -> dict[str, str]:
    profile = profiles.get_profile(profile_name)
    return {
        capability.value: (
            profiles.CapabilityState.DISABLED.value
            if requirement is profiles.Requirement.FORBIDDEN
            else profiles.CapabilityState.READY.value
        )
        for capability, requirement in profile.capabilities.items()
    }


class ReleaseProfileRegistryTests(unittest.TestCase):
    def test_schema_and_profile_names_are_versioned_and_exact(self) -> None:
        self.assertEqual(profiles.SCHEMA_VERSION, "nexus.osr.release-profile.v1")
        self.assertEqual(
            {item.value for item in profiles.ProfileName},
            {"development", "shadow", "pilot", "full_osr"},
        )
        self.assertEqual({item.value for item in profiles.Capability}, EXPECTED_CAPABILITIES)

    def test_every_profile_declares_every_capability_once(self) -> None:
        for profile_name in profiles.ProfileName:
            profile = profiles.get_profile(profile_name)
            self.assertEqual(profile.schema_version, profiles.SCHEMA_VERSION)
            self.assertEqual(set(profile.capabilities), set(profiles.Capability))
            self.assertEqual(len(profile.capabilities), len(profiles.Capability))

    def test_full_osr_requires_every_capability(self) -> None:
        profile = profiles.get_profile("full_osr")
        self.assertTrue(all(value is profiles.Requirement.REQUIRED for value in profile.capabilities.values()))

    def test_shadow_forbids_external_writes_and_requires_read_only_authority(self) -> None:
        profile = profiles.get_profile("shadow")
        self.assertIs(profile.capabilities[profiles.Capability.EXTERNAL_WRITES], profiles.Requirement.FORBIDDEN)
        for capability in (
            profiles.Capability.TRACKING_TRUTH,
            profiles.Capability.KNOWLEDGE_READINESS,
            profiles.Capability.WORKER_HEARTBEAT,
            profiles.Capability.WORKER_PROGRESS,
            profiles.Capability.QUEUE_HEALTH,
            profiles.Capability.OBSERVABILITY,
            profiles.Capability.AI_RUNTIME_CONTRACT,
        ):
            self.assertIs(profile.capabilities[capability], profiles.Requirement.REQUIRED)

    def test_pilot_requires_tenant_canary_dispatch_recovery_and_external_writes(self) -> None:
        profile = profiles.get_profile("pilot")
        for capability in (
            profiles.Capability.TENANT_AUTHORITY,
            profiles.Capability.PROVIDER_CANARY_AUTHORITY,
            profiles.Capability.DISPATCH_EXECUTION,
            profiles.Capability.DISPATCH_ACKNOWLEDGEMENT,
            profiles.Capability.EXTERNAL_WRITES,
            profiles.Capability.RECOVERY,
            profiles.Capability.RESILIENCE,
        ):
            self.assertIs(profile.capabilities[capability], profiles.Requirement.REQUIRED)

    def test_profile_registry_is_immutable(self) -> None:
        profile = profiles.get_profile("development")
        with self.assertRaises(TypeError):
            profile.capabilities[profiles.Capability.DATABASE] = profiles.Requirement.OPTIONAL

    def test_unknown_profile_fails_closed(self) -> None:
        with self.assertRaisesRegex(profiles.ReleaseProfileContractError, "release_profile_unknown"):
            profiles.get_profile("does-not-exist")


class ReleaseProfileEvaluationTests(unittest.TestCase):
    def test_ready_baseline_is_ready_for_every_profile(self) -> None:
        for profile_name in profiles.ProfileName:
            result = profiles.evaluate_release_profile(profile_name, _ready_evidence(profile_name.value))
            self.assertIs(result.status, profiles.ReadinessStatus.READY)
            self.assertEqual(result.reason_codes, ())
            self.assertEqual(result.profile, profile_name.value)
            self.assertEqual(result.schema_version, profiles.SCHEMA_VERSION)

    def test_required_missing_disabled_and_failed_are_not_ready(self) -> None:
        for state, suffix in (
            (None, "required_missing"),
            ("disabled", "required_disabled"),
            ("failed", "required_failed"),
        ):
            evidence = _ready_evidence("development")
            if state is None:
                evidence.pop("database")
            else:
                evidence["database"] = state
            result = profiles.evaluate_release_profile("development", evidence)
            self.assertIs(result.status, profiles.ReadinessStatus.NOT_READY)
            self.assertIn(f"database_{suffix}", result.reason_codes)

    def test_required_degraded_is_degraded(self) -> None:
        evidence = _ready_evidence("development")
        evidence["database"] = "degraded"
        result = profiles.evaluate_release_profile("development", evidence)
        self.assertIs(result.status, profiles.ReadinessStatus.DEGRADED)
        self.assertEqual(result.reason_codes, ("database_required_degraded",))

    def test_optional_missing_or_disabled_is_acceptable(self) -> None:
        evidence = _ready_evidence("development")
        evidence.pop("knowledge_readiness")
        evidence["tracking_truth"] = "disabled"
        result = profiles.evaluate_release_profile("development", evidence)
        self.assertIs(result.status, profiles.ReadinessStatus.READY)
        self.assertEqual(result.reason_codes, ())

    def test_optional_degraded_or_failed_is_degraded(self) -> None:
        evidence = _ready_evidence("development")
        evidence["knowledge_readiness"] = "degraded"
        evidence["tracking_truth"] = "failed"
        result = profiles.evaluate_release_profile("development", evidence)
        self.assertIs(result.status, profiles.ReadinessStatus.DEGRADED)
        self.assertEqual(
            result.reason_codes,
            ("knowledge_readiness_optional_degraded", "tracking_truth_optional_failed"),
        )

    def test_forbidden_enabled_states_are_not_ready(self) -> None:
        for state in ("ready", "degraded", "failed"):
            evidence = _ready_evidence("shadow")
            evidence["external_writes"] = state
            result = profiles.evaluate_release_profile("shadow", evidence)
            self.assertIs(result.status, profiles.ReadinessStatus.NOT_READY)
            self.assertEqual(result.reason_codes, ("external_writes_forbidden_enabled",))

    def test_not_ready_precedes_degraded(self) -> None:
        evidence = _ready_evidence("development")
        evidence["database"] = "failed"
        evidence["knowledge_readiness"] = "degraded"
        result = profiles.evaluate_release_profile("development", evidence)
        self.assertIs(result.status, profiles.ReadinessStatus.NOT_READY)
        self.assertEqual(
            result.reason_codes,
            ("database_required_failed", "knowledge_readiness_optional_degraded"),
        )

    def test_reason_codes_are_unique_sorted_and_bounded(self) -> None:
        evidence = _ready_evidence("development")
        evidence["database"] = "failed"
        evidence["migration_identity"] = "failed"
        result = profiles.evaluate_release_profile("development", evidence)
        self.assertEqual(result.reason_codes, tuple(sorted(set(result.reason_codes))))
        self.assertTrue(all(re.fullmatch(r"[a-z0-9_]{3,96}", item) for item in result.reason_codes))

    def test_evidence_entry_limit_fails_closed_before_unknown_keys(self) -> None:
        evidence = {f"unknown_{index}": "ready" for index in range(65)}
        with self.assertRaisesRegex(profiles.ReleaseProfileContractError, "release_evidence_too_large"):
            profiles.evaluate_release_profile("development", evidence)

    def test_unknown_capability_and_state_fail_closed(self) -> None:
        evidence = _ready_evidence("development")
        evidence["invented_capability"] = "ready"
        with self.assertRaisesRegex(profiles.ReleaseProfileContractError, "release_capability_unknown"):
            profiles.evaluate_release_profile("development", evidence)
        evidence = _ready_evidence("development")
        evidence["database"] = "maybe"
        with self.assertRaisesRegex(profiles.ReleaseProfileContractError, "release_capability_state_invalid"):
            profiles.evaluate_release_profile("development", evidence)

    def test_result_serialization_is_bounded_and_contains_no_input_details(self) -> None:
        evidence = _ready_evidence("development")
        evidence["database"] = "failed"
        result = profiles.evaluate_release_profile("development", evidence)
        payload = result.as_dict()
        self.assertEqual(set(payload), {"schema_version", "profile", "status", "reason_codes"})
        self.assertEqual(payload["status"], "not_ready")
        self.assertNotIn("details", payload)
        self.assertNotIn("evidence", payload)


class SafeConfigurationFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_lowercase_sha256_and_mapping_order_independent(self) -> None:
        left = {"profile": "shadow", "limits": {"queue": 5, "age": 60}}
        right = {"limits": {"age": 60, "queue": 5}, "profile": "shadow"}
        left_digest = profiles.safe_configuration_fingerprint(left)
        right_digest = profiles.safe_configuration_fingerprint(right)
        self.assertRegex(left_digest, r"^[a-f0-9]{64}$")
        self.assertEqual(left_digest, right_digest)

    def test_secret_key_values_are_redacted_before_hashing(self) -> None:
        first = {
            "profile": "shadow",
            "provider_token": "first-secret-value",
            "nested": {
                "ApiKey": "alpha",
                "APIKey": "allcaps-alpha",
                "clientAPIKey": "client-alpha",
                "secret_key": "secret-alpha",
                "SecretKey": "secret-camel-alpha",
                "jwtSecretKey": "jwt-secret-alpha",
                "password": "one",
                "clientPrivateKey": "private-one",
                "api_keys": ["api-one", "api-two"],
                "privateKeys": ["private-a", "private-b"],
                "access_keys": ["access-a"],
                "signing_keys": ["signing-a"],
                "secret_keys": ["secret-a"],
            },
        }
        second = {
            "profile": "shadow",
            "provider_token": "second-secret-value",
            "nested": {
                "ApiKey": "beta",
                "APIKey": "allcaps-beta",
                "clientAPIKey": "client-beta",
                "secret_key": "secret-beta",
                "SecretKey": "secret-camel-beta",
                "jwtSecretKey": "jwt-secret-beta",
                "password": "two",
                "clientPrivateKey": "private-two",
                "api_keys": ["rotated-api-one", "rotated-api-two"],
                "privateKeys": ["rotated-private-a", "rotated-private-b"],
                "access_keys": ["rotated-access-a"],
                "signing_keys": ["rotated-signing-a"],
                "secret_keys": ["rotated-secret-a"],
            },
        }
        self.assertEqual(
            profiles.safe_configuration_fingerprint(first),
            profiles.safe_configuration_fingerprint(second),
        )

    def test_non_secret_token_count_changes_are_not_redacted(self) -> None:
        first = {"profile": "shadow", "max_tokens": 128, "provider_token": "alpha"}
        second = {"profile": "shadow", "max_tokens": 256, "provider_token": "beta"}
        self.assertNotEqual(
            profiles.safe_configuration_fingerprint(first),
            profiles.safe_configuration_fingerprint(second),
        )

    def test_non_secret_configuration_changes_change_hash(self) -> None:
        first = {"profile": "shadow", "queue_limit": 5}
        second = {"profile": "pilot", "queue_limit": 5}
        self.assertNotEqual(
            profiles.safe_configuration_fingerprint(first),
            profiles.safe_configuration_fingerprint(second),
        )

    def test_sensitive_values_are_shape_validated_before_redaction(self) -> None:
        invalid_values = (
            {"secret_key": {1, 2}},
            {"password": "x" * 513},
            {"jwtSecretKey": {"a": {"b": {"c": {"d": "too-deep"}}}}},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(profiles.ReleaseProfileContractError):
                    profiles.safe_configuration_fingerprint(value)

    def test_numeric_and_key_bounds_fail_closed(self) -> None:
        invalid_values = (
            {"nan": float("nan")},
            {"infinity": float("inf")},
            {"integer": 10**18 + 1},
            {"": "empty-key"},
            {1: "non-string-key"},
            {"x" * 129: "oversized-key"},
        )
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(profiles.ReleaseProfileContractError):
                    profiles.safe_configuration_fingerprint(value)

    def test_non_mapping_root_fails_closed(self) -> None:
        with self.assertRaisesRegex(profiles.ReleaseProfileContractError, "release_configuration_root_invalid"):
            profiles.safe_configuration_fingerprint([("profile", "shadow")])

    def test_unsupported_or_excessive_configuration_fails_closed(self) -> None:
        invalid_values = (
            {"unsupported": {1, 2}},
            {"api_key": {1, 2}},
            {f"key_{index}": index for index in range(65)},
            {"items": list(range(65))},
            {"value": "x" * 513},
            {"password": "x" * 513},
            {"a": {"b": {"c": {"d": {"e": "too-deep"}}}}},
            {"secret": {"a": {"b": {"c": {"d": "too-deep"}}}}},
        )
        for value in invalid_values:
            with self.subTest(value_type=type(next(iter(value.values()))).__name__):
                with self.assertRaises(profiles.ReleaseProfileContractError):
                    profiles.safe_configuration_fingerprint(value)


if __name__ == "__main__":
    unittest.main()
