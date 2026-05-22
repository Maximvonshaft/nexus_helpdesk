from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class WebCallAIAllowedAction(str, Enum):
    ask_tracking_number = "ask_tracking_number"
    ask_caller_confirmation = "ask_caller_confirmation"
    lookup_tracking = "lookup_tracking"
    ask_waybill_suffix_selection = "ask_waybill_suffix_selection"
    explain_tracking_fact = "explain_tracking_fact"
    request_delivery_followup = "request_delivery_followup"
    handoff_to_human = "handoff_to_human"
    end_call = "end_call"


class WebCallAIForbiddenAction(str, Enum):
    cancel_order = "cancel_order"
    confirm_address_changed = "confirm_address_changed"
    submit_address_update_directly = "submit_address_update_directly"
    promise_compensation = "promise_compensation"
    promise_refund = "promise_refund"
    promise_delivery_time = "promise_delivery_time"
    blame_driver = "blame_driver"
    blame_dsp = "blame_dsp"
    contact_driver_directly = "contact_driver_directly"
    contact_dsp_directly = "contact_dsp_directly"
    execute_speedaf_write_directly = "execute_speedaf_write_directly"


NexusDecision = Literal["allowed", "blocked", "handoff", "failed"]


def reject_forbidden_action(action: str | WebCallAIAllowedAction) -> WebCallAIAllowedAction:
    value = action.value if isinstance(action, Enum) else str(action)
    forbidden = {item.value for item in WebCallAIForbiddenAction}
    if value in forbidden:
        raise ValueError(f"WebCall AI action is forbidden in PR-1: {value}")
    try:
        return WebCallAIAllowedAction(value)
    except ValueError as exc:
        raise ValueError(f"Unsupported WebCall AI foundation action: {value}") from exc


class WebCallAITurnDecision(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    action: WebCallAIAllowedAction
    intent: str | None = Field(default=None, max_length=80)
    language: str | None = Field(default=None, max_length=20)
    handoff_required: bool = False
    handoff_reason: str | None = Field(default=None, max_length=160)
    confidence: int | None = Field(default=None, ge=0, le=100)

    @field_validator("action", mode="before")
    @classmethod
    def _reject_forbidden_action(cls, value: str | WebCallAIAllowedAction) -> WebCallAIAllowedAction:
        return reject_forbidden_action(value)


class WebCallAIActionDecision(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    model_action: WebCallAIAllowedAction
    nexus_decision: NexusDecision
    decision_reason: str | None = Field(default=None, max_length=240)
    speedaf_tool_name: str | None = Field(default=None, max_length=160)
    result_status: str | None = Field(default=None, max_length=80)

    @field_validator("model_action", mode="before")
    @classmethod
    def _reject_forbidden_model_action(cls, value: str | WebCallAIAllowedAction) -> WebCallAIAllowedAction:
        return reject_forbidden_action(value)

    @field_validator("speedaf_tool_name")
    @classmethod
    def _block_direct_speedaf_write_tools(cls, value: str | None) -> str | None:
        if value in {"speedaf.order.cancel", "speedaf.order.update_address", "speedaf.work_order.create"}:
            raise ValueError(f"Direct Speedaf write tool is forbidden in PR-1: {value}")
        return value
