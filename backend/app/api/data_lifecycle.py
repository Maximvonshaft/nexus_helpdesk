from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..models_case_governance import DataSubjectRequest
from ..services.data_lifecycle_attachment_cleanup import (
    bind_attachment_cleanup_receipt,
    delete_subject_attachment_blobs,
)
from ..services.data_lifecycle_service import (
    DataLifecycleError,
    apply_retention_execution,
    build_data_subject_export,
    create_data_subject_request,
    create_retention_policy,
    execute_data_subject_deletion,
    place_legal_hold,
    plan_retention_execution,
    qualify_data_subject_request,
    release_legal_hold,
)
from ..services.data_subject_action_service import (
    activate_data_processing_restriction,
    execute_data_subject_correction,
    release_data_processing_restriction,
)
from ..services.permissions import (
    ensure_can_manage_users,
    ensure_can_view_security_audit,
)
from ..unit_of_work import managed_session
from .deps import get_current_user

router = APIRouter(
    prefix="/api/admin/privacy",
    tags=["administration", "privacy", "data-lifecycle"],
)


class DSARCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int = Field(gt=0)
    request_key: str = Field(min_length=1, max_length=160)
    request_type: Literal["access", "export", "delete", "restrict", "correct"]


class DSARQualifyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identity_evidence: str = Field(min_length=1, max_length=320)


class DSARCorrectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=200)
    phone: str | None = Field(default=None, min_length=3, max_length=80)
    external_ref: str | None = Field(default=None, min_length=1, max_length=120)


class DSARRestrictionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: str = Field(
        default="data_subject_requested_restriction",
        min_length=1,
        max_length=120,
    )


class LegalHoldCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: int | None = Field(default=None, gt=0)
    ticket_id: int | None = Field(default=None, gt=0)
    reason_code: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)


class RetentionPolicyCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_type: str = Field(min_length=1, max_length=80)
    retention_days: int = Field(ge=0, le=36500)
    legal_basis: str = Field(min_length=1, max_length=160)
    action: Literal["anonymize"] = "anonymize"


class RetentionExecutionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy_id: int = Field(gt=0)
    execution_key: str = Field(min_length=1, max_length=160)


def _raise(exc: DataLifecycleError) -> None:
    raise HTTPException(
        status_code=exc.status_code,
        detail={"code": exc.code},
    ) from exc


def _dsar_payload(row, *, created: bool | None = None) -> dict[str, Any]:
    payload = {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "customer_id": row.customer_id,
        "request_key": row.request_key,
        "request_type": row.request_type,
        "status": row.status,
        "identity_verified": bool(row.identity_evidence_hash),
        "scope": row.scope_json or {},
        "result_manifest": row.result_manifest_json,
        "result_sha256": row.result_sha256,
        "blocked_reason": row.blocked_reason,
        "received_at": row.received_at,
        "due_at": row.due_at,
        "completed_at": row.completed_at,
        "updated_at": row.updated_at,
    }
    if created is not None:
        payload["created"] = created
    return payload


def _hold_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "customer_id": row.customer_id,
        "ticket_id": row.ticket_id,
        "reason_code": row.reason_code,
        "status": row.status,
        "placed_at": row.placed_at,
        "released_at": row.released_at,
    }


def _restriction_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "customer_id": row.customer_id,
        "request_id": row.request_id,
        "status": row.status,
        "reason_code": row.reason_code,
        "blocked_purposes": row.blocked_purposes_json or [],
        "allowed_purposes": row.allowed_purposes_json or [],
        "placed_at": row.placed_at,
        "released_at": row.released_at,
    }


def _execution_payload(row) -> dict[str, Any]:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "policy_version_id": row.policy_version_id,
        "execution_key": row.execution_key,
        "status": row.status,
        "cutoff_at": row.cutoff_at,
        "scanned_count": row.scanned_count,
        "affected_count": row.affected_count,
        "held_count": row.held_count,
        "receipt": row.receipt_json,
        "receipt_sha256": row.receipt_sha256,
        "created_at": row.created_at,
        "completed_at": row.completed_at,
    }


