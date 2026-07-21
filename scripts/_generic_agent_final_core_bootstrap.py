from __future__ import annotations

from pathlib import Path

path = Path("scripts/_generic_agent_final_core.py")
text = path.read_text(encoding="utf-8")
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
