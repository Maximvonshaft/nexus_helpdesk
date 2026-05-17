from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace

os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/webchat_fast_stream_tracking_facts.db")
os.environ.setdefault("WEBCHAT_FAST_AI_ENABLED", "false")

from app.services import webchat_fast_stream_service as service
from app.services.webchat_fast_stream_service import StreamBeginOutcome, stream_webchat_fast_reply_events
from app.services.webchat_openclaw_stream_adapter import Completed


def _valid_final_text() -> str:
    return json.dumps(
        {
            "reply": "Thanks. I can help with this tracking status.",
            "intent": "tracking",
            "tracking_number": "PK120053679836",
            "handoff_required": False,
            "handoff_reason": None,
            "recommended_agent_action": None,
        },
        separators=(",", ":"),
    )


async def _collect_events(**kwargs) -> list[str]:
    return [item async for item in stream_webchat_fast_reply_events(**kwargs)]


def _base_kwargs() -> dict:
    return {
        "begin": StreamBeginOutcome(status="owner", request_hash="hash", row_id=123),
        "tenant_key": "default",
        "channel_key": "website",
        "session_id": "stream-tracking-fact-session",
        "client_message_id": "stream-tracking-fact-message",
        "body": "Where is PK120053679836?",
        "recent_context": [],
        "visitor": None,
        "request_id": "pytest-request",
        "settings": SimpleNamespace(openclaw_responses_agent_id="webchat-fast"),
        "routing_context": None,
    }


def test_stream_injects_tracking_fact_summary_when_evidence_present(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_call_stream(**kwargs):
        seen["input_text"] = kwargs["input_text"]
        yield Completed(full_text=_valid_final_text())

    monkeypatch.setattr(service.openclaw_client, "call_openclaw_responses_stream", fake_call_stream)
    monkeypatch.setattr(service, "_persist_stream_result", lambda **kwargs: None)
    monkeypatch.setattr(service, "_mark_done", lambda *args, **kwargs: None)

    events = asyncio.run(
        _collect_events(
            **_base_kwargs(),
            tracking_fact_summary="Verified tracking fact: package is out for delivery.",
            tracking_fact_metadata={"tool_status": "success"},
            tracking_fact_evidence_present=True,
        )
    )

    assert "Verified tracking fact: package is out for delivery." in seen["input_text"]
    assert any("event: final" in item for item in events)


def test_stream_does_not_inject_tracking_fact_summary_without_evidence(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_call_stream(**kwargs):
        seen["input_text"] = kwargs["input_text"]
        yield Completed(full_text=_valid_final_text())

    monkeypatch.setattr(service.openclaw_client, "call_openclaw_responses_stream", fake_call_stream)
    monkeypatch.setattr(service, "_persist_stream_result", lambda **kwargs: None)
    monkeypatch.setattr(service, "_mark_done", lambda *args, **kwargs: None)

    events = asyncio.run(
        _collect_events(
            **_base_kwargs(),
            tracking_fact_summary="This summary must not be injected without evidence.",
            tracking_fact_metadata={"tool_status": "error"},
            tracking_fact_evidence_present=False,
        )
    )

    assert "This summary must not be injected without evidence." not in seen["input_text"]
    assert any("event: final" in item for item in events)
