from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str, *, label: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}_COUNT={count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "backend/app/services/webchat_service.py",
    "from .outbound_safety import evaluate_outbound_safety, format_safety_reasons\n",
    "from .outbound_safety import evaluate_outbound_safety, format_safety_reasons\nfrom .server_fact_evidence import resolve_server_fact_evidence\n",
    label="WEBCHAT_SERVICE_IMPORT",
)
replace_once(
    "backend/app/services/webchat_service.py",
    """    body: str,
    has_fact_evidence: bool = False,
    confirm_review: bool = False,
    conversation_public_id: str | None = None,
""",
    """    body: str,
    has_fact_evidence: bool = False,
    evidence_reference_id: int | None = None,
    confirm_review: bool = False,
    conversation_public_id: str | None = None,
""",
    label="ADMIN_REPLY_SIGNATURE",
)
replace_once(
    "backend/app/services/webchat_service.py",
    """    normalized_body = _clip_body(body)
    decision = evaluate_outbound_safety(ticket, normalized_body, source="manual", has_fact_evidence=has_fact_evidence)
    decision_payload = asdict(decision)
""",
    """    normalized_body = _clip_body(body)
    server_evidence = resolve_server_fact_evidence(
        db,
        ticket=ticket,
        conversation=conversation,
        evidence_reference_id=evidence_reference_id,
    )
    # Backwards-compatible parsing only.  The client boolean is never trusted.
    _ = has_fact_evidence
    decision = evaluate_outbound_safety(
        ticket,
        normalized_body,
        source="manual",
        has_fact_evidence=server_evidence.present,
    )
    decision_payload = asdict(decision)
    decision_payload["evidence"] = server_evidence.audit_payload()
""",
    label="ADMIN_REPLY_SAFETY",
)
replace_once(
    "backend/app/services/webchat_service.py",
    "metadata_json=_metadata(generated_by=\"human_agent\", safety_level=decision.level, fact_evidence_present=has_fact_evidence, external_send=is_external_reply),",
    "metadata_json=_metadata(generated_by=\"human_agent\", safety_level=decision.level, fact_evidence_present=server_evidence.present, fact_evidence_reference_id=server_evidence.reference_id, fact_evidence_reason=server_evidence.reason, external_send=is_external_reply),",
    label="ADMIN_REPLY_INITIAL_METADATA",
)
replace_once(
    "backend/app/services/webchat_service.py",
    """        fact_evidence_present=has_fact_evidence,
        external_send=is_external_reply,
""",
    """        fact_evidence_present=server_evidence.present,
        fact_evidence_reference_id=server_evidence.reference_id,
        fact_evidence_reason=server_evidence.reason,
        external_send=is_external_reply,
""",
    label="ADMIN_REPLY_FINAL_METADATA",
)
replace_once(
    "backend/app/services/webchat_service.py",
    """            "provider_status": provider_status,
        },
""",
    """            "provider_status": provider_status,
            "case_context_id": server_evidence.reference_id,
            "fact_evidence_present": server_evidence.present,
            "fact_evidence_reason": server_evidence.reason,
        },
""",
    label="ADMIN_REPLY_EVENT_EVIDENCE",
)

replace_once(
    "backend/app/api/webchat.py",
    """class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    confirm_review: bool = False
""",
    """class WebchatReplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=2000)
    has_fact_evidence: bool = False
    evidence_reference_id: int | None = Field(default=None, ge=1)
    confirm_review: bool = False
""",
    label="WEBCHAT_REPLY_MODEL",
)
replace_once(
    "backend/app/api/webchat.py",
    "result = admin_reply(db, ticket_id, current_user, body=payload.body, has_fact_evidence=payload.has_fact_evidence, confirm_review=payload.confirm_review)",
    """result = admin_reply(
            db,
            ticket_id,
            current_user,
            body=payload.body,
            has_fact_evidence=payload.has_fact_evidence,
            evidence_reference_id=payload.evidence_reference_id,
            confirm_review=payload.confirm_review,
        )""",
    label="WEBCHAT_REPLY_CALL",
)

replace_once(
    "backend/app/api/support_conversations.py",
    """class SupportConversationReplyRequest(BaseModel):
    session_key: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=40000)
    has_fact_evidence: bool = False
    confirm_review: bool = False
""",
    """class SupportConversationReplyRequest(BaseModel):
    session_key: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=40000)
    has_fact_evidence: bool = False
    evidence_reference_id: int | None = Field(default=None, ge=1)
    confirm_review: bool = False
""",
    label="SUPPORT_REPLY_MODEL",
)
replace_once(
    "backend/app/api/support_conversations.py",
    """            body=payload.body,
            has_fact_evidence=payload.has_fact_evidence,
            confirm_review=payload.confirm_review,
""",
    """            body=payload.body,
            has_fact_evidence=payload.has_fact_evidence,
            evidence_reference_id=payload.evidence_reference_id,
            confirm_review=payload.confirm_review,
""",
    label="SUPPORT_REPLY_CALL",
)

replace_once(
    "backend/app/api/webchat_ws.py",
    """                    body=str(message.get("body") or ""),
                    has_fact_evidence=bool(message.get("has_fact_evidence")),
                    confirm_review=bool(message.get("confirm_review")),
""",
    """                    body=str(message.get("body") or ""),
                    has_fact_evidence=bool(message.get("has_fact_evidence")),
                    evidence_reference_id=message.get("evidence_reference_id"),
                    confirm_review=bool(message.get("confirm_review")),
""",
    label="WEBSOCKET_REPLY_CALL",
)

replace_once(
    "backend/app/services/webchat_osr_audit_service.py",
    """            "checked_at",
            "tracking_number_hash",
            "tracking_reference_suffix",
            "safe_tracking_reference",
            "lookup_elapsed_ms",
            "status_context",
            "tracking_fact_failure_reason",
""",
    """            "checked_at",
            "observed_at",
            "freshness",
            "evidence_state",
            "source_authority",
            "contradictions",
            "used_sources",
            "tracking_number_hash",
            "tracking_reference_suffix",
            "safe_tracking_reference",
            "lookup_elapsed_ms",
            "status_context",
            "tracking_fact_failure_reason",
""",
    label="CASE_CONTEXT_FACT_FIELDS",
)