@router.post("/dsar")
def create_dsar(
    payload: DSARCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row, created = create_data_subject_request(
                db,
                actor=current_user,
                customer_id=payload.customer_id,
                request_key=payload.request_key,
                request_type=payload.request_type,
            )
        return _dsar_payload(row, created=created)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/dsar/{request_id}/qualify")
def qualify_dsar(
    request_id: int,
    payload: DSARQualifyRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = qualify_data_subject_request(
                db,
                actor=current_user,
                request_id=request_id,
                identity_evidence=payload.identity_evidence,
            )
        return _dsar_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.get("/dsar/{request_id}/export")
def export_dsar(
    request_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_view_security_audit(current_user, db)
    try:
        with managed_session(db):
            return build_data_subject_export(
                db,
                actor=current_user,
                request_id=request_id,
            )
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/dsar/{request_id}/delete")
def delete_dsar_subject(
    request_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            request = db.get(DataSubjectRequest, request_id)
            if request is None:
                raise DataLifecycleError("dsar_not_found", status_code=404)
            attachment_receipt = delete_subject_attachment_blobs(
                db,
                actor=current_user,
                customer_id=request.customer_id,
            )
            database_receipt = execute_data_subject_deletion(
                db,
                actor=current_user,
                request_id=request_id,
            )
            receipt = bind_attachment_cleanup_receipt(
                db,
                request_id=request_id,
                database_receipt=database_receipt,
                attachment_receipt=attachment_receipt,
            )
        return {
            "schema": "nexus.subject-deletion-result.v1",
            "customer_id": receipt.customer_id,
            "ticket_count": receipt.ticket_count,
            "conversation_count": receipt.conversation_count,
            "message_count": receipt.message_count,
            "related_row_count": receipt.related_row_count,
            "attachment_count": receipt.attachment_count,
            "attachment_deleted_count": receipt.attachment_deleted_count,
            "attachment_already_absent_count": (
                receipt.attachment_already_absent_count
            ),
            "receipt_sha256": receipt.receipt_sha256,
        }
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/dsar/{request_id}/correct")
def correct_dsar_subject(
    request_id: int,
    payload: DSARCorrectionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            result = execute_data_subject_correction(
                db,
                actor=current_user,
                request_id=request_id,
                name=payload.name,
                email=payload.email,
                phone=payload.phone,
                external_ref=payload.external_ref,
            )
        return {
            "schema": "nexus.subject-correction-result.v1",
            "request": _dsar_payload(result.request),
            "customer_id": result.customer.id,
            "corrected_fields": list(result.fields),
            "raw_values_persisted_in_receipt": False,
        }
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/dsar/{request_id}/restrict")
def restrict_dsar_subject(
    request_id: int,
    payload: DSARRestrictionRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = activate_data_processing_restriction(
                db,
                actor=current_user,
                request_id=request_id,
                reason_code=payload.reason_code,
            )
        return _restriction_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/processing-restrictions/{restriction_id}/release")
def release_processing_restriction(
    restriction_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = release_data_processing_restriction(
                db,
                actor=current_user,
                restriction_id=restriction_id,
            )
        return _restriction_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/legal-holds")
def create_legal_hold(
    payload: LegalHoldCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = place_legal_hold(
                db,
                actor=current_user,
                customer_id=payload.customer_id,
                ticket_id=payload.ticket_id,
                reason_code=payload.reason_code,
                note=payload.note,
            )
        return _hold_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/legal-holds/{hold_id}/release")
def release_hold(
    hold_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = release_legal_hold(
                db,
                actor=current_user,
                hold_id=hold_id,
            )
        return _hold_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/retention/policies")
def create_policy(
    payload: RetentionPolicyCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = create_retention_policy(
                db,
                actor=current_user,
                resource_type=payload.resource_type,
                retention_days=payload.retention_days,
                legal_basis=payload.legal_basis,
                action=payload.action,
            )
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "resource_type": row.resource_type,
            "version": row.version,
            "retention_days": row.retention_days,
            "legal_basis": row.legal_basis,
            "action": row.action,
            "status": row.status,
            "effective_from": row.effective_from,
        }
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/retention/executions")
def create_execution(
    payload: RetentionExecutionCreateRequest,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = plan_retention_execution(
                db,
                actor=current_user,
                policy_id=payload.policy_id,
                execution_key=payload.execution_key,
            )
        return _execution_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)


@router.post("/retention/executions/{execution_id}/apply")
def apply_execution(
    execution_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user),
):
    ensure_can_manage_users(current_user, db)
    try:
        with managed_session(db):
            row = apply_retention_execution(
                db,
                actor=current_user,
                execution_id=execution_id,
            )
        return _execution_payload(row)
    except DataLifecycleError as exc:
        _raise(exc)
