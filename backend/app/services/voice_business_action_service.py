from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from ..enums import EventType
from ..models import User
from ..utils.time import utc_now
from ..voice_models import WebchatVoiceAIAction
from ..webchat_models import WebchatEvent
from .audit_service import log_admin_audit
from .background_jobs import (
    enqueue_speedaf_voice_callback_job,
    find_recent_speedaf_voice_callback_job,
)
from .event_service import log_event
from .permissions import ensure_can_control_webcall_voice
from .speedaf.redactor import safe_waybill_payload
from .voice_session_service import _visible_context


def queue_speedaf_voice_callback(
    db: Session,
    *,
    voice_session_public_id: str,
    current_user: User,
    call_session_id: str | None,
    is_transferred_to_human: bool,
    action: dict[str, Any],
    request_id: str | None = None,
) -> dict[str, Any]:
    ensure_can_control_webcall_voice(current_user, db)
    session, _conversation, ticket = _visible_context(
        db,
        public_id=voice_session_public_id,
        current_user=current_user,
    )
    if ticket is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="formal_ticket_required_for_voice_business_action",
        )

    waybill_code = " ".join(
        str(action.get("waybillCode") or "").strip().split()
    ).upper()
    action_name = " ".join(str(action.get("action") or "").strip().split())[:32]
    action_summary = " ".join(
        str(action.get("aiActionSummary") or "").strip().split()
    )[:200]
    action_status = str(action.get("actionStatus") or "SUCCESS").strip().upper()
    error_code = " ".join(
        str(action.get("errorCode") or "").strip().split()
    )[:80]
    if not waybill_code:
        raise HTTPException(status_code=422, detail="speedaf_voice_callback_waybill_required")
    if not action_name:
        raise HTTPException(status_code=422, detail="speedaf_voice_callback_action_required")
    if not action_summary:
        raise HTTPException(status_code=422, detail="speedaf_voice_callback_summary_required")
    if action_status not in {"SUCCESS", "FAILED"}:
        raise HTTPException(status_code=422, detail="speedaf_voice_callback_status_invalid")

    now = utc_now()
    action_time_input = str(action.get("actionTime") or "").strip()
    action_time = action_time_input or now.strftime("%Y-%m-%d %H:%M:%S")
    callback_action = {
        "waybillCode": waybill_code,
        "action": action_name,
        "actionTime": action_time,
        "aiActionSummary": action_summary,
        "actionStatus": action_status,
        "errorCode": error_code,
    }
    resolved_call_session_id = str(
        call_session_id or session.public_id or session.id
    ).strip()
    dedupe_material = json.dumps(
        {
            "voice_session_id": session.id,
            "callSessionId": resolved_call_session_id,
            "isTransferredToHuman": bool(is_transferred_to_human),
            "action": {**callback_action, "actionTime": action_time_input},
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    dedupe_key = (
        f"speedaf-voice-callback:voice:{session.id}:payload:"
        f"{hashlib.sha256(dedupe_material.encode('utf-8')).hexdigest()[:16]}"
    )
    existing_job = find_recent_speedaf_voice_callback_job(
        db,
        dedupe_key=dedupe_key,
    )
    if existing_job is not None:
        value = getattr(existing_job.status, "value", str(existing_job.status))
        response_status = "already_submitted" if value == "done" else "already_queued"
        return {
            "ok": True,
            "ticket_id": ticket.id,
            "voice_session_id": session.public_id,
            "status": response_status,
            "message": (
                "Speedaf voice callback already submitted."
                if response_status == "already_submitted"
                else "Speedaf voice callback already queued."
            ),
            "jobId": existing_job.id,
            "dedupeKey": dedupe_key,
            "ai_action_id": None,
        }

    job = enqueue_speedaf_voice_callback_job(
        db,
        ticket_id=ticket.id,
        voice_session_id=session.id,
        call_session_id=resolved_call_session_id,
        is_transferred_to_human=bool(is_transferred_to_human),
        action=callback_action,
        dedupe_key=dedupe_key,
        request_id=request_id,
    )
    ai_action = WebchatVoiceAIAction(
        voice_session_id=session.id,
        turn_id=None,
        model_action=action_name,
        nexus_decision="speedaf_voice_callback_queued",
        decision_reason="operator_confirmed_speedaf_voice_callback",
        speedaf_tool_name="speedaf.voice.callback",
        background_job_id=job.id,
        result_status="queued",
        created_at=now,
    )
    db.add(ai_action)
    db.flush()
    safe_payload = {
        "voice_session_id": session.public_id,
        "job_id": job.id,
        "ai_action_id": ai_action.id,
        "dedupe_key": dedupe_key,
        "call_session_id": {
            "redacted": True,
            "sha256": hashlib.sha256(
                resolved_call_session_id.encode("utf-8")
            ).hexdigest(),
        },
        "is_transferred_to_human": bool(is_transferred_to_human),
        "action": {
            **safe_waybill_payload(waybill_code),
            "action": action_name,
            "actionTime": action_time,
            "aiActionSummary": action_summary,
            "actionStatus": action_status,
            "errorCode": error_code,
        },
    }
    ticket_event = log_event(
        db,
        ticket_id=ticket.id,
        actor_id=current_user.id,
        event_type=EventType.field_updated,
        field_name="speedaf_voice_callback",
        new_value="queued",
        note="Speedaf voice callback queued.",
        payload=safe_payload,
    )
    webchat_event = WebchatEvent(
        conversation_id=session.conversation_id,
        ticket_id=ticket.id,
        event_type="voice.speedaf_callback.queued",
        payload_json=json.dumps(
            {
                **safe_payload,
                "actor_user_id": current_user.id,
                "ticket_event_id": ticket_event.id,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        created_at=now,
    )
    db.add(webchat_event)
    db.flush()
    audit = log_admin_audit(
        db,
        actor_id=current_user.id,
        action="speedaf.voice.callback.queued",
        target_type="webchat_voice_session",
        target_id=session.id,
        old_value=None,
        new_value={
            **safe_payload,
            "ticket_id": ticket.id,
            "webchat_event_id": webchat_event.id,
        },
    )
    session.updated_at = now
    db.flush()
    return {
        "ok": True,
        "ticket_id": ticket.id,
        "voice_session_id": session.public_id,
        "status": "queued",
        "message": "Speedaf voice callback queued.",
        "jobId": job.id,
        "dedupeKey": dedupe_key,
        "ai_action_id": ai_action.id,
        "audit_id": audit.id,
    }
