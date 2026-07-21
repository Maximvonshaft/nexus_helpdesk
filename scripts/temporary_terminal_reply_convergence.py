#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SERVICE = ROOT / "backend/app/services/agent_runtime/service.py"
SHARED = ROOT / "backend/app/services/agent_runtime/terminal_reply.py"
RUNTIME_WRAPPER = ROOT / "backend/app/services/webchat_runtime_ai_service.py"
TICKET_SERVICE = ROOT / "backend/app/services/webchat_ai_service.py"
TICKETLESS_SERVICE = ROOT / "backend/app/services/conversation_ai_service.py"
TURN_TESTS = ROOT / "backend/tests/test_webchat_ai_turn_runtime.py"
RESIDUE = ROOT / "scripts/ci/check_agent_runtime_residue.py"
ARCHITECTURE = ROOT / "docs/architecture/generic-agent-skill-runtime.md"
WORKFLOW = ROOT / ".github/workflows/temporary-terminal-reply-convergence.yml"
SELF = Path(__file__).resolve()


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_count(text: str, old: str, new: str, *, count: int, label: str) -> str:
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(f"{label}: expected {count} matches, found {actual}")
    return text.replace(old, new)


def write_shared_authority() -> None:
    SHARED.write_text(
        '''from __future__ import annotations\n\n\ndef customer_visible_fallback(language: str | None, body: str | None) -> str:\n    """Return the sole deterministic customer-visible terminal fallback."""\n\n    hint = str(language or "").strip().lower()\n    customer_body = str(body or "")\n    if hint.startswith("zh") or any("\\u4e00" <= char <= "\\u9fff" for char in customer_body):\n        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"\n    if hint.startswith("de"):\n        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."\n    return "Sorry, I could not complete that request right now. Please try again or ask for human support."\n''',
        encoding="utf-8",
    )


def patch_agent_service() -> None:
    text = AGENT_SERVICE.read_text(encoding="utf-8")
    if "from .terminal_reply import customer_visible_fallback" not in text:
        text = replace_once(
            text,
            "from .skill_registry import prompt_skill_catalog\n",
            "from .skill_registry import prompt_skill_catalog\nfrom .terminal_reply import customer_visible_fallback\n",
            label="agent fallback import",
        )
    text = replace_once(
        text,
        "    reply = _localized_fallback(request.language, request.body)\n",
        "    reply = customer_visible_fallback(request.language, request.body)\n",
        label="agent fallback call",
    )
    text = replace_once(
        text,
        '''\n\ndef _localized_fallback(language: str | None, body: str) -> str:\n    hint = str(language or "").strip().lower()\n    if hint == "zh" or any("\\u4e00" <= char <= "\\u9fff" for char in body):\n        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"\n    if hint == "de":\n        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."\n    return "Sorry, I could not complete that request right now. Please try again or ask for human support."\n''',
        "",
        label="retired agent fallback",
    )
    AGENT_SERVICE.write_text(text, encoding="utf-8")


def patch_runtime_wrapper() -> None:
    text = RUNTIME_WRAPPER.read_text(encoding="utf-8")
    if "from .agent_runtime.terminal_reply import customer_visible_fallback" not in text:
        text = replace_once(
            text,
            "from .agent_runtime.service import run_agent\n",
            "from .agent_runtime.service import run_agent\nfrom .agent_runtime.terminal_reply import customer_visible_fallback\n",
            label="runtime wrapper fallback import",
        )
    text = replace_once(
        text,
        "            reply=_fallback(language, body),\n",
        "            reply=customer_visible_fallback(language, body),\n",
        label="runtime wrapper fallback call",
    )
    text = replace_once(
        text,
        '''\n\ndef _fallback(language: str | None, body: str) -> str:\n    hint = str(language or "").strip().lower()\n    if hint == "zh" or any("\\u4e00" <= char <= "\\u9fff" for char in body):\n        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"\n    if hint == "de":\n        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."\n    return "Sorry, I could not complete that request right now. Please try again or ask for human support."\n''',
        "",
        label="retired runtime wrapper fallback",
    )
    RUNTIME_WRAPPER.write_text(text, encoding="utf-8")


def patch_ticket_service() -> None:
    text = TICKET_SERVICE.read_text(encoding="utf-8")
    if "from .agent_runtime.terminal_reply import customer_visible_fallback" not in text:
        text = replace_once(
            text,
            "from .agent_runtime.access_policy import resolve_webchat_agent_access\n",
            "from .agent_runtime.access_policy import resolve_webchat_agent_access\nfrom .agent_runtime.terminal_reply import customer_visible_fallback\n",
            label="ticket fallback import",
        )
    text = replace_once(
        text,
        '''\n\ndef _localized_fallback(language: str | None, body: str) -> str:\n    if language == "zh" or any("\\u4e00" <= char <= "\\u9fff" for char in body):\n        return "抱歉，我暂时无法完成这次处理。请稍后重试，或者告诉我是否需要转人工客服。"\n    if language == "de":\n        return "Entschuldigung, ich konnte diese Anfrage gerade nicht abschließen. Bitte versuchen Sie es erneut oder bitten Sie um menschlichen Support."\n    return "Sorry, I could not complete that request right now. Please try again or ask for human support."\n''',
        "",
        label="retired ticket fallback",
    )
    text = replace_count(
        text,
        "_localized_fallback(language, visitor_message.body or \"\")",
        "customer_visible_fallback(language, visitor_message.body or \"\")",
        count=2,
        label="ticket fallback calls",
    )
    TICKET_SERVICE.write_text(text, encoding="utf-8")


