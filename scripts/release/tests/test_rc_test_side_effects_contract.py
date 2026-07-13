from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "rc_test_side_effects.py").read_text(encoding="utf-8")


class RCTestSideEffectsContractTests(unittest.TestCase):
    def test_durable_tool_execution_authorities_are_required(self) -> None:
        for marker in (
            '"tool_call_logs": 41',
            "tool_call_log_execution_count",
            "IN ('success', 'executed')",
            "runtime_decision_audits",
            "webchat_voice_ai_actions",
            "external_tool_execution_count",
        ):
            self.assertIn(marker, SOURCE)

    def test_customer_output_evidence_is_required(self) -> None:
        for marker in (
            "webchat_ai_customer_output_count",
            "provider_customer_output_count",
            "tts_provider_customer_output_count",
        ):
            self.assertIn(marker, SOURCE)


if __name__ == "__main__":
    unittest.main()
