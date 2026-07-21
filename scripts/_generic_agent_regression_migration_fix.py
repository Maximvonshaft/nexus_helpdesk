from __future__ import annotations

import re
from pathlib import Path


def read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    Path(path).write_text(text.rstrip() + "\n", encoding="utf-8")


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# The admin route must manage the same agent_turn scenario used by the runtime
# and migration. Keeping a webchat_runtime_reply row would recreate a dead route.
admin_path = "backend/app/api/admin_provider_runtime.py"
admin = read(admin_path)
admin = admin.replace("_WEBCHAT_RUNTIME_SCENARIO", "_AGENT_TURN_SCENARIO")
admin = admin.replace('"webchat_runtime_reply"', '"agent_turn"')
admin = admin.replace("class WebchatRuntimeRoutingUpdate", "class AgentTurnRoutingUpdate")
admin = admin.replace("WebchatRuntimeRoutingUpdate,", "AgentTurnRoutingUpdate,")
admin = admin.replace('@router.patch("/routing/webchat-runtime")', '@router.patch("/routing/agent-turn")')
admin = admin.replace("def update_webchat_runtime_routing(", "def update_agent_turn_routing(")
write(admin_path, admin)

admin_test_path = "backend/tests/test_admin_provider_runtime_routing_api.py"
admin_test = read(admin_test_path)
admin_test = admin_test.replace(
    "WebchatRuntimeRoutingUpdate, update_webchat_runtime_routing",
    "AgentTurnRoutingUpdate, update_agent_turn_routing",
)
admin_test = admin_test.replace(
    "test_admin_provider_runtime_routing_api_inserts_safe_default",
    "test_admin_agent_turn_routing_api_inserts_safe_default",
)
admin_test = admin_test.replace("update_webchat_runtime_routing(", "update_agent_turn_routing(")
admin_test = admin_test.replace("WebchatRuntimeRoutingUpdate(", "AgentTurnRoutingUpdate(")
admin_test = admin_test.replace('assert rule["scenario"] == "webchat_runtime_reply"', 'assert rule["scenario"] == "agent_turn"')
admin_test = admin_test.replace('assert rule["output_contract"] == "nexus.webchat_runtime_reply"', 'assert rule["output_contract"] == "nexus.agent_turn.v1"')
write(admin_test_path, admin_test)


# Persona selection metadata is operational provenance, not business hardcoding.
# Preserve it alongside the generic identity projection for audit and diagnostics.
context_path = "backend/app/services/ai_runtime_context.py"
context = read(context_path)
old_persona_return = '    return {"match_rank": match_rank, "identity_context": identity}\n'
new_persona_return = '''    return {
        "profile_key": getattr(profile, "profile_key", None),
        "profile_name": getattr(profile, "name", None),
        "published_version": getattr(profile, "published_version", None),
        "match_rank": match_rank,
        "identity_context": identity,
    }
'''
context = replace_once(
    context,
    old_persona_return,
    new_persona_return,
    label="persona provenance projection",
)
write(context_path, context)


# Replace the retired domain answer-policy tests with the actual generic context
# contract: conversation history is context only and domain truth comes from Skills
# plus committed Tool observations.
runtime_guard_test = '''from __future__ import annotations

from types import SimpleNamespace

from app.services.ai_runtime_context import (
    build_runtime_context_guard,
    build_structured_recent_context,
    sanitize_runtime_context,
)


def _row(row_id: int, direction: str, text: str):
    return SimpleNamespace(
        id=row_id,
        direction=direction,
        body=text,
        body_text=None,
    )


def test_recent_context_projects_only_generic_conversation_roles():
    context = build_structured_recent_context(
        history_rows=[
            _row(1, "visitor", "Please check the current record."),
            _row(2, "agent", "I will use an approved Tool when required."),
        ]
    )

    assert context == [
        {
            "role": "customer",
            "text": "Please check the current record.",
            "message_id": 1,
        },
        {
            "role": "assistant",
            "text": "I will use an approved Tool when required.",
            "message_id": 2,
        },
    ]


def test_runtime_context_guard_is_domain_neutral():
    recent = build_structured_recent_context(
        history_rows=[
            _row(1, "visitor", "I need an update."),
            _row(2, "agent", "I need to inspect the approved tools."),
        ]
    )
    guard = build_runtime_context_guard(
        structured_recent_context=recent,
        tracking_intent_detected=True,
        tracking_fact_evidence_present=False,
        kb_hits_count=99,
    )

    assert guard == {
        "context_guard": {
            "recent_context_count": 2,
            "customer_message_count": 1,
            "assistant_message_count": 1,
            "business_truth_policy": "owned_by_skills_and_tool_observations",
        }
    }
    serialized = str(guard)
    assert "answer_policy" not in serialized
    assert "tracking_status_requires_tool_fact" not in serialized
    assert "kb_cannot_answer_live_tracking_status" not in serialized


def test_context_sanitizer_removes_credentials_without_domain_branching():
    value = sanitize_runtime_context(
        {
            "authorization": "Bearer should-not-survive",
            "nested": {
                "token": "secret",
                "message": "normal customer context",
            },
        }
    )

    assert value == {"nested": {"message": "normal customer context"}}
'''
write("backend/tests/test_runtime_context_guard.py", runtime_guard_test)