def patch_ticketless_service() -> None:
    text = TICKETLESS_SERVICE.read_text(encoding="utf-8")
    if "from .agent_runtime.terminal_reply import customer_visible_fallback" not in text:
        text = replace_once(
            text,
            "from .agent_runtime.access_policy import resolve_webchat_agent_access\n",
            "from .agent_runtime.access_policy import resolve_webchat_agent_access\nfrom .agent_runtime.terminal_reply import customer_visible_fallback\n",
            label="ticketless fallback import",
        )
    old = '''    safe_runtime_trace = sanitized_ai_turn_runtime_trace(\n        result.runtime_trace\n    )\n    if not result.ok or not result.reply:\n        return {\n            "status": "failed_no_public_reply",\n            "reason": result.error_code or "agent_runtime_no_reply",\n            "reply_source": result.reply_source,\n            "runtime_trace": safe_runtime_trace,\n            "bridge_elapsed_ms": result.elapsed_ms,\n        }\n\n    if suppress_stale_reply_if_needed(\n        db,\n        conversation=conversation,\n        turn=turn,\n        reason="conversation_state_changed_during_ticketless_runtime",\n    ):\n        return {\n            "status": "superseded",\n            "reason": "conversation_state_changed_during_ticketless_runtime",\n            "reply_source": "suppressed",\n            "runtime_trace": safe_runtime_trace,\n            "bridge_elapsed_ms": result.elapsed_ms,\n        }\n\n    policy = evaluate_customer_visible_policy(result.reply)\n    if not policy.allowed or not policy.normalized_body.strip():\n        return {\n            "status": "failed_no_public_reply",\n            "reason": "customer_visible_policy_blocked",\n            "reply_source": result.reply_source,\n            "runtime_trace": safe_runtime_trace,\n            "bridge_elapsed_ms": result.elapsed_ms,\n        }\n\n    db.expire(conversation)\n    if result.handoff_required and not conversation.current_handoff_request_id:\n        return {\n            "status": "failed_no_public_reply",\n            "reason": "handoff_tool_side_effect_missing",\n            "reply_source": result.reply_source,\n            "runtime_trace": safe_runtime_trace,\n            "bridge_elapsed_ms": result.elapsed_ms,\n        }\n\n'''
    new = '''    safe_runtime_trace = sanitized_ai_turn_runtime_trace(\n        result.runtime_trace\n    )\n    fallback_reason = result.error_code if not result.ai_generated else None\n    reply_source = result.reply_source or "agent_runtime"\n    handoff_required = bool(result.handoff_required)\n    if not result.ok or not result.reply:\n        fallback_reason = result.error_code or "agent_runtime_no_reply"\n        reply_source = "agent_runtime:fallback"\n        handoff_required = False\n        policy = evaluate_customer_visible_policy(\n            customer_visible_fallback(language, visitor_message.body or "")\n        )\n    else:\n        policy = evaluate_customer_visible_policy(result.reply)\n        if not policy.allowed or not policy.normalized_body.strip():\n            fallback_reason = "customer_visible_policy_blocked"\n            reply_source = "agent_runtime:fallback"\n            handoff_required = False\n            policy = evaluate_customer_visible_policy(\n                customer_visible_fallback(language, visitor_message.body or "")\n            )\n    if not policy.allowed or not policy.normalized_body.strip():\n        raise RuntimeError("customer_visible_fallback_rejected")\n\n    if suppress_stale_reply_if_needed(\n        db,\n        conversation=conversation,\n        turn=turn,\n        reason="conversation_state_changed_during_ticketless_runtime",\n    ):\n        return {\n            "status": "superseded",\n            "reason": "conversation_state_changed_during_ticketless_runtime",\n            "reply_source": "suppressed",\n            "runtime_trace": safe_runtime_trace,\n            "bridge_elapsed_ms": result.elapsed_ms,\n        }\n\n    db.expire(conversation)\n    if handoff_required and not conversation.current_handoff_request_id:\n        fallback_reason = "handoff_tool_side_effect_missing"\n        reply_source = "agent_runtime:fallback"\n        handoff_required = False\n        policy = evaluate_customer_visible_policy(\n            customer_visible_fallback(language, visitor_message.body or "")\n        )\n        if not policy.allowed or not policy.normalized_body.strip():\n            raise RuntimeError("customer_visible_fallback_rejected")\n\n'''
    text = replace_once(text, old, new, label="ticketless terminal reply flow")
    text = replace_count(
        text,
        '"reply_source": result.reply_source,',
        '"reply_source": reply_source,',
        count=2,
        label="ticketless reply source projection",
    )
    text = replace_once(
        text,
        '''                "runtime_handoff_required": bool(\n                    result.handoff_required\n                ),''',
        '''                "runtime_handoff_required": handoff_required,''',
        label="ticketless metadata handoff projection",
    )
    text = replace_once(
        text,
        '                "fallback": not result.ai_generated,\n',
        '                "fallback": bool(fallback_reason) or not result.ai_generated,\n',
        label="ticketless fallback marker",
    )
    text = replace_count(
        text,
        '''                "fallback_reason": (\n                    result.error_code if not result.ai_generated else None\n                ),''',
        '''                "fallback_reason": fallback_reason,''',
        count=1,
        label="ticketless metadata fallback reason",
    )
    text = replace_once(
        text,
        '''        "fallback_reason": (\n            result.error_code if not result.ai_generated else None\n        ),''',
        '''        "fallback_reason": fallback_reason,''',
        label="ticketless result fallback reason",
    )
    text = replace_once(
        text,
        '        "runtime_handoff_required": bool(result.handoff_required),\n',
        '        "runtime_handoff_required": handoff_required,\n',
        label="ticketless result handoff projection",
    )
    TICKETLESS_SERVICE.write_text(text, encoding="utf-8")


