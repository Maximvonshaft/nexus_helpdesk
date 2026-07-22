from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from sqlalchemy.orm import Session

from ..webchat_models import WebchatConversation
from .agent_confirmation_service import (
    confirmation_projection,
    create_or_reuse_confirmation,
)
from .nexus_osr.controlled_action_executor import (
    ActionExecutionRequest,
    ActionExecutionResult,
    ActionHandler,
)
from .webchat_ai_decision_runtime.tool_registry import get_tool_contract

_TOOL_NAME = "customer.confirmation.request"


def build_agent_confirmation_tool_handlers(
    db: Session,
    *,
    conversation: WebchatConversation | None,
) -> dict[str, ActionHandler]:
    def request_confirmation(
        request: ActionExecutionRequest,
    ) -> ActionExecutionResult:
        if conversation is None:
            return _failure(request, "conversation_required")
        arguments = request.action.arguments
        target_tool = " ".join(
            str(arguments.get("tool_name") or "").strip().split()
        )[:160]
        target_arguments = (
            arguments.get("arguments")
            if isinstance(arguments.get("arguments"), dict)
            else {}
        )
        question = " ".join(
            str(arguments.get("question") or "").strip().split()
        )[:1000]
        contract = get_tool_contract(target_tool)
        if contract is None or not contract.confirmation_required:
            return _failure(request, "confirmation_target_invalid")
        confirmable = {
            str(item).strip()
            for item in request.audit_context.get("confirmable_tool_names") or []
            if str(item).strip()
        }
        if target_tool not in confirmable:
            return _failure(request, "confirmation_target_not_available")
        granted_permissions = {
            str(item).strip()
            for item in request.audit_context.get("granted_permissions") or []
            if str(item).strip()
        }
        if not set(contract.required_permissions).issubset(granted_permissions):
            return _failure(request, "confirmation_target_permission_denied")
        errors = sorted(
            Draft202012Validator(contract.input_schema).iter_errors(
                target_arguments
            ),
            key=lambda error: tuple(str(item) for item in error.absolute_path),
        )
        if errors:
            return _failure(request, "confirmation_target_arguments_invalid")
        if not question:
            return _failure(request, "confirmation_question_required")
        row = create_or_reuse_confirmation(
            db,
            conversation=conversation,
            tool_name=target_tool,
            arguments=target_arguments,
            question_text=question,
            requested_message_id=(
                int(request.audit_context["trigger_message_id"])
                if request.audit_context.get("trigger_message_id") is not None
                else None
            ),
        )
        projection = confirmation_projection(row)
        return ActionExecutionResult(
            ok=True,
            tool_name=request.action.tool_name,
            status="executed",
            summary=projection,
            customer_visible_summary=row.question_text,
            case_context=request.case_context,
        )

    return {_TOOL_NAME: request_confirmation}


def executable_confirmation_tool_names() -> tuple[str, ...]:
    return (_TOOL_NAME,)


def _failure(
    request: ActionExecutionRequest,
    error_code: str,
) -> ActionExecutionResult:
    return ActionExecutionResult(
        ok=False,
        tool_name=request.action.tool_name,
        status="failed",
        summary={},
        case_context=request.case_context,
        error_code=error_code[:120],
    )