# Keep Knowledge CRUD/indexing tests. Replace only the retired behavior that
# injected Knowledge/Tracking into the model before the Agent could select a Tool.
knowledge_test_path = "backend/tests/test_knowledge_runtime_context.py"
knowledge_tests = read(knowledge_test_path)
marker = "def test_runtime_context_includes_published_persona_and_safe_knowledge"
match = re.search(rf"^{re.escape(marker)}\(", knowledge_tests, flags=re.MULTILINE)
if match is None:
    raise SystemExit("knowledge runtime legacy test marker not found")
knowledge_tests = knowledge_tests[: match.start()].rstrip()
knowledge_tests += '''


def test_runtime_context_projects_persona_and_channel_without_pre_model_retrieval(db_session):
    admin = _user(db_session)
    profile = persona_service.create_profile(
        db_session,
        PersonaProfileCreate(
            profile_key="default.website.en",
            name="Default Website English",
            channel="website",
            language="en",
            draft_summary="Use approved Skills and Tools.",
            draft_content_json={"tone": "concise"},
        ),
        admin,
    )
    persona_service.publish_profile(db_session, profile, admin, notes="publish")
    item = knowledge_service.create_item(
        db_session,
        _knowledge_payload(item_key="runtime.address", channel="website"),
        admin,
    )
    knowledge_service.publish_item(db_session, item, admin, notes="publish")

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Can I change my delivery address?",
    )

    assert context["context_version"] == "nexus.agent_context.v1"
    assert context["persona_context"]["profile_key"] == "default.website.en"
    assert context["persona_context"]["identity_context"]
    assert context["channel_context"]["channel"] == "website"
    assert "knowledge_context" not in context
    assert "rag_trace" not in context
    assert "safety_policy" not in context
    assert "conversation_state" not in context

    hits, total = search_published_chunks(
        db_session,
        q="change delivery address",
        channel="website",
        audience_scope="customer",
        limit=5,
    )
    assert total == 1
    assert [hit.item_key for hit in hits] == ["runtime.address"]


def test_runtime_context_uses_effective_country_in_generic_channel_context(db_session):
    ch_market = Market(
        code="CH",
        name="Switzerland",
        country_code="CH",
        is_active=True,
    )
    db_session.add(ch_market)
    db_session.flush()
    ticket = Ticket(
        ticket_no="T-CH-GENERIC-CONTEXT",
        title="Swiss customer",
        description="Swiss customer",
        source=TicketSource.user_message,
        source_channel=SourceChannel.web_chat,
        priority=TicketPriority.medium,
        status=TicketStatus.new,
        resolution_category=ResolutionCategory.none,
        market_id=ch_market.id,
        conversation_state=ConversationState.ai_active,
    )
    db_session.add(ticket)
    db_session.flush()

    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Please help with the current request.",
        market_id=ch_market.id,
        ticket=ticket,
        channel_payload={"order_destination_country": "CH"},
    )

    channel = context["channel_context"]
    assert channel["effective_country"] == "CH"
    assert channel["country_source"] == "order_destination_country"
    assert "knowledge_context" not in context


def test_runtime_context_ignores_retired_tracking_prefetch_arguments(db_session):
    context = build_webchat_runtime_context(
        db_session,
        tenant_key="default",
        channel_key="website",
        language="en",
        body="Reference CH1200000011425",
        tracking_number="CH1200000011425",
        tracking_fact_evidence_present=True,
    )

    serialized = str(context)
    assert "knowledge_context" not in context
    assert "conversation_state" not in context
    assert "tracking_fact_evidence_present" not in serialized
    assert "locked_facts" not in serialized
'''
write(knowledge_test_path, knowledge_tests)


# Generic result envelopes no longer carry the retired tracking-prefetch fields.
for path in (
    "backend/tests/test_webchat_round_b.py",
    "backend/tests/test_webchat_handoff_control.py",
    "backend/tests/test_webchat_osr_audit_integration.py",
):
    source = read(path)
    source = re.sub(r"^\s*tracking_number=None,\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^\s*ticket_creation_queued=False,\n", "", source, flags=re.MULTILINE)
    source = re.sub(r"^\s*rag_trace=rag_trace,\n", "", source, flags=re.MULTILINE)
    write(path, source)

# Round-B's pre-model lookup monkeypatch encoded the retired orchestration.
round_b_path = "backend/tests/test_webchat_round_b.py"
round_b = read(round_b_path)
round_b = round_b.replace(
    "from app.services.tracking_fact_schema import TrackingFactResult\n",
    "",
)
round_b = re.sub(
    r"\n\s*monkeypatch\.setattr\(\n\s*conversation_ai_service,\n\s*'lookup_tracking_fact',\n\s*lambda \*\*_kwargs: TrackingFactResult\([\s\S]*?\n\s*\),\n\s*\)\n",
    "\n",
    round_b,
    count=1,
)
write(round_b_path, round_b)


# Make retired runtime test semantics a permanent failure if reintroduced.
residue_path = "scripts/ci/check_agent_runtime_residue.py"
residue = read(residue_path)
if '"webchat_runtime_reply"' not in residue:
    residue = residue.replace(
        '    "nexus.webchat_runtime_reply",\n',
        '    "nexus.webchat_runtime_reply",\n'
        '    "webchat_runtime_reply",\n',
    )
write(residue_path, residue)

assert '"agent_turn"' in read(admin_path)
assert "knowledge_context" not in read("backend/tests/test_runtime_context_guard.py")
assert "test_runtime_context_expands_tracking_no_evidence" not in read(knowledge_test_path)