def patch_tests() -> None:
    text = TURN_TESTS.read_text(encoding="utf-8")
    if "from app.services.agent_runtime.terminal_reply import customer_visible_fallback" not in text:
        text = replace_once(
            text,
            "from app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_webchat_ai_reply_jobs\n",
            "from app.services.agent_runtime.terminal_reply import customer_visible_fallback\nfrom app.services.background_jobs import WEBCHAT_AI_REPLY_JOB, dispatch_pending_webchat_ai_reply_jobs\n",
            label="terminal fallback test import",
        )
    old = '''        turn = db.get(WebchatAITurn, ai_turn_id)\n        assert turn is not None\n        assert turn.ticket_id is None\n        assert turn.status == "failed"\n        assert turn.status_reason == "handoff_tool_side_effect_missing"\n        assert (\n            db.query(WebchatMessage)\n            .filter(\n                WebchatMessage.ai_turn_id == ai_turn_id,\n                WebchatMessage.direction == "agent",\n            )\n            .count()\n            == 0\n        )\n        conversation = db.get(WebchatConversation, turn.conversation_id)\n'''
    new = '''        turn = db.get(WebchatAITurn, ai_turn_id)\n        assert turn is not None\n        assert turn.ticket_id is None\n        assert turn.status == "completed"\n        assert turn.status_reason is None\n        message = (\n            db.query(WebchatMessage)\n            .filter(\n                WebchatMessage.ai_turn_id == ai_turn_id,\n                WebchatMessage.direction == "agent",\n            )\n            .one()\n        )\n        assert message.body == customer_visible_fallback(\n            "en",\n            "I need a human to review this",\n        )\n        metadata = json.loads(message.metadata_json or "{}")\n        assert metadata["fallback"] is True\n        assert metadata["fallback_reason"] == "handoff_tool_side_effect_missing"\n        assert metadata["runtime_handoff_required"] is False\n        conversation = db.get(WebchatConversation, turn.conversation_id)\n'''
    text = replace_once(text, old, new, label="handoff terminal fallback regression")
    TURN_TESTS.write_text(text, encoding="utf-8")


def patch_residue_gate() -> None:
    text = RESIDUE.read_text(encoding="utf-8")
    for marker in ('    "def _localized_fallback(",\n', '    "def _fallback(language:",\n'):
        if marker not in text:
            text = replace_once(
                text,
                '    "**_legacy",\n',
                '    "**_legacy",\n' + marker,
                label=f"residue fallback marker {marker.strip()}",
            )
    RESIDUE.write_text(text, encoding="utf-8")


def patch_architecture() -> None:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    marker = "## Terminal reply authority"
    if marker not in text:
        text += '''\n\n## Terminal reply authority\n\n`agent_runtime/terminal_reply.py` is the only deterministic customer-visible fallback authority. Provider failure, Tool failure, output-policy rejection, missing Tool side effects, and disabled Runtime all converge on this reply before message persistence. Ticket-backed and ticketless adapters may differ in persistence mechanics, but neither may return an accepted customer message as `failed_no_public_reply` merely because Agent output was unusable. Superseded turns and active human takeover remain intentionally suppressed because a newer turn or human operator owns the customer response.\n'''
    ARCHITECTURE.write_text(text, encoding="utf-8")


def cleanup() -> None:
    for path in (WORKFLOW, SELF):
        path.unlink(missing_ok=True)


def main() -> None:
    write_shared_authority()
    patch_agent_service()
    patch_runtime_wrapper()
    patch_ticket_service()
    patch_ticketless_service()
    patch_tests()
    patch_residue_gate()
    patch_architecture()
    cleanup()


if __name__ == "__main__":
    main()
