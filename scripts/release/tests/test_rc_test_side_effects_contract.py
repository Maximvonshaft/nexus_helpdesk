from __future__ import annotations

import unittest
from pathlib import Path


RELEASE_DIR = Path(__file__).resolve().parents[1]
CHECKER = RELEASE_DIR / "rc_test_side_effects.py"
BASE_CHECKER = RELEASE_DIR / "rc_test_side_effects_base.py"


class RCTestSideEffectsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.wrapper = CHECKER.read_text(encoding="utf-8")
        cls.base = BASE_CHECKER.read_text(encoding="utf-8")
        cls.source = cls.wrapper + "\n" + cls.base

    def test_external_tool_execution_is_proven_from_all_durable_authorities(self) -> None:
        for marker in (
            "runtime_decision_audits",
            "decision_json",
            "tool_actions",
            "executed",
            "webchat_voice_ai_actions",
            "tool_call_logs",
            "tool_call_log_execution_count",
            "IN ('success', 'executed')",
            "external_tool_execution_count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)
        self.assertIn('MISSING_TABLE_EXIT_CODES["tool_call_logs"] = 41', self.wrapper)

    def test_failed_or_denied_tool_audits_are_not_classified_as_execution(self) -> None:
        self.assertIn("lower(BTRIM(COALESCE(status, '')))", self.wrapper)

    def test_tts_provider_customer_output_is_proven_from_durable_voice_rows(self) -> None:
        for marker in (
            "webchat_voice_ai_turns",
            "webchat_voice_ai_actions",
            "ai_response_text_redacted",
            "provider",
            "tts_provider",
            "tts_provider_customer_output_count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

    def test_queued_ai_turn_is_diagnostic_not_customer_output(self) -> None:
        self.assertIn("FORBIDDEN_SEMANTIC_COUNTS", self.base)
        self.assertIn("webchat_ai_queued_turn_count", self.base)
        self.assertIn("webchat_ai_customer_output_count", self.base)
        self.assertIn("reply_message_id IS NOT NULL", self.base)
        self.assertNotIn(
            'webchat_ai_turn_count = _count(db, "SELECT COUNT(*) FROM webchat_ai_turns")',
            self.base,
        )


if __name__ == "__main__":
    unittest.main()
