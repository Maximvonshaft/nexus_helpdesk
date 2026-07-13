from __future__ import annotations

import unittest
from pathlib import Path


CHECKER = Path(__file__).resolve().parents[1] / "rc_test_side_effects.py"


class RCTestSideEffectsContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = CHECKER.read_text(encoding="utf-8")

    def test_external_tool_execution_is_proven_from_durable_runtime_decisions(self) -> None:
        for marker in (
            "runtime_decision_audits",
            "decision_json",
            "tool_actions",
            "executed",
            "external_tool_execution_count",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.source)

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
        # A public message deliberately creates one queued WebchatAITurn before
        # the disabled AI worker observes the job. That durable placeholder is
        # not Provider execution and must not fail the zero-output gate.
        self.assertIn("FORBIDDEN_SEMANTIC_COUNTS", self.source)
        self.assertIn("webchat_ai_queued_turn_count", self.source)
        self.assertIn("webchat_ai_customer_output_count", self.source)
        self.assertIn("reply_message_id IS NOT NULL", self.source)
        self.assertNotIn(
            'webchat_ai_turn_count = _count(db, "SELECT COUNT(*) FROM webchat_ai_turns")',
            self.source,
        )


if __name__ == "__main__":
    unittest.main()
