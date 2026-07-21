from __future__ import annotations

from pathlib import Path

path = Path("scripts/_generic_agent_final_core.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    'match = re.search(rf"^def {re.escape(name)}\\(", text, flags=re.MULTILINE)',
    'match = re.search(rf"^(?:async\\s+)?def {re.escape(name)}\\(", text, flags=re.MULTILINE)',
)
text = text.replace(
    'next_match = re.search(r"^def [A-Za-z0-9_]+\\(", text[match.end():], flags=re.MULTILINE)',
    'next_match = re.search(r"^(?:async\\s+)?def [A-Za-z0-9_]+\\(", text[match.end():], flags=re.MULTILINE)',
)
start_marker = 'existing_start, existing_end = function_bounds(core, "_existing_executed_log")\n'
end_marker = 'write(core_path, core)\n'
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("final core idempotency bootstrap boundary missing")
end += len(end_marker)
replacement = '''core = replace_function(
    core,
    "_existing_executed_log",
    """def _existing_executed_log(
    db: Session,
    *,
    action: RuntimeToolAction,
    idempotency_key: str | None,
    conversation: WebchatConversation | None,
    ticket: Ticket | None,
) -> ToolCallLog | None:
    if not idempotency_key:
        return None
    query = db.query(ToolCallLog).filter(
        ToolCallLog.tool_name == action.tool_name,
        ToolCallLog.request_id == idempotency_key,
        ToolCallLog.status == "executed",
    )
    if conversation is not None:
        # Conversation remains the stable idempotency scope when ticket.create
        # transitions the case from ticketless to ticket-backed.
        query = query.filter(ToolCallLog.webchat_conversation_id == conversation.id)
    elif ticket is not None:
        query = query.filter(ToolCallLog.ticket_id == ticket.id)
    return query.order_by(ToolCallLog.id.desc()).first()""",
)
write(core_path, core)
'''
path.write_text(text[:start] + replacement + text[end:], encoding="utf-8")

final_tests_path = Path("scripts/_generic_agent_final_tests.py")
final_tests = final_tests_path.read_text(encoding="utf-8")
last_mile_hook = '''

for finalizer_name in (
    "_generic_agent_last_mile.py",
    "_generic_agent_last_two.py",
):
    finalizer_path = ROOT / "scripts" / finalizer_name
    exec(
        compile(
            finalizer_path.read_text(encoding="utf-8"),
            str(finalizer_path),
            "exec",
        )
    )
    finalizer_path.unlink()
'''
if "for finalizer_name in (" not in final_tests:
    final_tests_path.write_text(final_tests.rstrip() + last_mile_hook + "\n", encoding="utf-8")
