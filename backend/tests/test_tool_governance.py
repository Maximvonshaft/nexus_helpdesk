from __future__ import annotations

from app.services import tool_governance


class BrokenSession:
    def query(self, *args, **kwargs):
        raise RuntimeError("schema not migrated")

    def add(self, *args, **kwargs):
        raise RuntimeError("should not be reached")

    def flush(self):
        raise RuntimeError("should not be reached")

    def rollback(self):
        self.rolled_back = True


def test_classify_tool_type_read_write_and_system():
    assert tool_governance.classify_tool_type("conversations_list") == "read_only"
    assert tool_governance.classify_tool_type("messages_read") == "read_only"
    assert tool_governance.classify_tool_type("messages_send") == "external_send"
    assert tool_governance.classify_tool_type("openclaw_bridge.ai_reply") == "system"
    assert tool_governance.classify_tool_type("unknown_future_tool") == "read_only"


def test_safe_summary_redacts_sensitive_and_text_payloads():
    summary = tool_governance.summarize_input_safe(
        {
            "token": "super-secret-token",
            "prompt": "internal prompt must not be stored verbatim",
            "text": "full customer message must not be stored verbatim",
            "limit": 5,
            "status": "ok",
        }
    )

    assert "super-secret-token" not in summary
    assert "internal prompt must not be stored verbatim" not in summary
    assert "full customer message must not be stored verbatim" not in summary
    assert "sha256_prefix" in summary
    assert "limit" in summary


def test_record_tool_call_is_audit_only_and_does_not_raise_on_db_failure(monkeypatch):
    recorded_metrics = []
    monkeypatch.setattr(
        tool_governance,
        "record_tool_call_metric",
        lambda **payload: recorded_metrics.append(payload),
    )

    tool_governance.record_tool_call(
        tool_name="messages_send",
        input_payload={"text": "do not store full outbound message", "session_key": "abc123"},
        output_payload={"ok": True},
        status="success",
        elapsed_ms=12,
        db=BrokenSession(),
    )

    assert recorded_metrics
    assert recorded_metrics[0]["tool_type"] == "external_send"
    assert recorded_metrics[0]["status"] == "success"
