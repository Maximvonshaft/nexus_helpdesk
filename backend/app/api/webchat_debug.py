from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..api.deps import get_current_user
from ..db import get_db
from ..models import Ticket
from ..models_webchat_debug import WebchatAIDebugRun, WebchatAIEvalCase, WebchatAITestFinding
from ..services.permissions import ensure_ticket_visible
from ..services.webchat_debug_bundle_service import build_ai_debug_bundle, create_eval_case_from_finding, create_test_finding
from ..utils.time import utc_now
from ..webchat_models import WebchatAITurn, WebchatEvent

router = APIRouter(prefix="/admin", tags=["webchat-debug"])


class DebugFindingPayload(BaseModel):
    finding_type: str = Field(min_length=1, max_length=120)
    severity: str = Field(default="medium", max_length=40)
    tester_note: str | None = Field(default=None, max_length=2000)
    expected_behavior: str | None = Field(default=None, max_length=2000)
    actual_behavior: str | None = Field(default=None, max_length=2000)


def _loads_json(value: str | None) -> Any:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, (dict, list)) else {}


def _debug_run_out(row: WebchatAIDebugRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "conversation_id": row.conversation_id,
        "ticket_id": row.ticket_id,
        "ai_turn_id": row.ai_turn_id,
        "visitor_message_id": row.visitor_message_id,
        "reply_message_id": row.reply_message_id,
        "request_id": row.request_id,
        "channel": row.channel,
        "status": row.status,
        "intent": row.intent,
        "reply_type": row.reply_type,
        "reply_source": row.reply_source,
        "provider_status": row.provider_status,
        "tracking_intent_detected": row.tracking_intent_detected,
        "tracking_fact_evidence_present": row.tracking_fact_evidence_present,
        "tool_facts_present": row.tool_facts_present,
        "live_tracking_answer_allowed": row.live_tracking_answer_allowed,
        "kb_hits_count": row.kb_hits_count,
        "tool_call_count": row.tool_call_count,
        "runtime_event_count": row.runtime_event_count,
        "safety_status": row.safety_status,
        "fact_gate_reason": row.fact_gate_reason,
        "customer_visible_message_created": row.customer_visible_message_created,
        "privacy": _loads_json(row.privacy_report_json),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


def _finding_out(row: WebchatAITestFinding) -> dict[str, Any]:
    return {
        "id": row.id,
        "debug_run_id": row.debug_run_id,
        "ai_turn_id": row.ai_turn_id,
        "conversation_id": row.conversation_id,
        "ticket_id": row.ticket_id,
        "finding_type": row.finding_type,
        "severity": row.severity,
        "tester_note": row.tester_note,
        "expected_behavior": row.expected_behavior,
        "actual_behavior": row.actual_behavior,
        "status": row.status,
        "linked_issue_url": row.linked_issue_url,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _eval_case_out(row: WebchatAIEvalCase) -> dict[str, Any]:
    return {
        "id": row.id,
        "case_key": row.case_key,
        "source_debug_run_id": row.source_debug_run_id,
        "source_finding_id": row.source_finding_id,
        "scenario": row.scenario,
        "intent": row.intent,
        "channel": row.channel,
        "expected_reply_type": row.expected_reply_type,
        "status": row.status,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _load_turn_visible(db: Session, *, ai_turn_id: int, current_user) -> WebchatAITurn:
    turn = db.get(WebchatAITurn, ai_turn_id)
    if turn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ai_turn_not_found")
    ticket = db.get(Ticket, turn.ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket_not_found")
    ensure_ticket_visible(current_user, ticket, db)
    return turn


@router.get("/ai-turns/{ai_turn_id}/debug-bundle")
def get_ai_turn_debug_bundle(ai_turn_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    turn = _load_turn_visible(db, ai_turn_id=ai_turn_id, current_user=current_user)
    bundle, _run = build_ai_debug_bundle(db, turn=turn)
    db.commit()
    return bundle


@router.get("/debug-runs")
def list_debug_runs(
    since_hours: int = Query(default=24, ge=1, le=720),
    channel: str | None = Query(default=None),
    intent: str | None = Query(default=None),
    status_value: str | None = Query(default=None, alias="status"),
    tracking_fact_evidence_present: bool | None = Query(default=None),
    live_tracking_answer_allowed: bool | None = Query(default=None),
    customer_visible_message_created: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
) -> dict[str, Any]:
    cutoff = utc_now() - timedelta(hours=since_hours)
    recent_turns = (
        db.query(WebchatAITurn)
        .filter(WebchatAITurn.created_at >= cutoff)
        .order_by(WebchatAITurn.created_at.desc(), WebchatAITurn.id.desc())
        .limit(limit)
        .all()
    )
    for turn in recent_turns:
        ticket = db.get(Ticket, turn.ticket_id)
        if ticket is None:
            continue
        try:
            ensure_ticket_visible(current_user, ticket, db)
        except HTTPException:
            continue
        try:
            build_ai_debug_bundle(db, turn=turn)
        except Exception:
            continue
    db.commit()

    query = db.query(WebchatAIDebugRun).filter(WebchatAIDebugRun.created_at >= cutoff)
    if channel:
        query = query.filter(WebchatAIDebugRun.channel == channel)
    if intent:
        query = query.filter(WebchatAIDebugRun.intent == intent)
    if status_value:
        query = query.filter(WebchatAIDebugRun.status == status_value)
    if tracking_fact_evidence_present is not None:
        query = query.filter(WebchatAIDebugRun.tracking_fact_evidence_present.is_(tracking_fact_evidence_present))
    if live_tracking_answer_allowed is not None:
        query = query.filter(WebchatAIDebugRun.live_tracking_answer_allowed.is_(live_tracking_answer_allowed))
    if customer_visible_message_created is not None:
        query = query.filter(WebchatAIDebugRun.customer_visible_message_created.is_(customer_visible_message_created))
    rows = query.order_by(WebchatAIDebugRun.created_at.desc(), WebchatAIDebugRun.id.desc()).limit(limit).all()
    visible_rows = []
    for row in rows:
        ticket = db.get(Ticket, row.ticket_id)
        if ticket is None:
            continue
        try:
            ensure_ticket_visible(current_user, ticket, db)
        except HTTPException:
            continue
        visible_rows.append(_debug_run_out(row))
    return {"items": visible_rows, "total": len(visible_rows), "since_hours": since_hours}


@router.get("/tickets/{ticket_id}/debug-events")
def list_ticket_debug_events(ticket_id: int, after_id: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200), db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket_not_found")
    ensure_ticket_visible(current_user, ticket, db)
    rows = db.query(WebchatEvent).filter(WebchatEvent.ticket_id == ticket_id, WebchatEvent.id > after_id).order_by(WebchatEvent.id.asc()).limit(limit).all()
    events = []
    for row in rows:
        payload = _loads_json(row.payload_json)
        event_type = row.event_type
        if not (event_type.startswith("ai.debug.") or event_type.startswith("ai_turn.") or event_type == "message.created"):
            continue
        events.append({"id": row.id, "event_type": event_type, "payload_json": payload, "created_at": row.created_at.isoformat() if row.created_at else None})
    return {"events": events, "last_event_id": rows[-1].id if rows else after_id, "has_more": len(rows) >= limit}


@router.post("/ai-turns/{ai_turn_id}/test-findings")
def create_ai_turn_test_finding(ai_turn_id: int, payload: DebugFindingPayload, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    turn = _load_turn_visible(db, ai_turn_id=ai_turn_id, current_user=current_user)
    _bundle, run = build_ai_debug_bundle(db, turn=turn)
    row = create_test_finding(db, run=run, current_user_id=getattr(current_user, "id", None), finding_type=payload.finding_type, severity=payload.severity, tester_note=payload.tester_note, expected_behavior=payload.expected_behavior, actual_behavior=payload.actual_behavior)
    db.commit()
    return _finding_out(row)


@router.post("/test-findings/{finding_id}/eval-case")
def create_eval_case_from_test_finding(finding_id: int, db: Session = Depends(get_db), current_user=Depends(get_current_user)) -> dict[str, Any]:
    finding = db.get(WebchatAITestFinding, finding_id)
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding_not_found")
    ticket = db.get(Ticket, finding.ticket_id)
    if ticket is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="ticket_not_found")
    ensure_ticket_visible(current_user, ticket, db)
    row = create_eval_case_from_finding(db, finding=finding, current_user_id=getattr(current_user, "id", None))
    db.commit()
    return _eval_case_out(row)
