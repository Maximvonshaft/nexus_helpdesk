from __future__ import annotations

from sqlalchemy import event, update
from sqlalchemy.inspection import inspect

from .utils.time import utc_now
from .voice_models import WebchatVoiceSession, WebchatVoiceSessionAction

_INSTALLED = False


def _project_command_state(connection, target: WebchatVoiceSessionAction) -> None:  # noqa: ANN001
    if target.action_type not in {"recording_start", "recording_stop"}:
        return
    status_value = str(target.status or "").strip().lower()
    state: str | None = None
    if target.action_type == "recording_start":
        if status_value in {"requested", "dispatching", "retryable"}:
            state = "start_requested"
        elif status_value == "succeeded":
            state = "active"
        elif status_value in {"failed", "cancelled"}:
            state = "failed"
    else:
        if status_value in {"requested", "dispatching", "retryable"}:
            state = "stop_requested"
        elif status_value == "succeeded":
            state = "completed"
        elif status_value in {"failed", "cancelled"}:
            state = "failed"
    if state is None:
        return
    connection.execute(
        update(WebchatVoiceSession)
        .where(WebchatVoiceSession.id == int(target.voice_session_id))
        .values(recording_status=state, updated_at=utc_now())
    )


def _after_insert(_mapper, connection, target: WebchatVoiceSessionAction) -> None:  # noqa: ANN001
    _project_command_state(connection, target)


def _after_update(_mapper, connection, target: WebchatVoiceSessionAction) -> None:  # noqa: ANN001
    history = inspect(target).attrs.status.history
    if not history.has_changes():
        return
    _project_command_state(connection, target)


def install_voice_runtime_state_events() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    event.listen(WebchatVoiceSessionAction, "after_insert", _after_insert)
    event.listen(WebchatVoiceSessionAction, "after_update", _after_update)
    _INSTALLED = True


__all__ = ["install_voice_runtime_state_events"]
