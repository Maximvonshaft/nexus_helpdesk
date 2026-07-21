from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..models import Customer
from ..models_agent_control import CustomerMemoryFact
from ..utils.time import utc_now
from .agent_control_config import MEMORY_POLICY, resolve_singleton_agent_config
from .audit_service import log_admin_audit

_SAFE_MEMORY_KEY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,119}$")
_SECRET_PATTERNS = (
    re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}\b", re.IGNORECASE),
    re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
)
_DEFAULT_POLICY = {
    "injection_enabled": False,
    "write_enabled": False,
    "require_explicit_consent": True,
    "max_facts": 0,
    "retention_days": 180,
    "allowed_keys": [],
    "prohibited_categories": [
        "credential",
        "payment_card",
        "government_identifier",
        "health",
        "biometric",
        "raw_transcript",
    ],
    "enabled": False,
}


def resolve_memory_policy(
    db: Session,
    *,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    resolved = resolve_singleton_agent_config(
        db,
        config_type=MEMORY_POLICY,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    return dict(resolved.content) if resolved else dict(_DEFAULT_POLICY)


def list_customer_memory(
    db: Session,
    *,
    tenant_key: str,
    customer_id: int,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    _customer_or_404(db, tenant_key=tenant_key, customer_id=customer_id)
    query = db.query(CustomerMemoryFact).filter(
        CustomerMemoryFact.tenant_key == _tenant(tenant_key),
        CustomerMemoryFact.customer_id == customer_id,
    )
    if not include_inactive:
        query = query.filter(CustomerMemoryFact.is_active.is_(True))
    return [_out(row) for row in query.order_by(CustomerMemoryFact.memory_key.asc()).all()]


def runtime_memory_context(
    db: Session,
    *,
    tenant_key: str,
    customer_id: int | None,
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> dict[str, Any]:
    policy = resolve_memory_policy(
        db,
        market_id=market_id,
        channel=channel,
        language=language,
    )
    if not customer_id or not policy.get("enabled") or not policy.get("injection_enabled"):
        return {"enabled": False, "facts": [], "count": 0}
    now = utc_now()
    rows = (
        db.query(CustomerMemoryFact)
        .filter(
            CustomerMemoryFact.tenant_key == _tenant(tenant_key),
            CustomerMemoryFact.customer_id == customer_id,
            CustomerMemoryFact.is_active.is_(True),
            CustomerMemoryFact.sensitivity == "standard",
            or_(CustomerMemoryFact.expires_at.is_(None), CustomerMemoryFact.expires_at > now),
        )
        .order_by(
            CustomerMemoryFact.last_confirmed_at.desc().nullslast(),
            CustomerMemoryFact.updated_at.desc(),
        )
        .limit(int(policy.get("max_facts") or 0))
        .all()
    )
    facts = [
        {
            "key": row.memory_key,
            "value": row.value_text[:1000],
            "confidence": round(float(row.confidence or 0), 3),
            "source_type": row.source_type,
            "last_confirmed_at": row.last_confirmed_at.isoformat() if row.last_confirmed_at else None,
            "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        }
        for row in rows
    ]
    return {
        "enabled": True,
        "facts": facts,
        "count": len(facts),
        "policy": {
            "require_explicit_consent": bool(policy.get("require_explicit_consent")),
            "write_enabled": bool(policy.get("write_enabled")),
        },
    }


def upsert_customer_memory(
    db: Session,
    *,
    tenant_key: str,
    customer_id: int,
    memory_key: str,
    value_text: str,
    actor_id: int | None,
    consent_basis: str | None,
    source_type: str = "operator",
    source_reference: str | None = None,
    confidence: float = 1.0,
    sensitivity: str = "standard",
    market_id: int | None = None,
    channel: str | None = None,
    language: str | None = None,
) -> CustomerMemoryFact:
    customer = _customer_or_404(db, tenant_key=tenant_key, customer_id=customer_id)
    del customer
    policy = resolve_memory_policy(db, market_id=market_id, channel=channel, language=language)
    if not policy.get("enabled") or not policy.get("write_enabled"):
        raise HTTPException(status_code=409, detail="customer_memory_write_disabled")
    key = _memory_key(memory_key)
    allowed = {str(item).strip().lower() for item in policy.get("allowed_keys") or []}
    if key not in allowed:
        raise HTTPException(status_code=400, detail="customer_memory_key_not_allowed")
    consent = " ".join(str(consent_basis or "").strip().split()) or None
    if policy.get("require_explicit_consent") and not consent:
        raise HTTPException(status_code=400, detail="customer_memory_explicit_consent_required")
    value = _safe_value(value_text)
    normalized_sensitivity = str(sensitivity or "standard").strip().lower()
    if normalized_sensitivity not in {"standard", "restricted"}:
        raise HTTPException(status_code=400, detail="customer_memory_sensitivity_invalid")
    now = utc_now()
    expires_at = now + timedelta(days=int(policy.get("retention_days") or 180))
    row = (
        db.query(CustomerMemoryFact)
        .filter(
            CustomerMemoryFact.tenant_key == _tenant(tenant_key),
            CustomerMemoryFact.customer_id == customer_id,
            CustomerMemoryFact.memory_key == key,
        )
        .first()
    )
    old = _out(row) if row else None
    if row is None:
        row = CustomerMemoryFact(
            tenant_key=_tenant(tenant_key),
            customer_id=customer_id,
            memory_key=key,
            value_text=value,
            created_by=actor_id,
        )
        db.add(row)
    row.value_text = value
    row.source_type = _source_type(source_type)
    row.source_reference = _optional_text(source_reference, 200)
    row.consent_basis = consent[:80] if consent else None
    row.confidence = max(0.0, min(1.0, float(confidence)))
    row.sensitivity = normalized_sensitivity
    row.is_active = True
    row.expires_at = expires_at
    row.last_confirmed_at = now
    row.updated_by = actor_id
    row.updated_at = now
    db.flush()
    log_admin_audit(
        db,
        actor_id=actor_id,
        action="customer_memory.upsert",
        target_type="customer_memory_fact",
        target_id=row.id,
        old_value=old,
        new_value=_out(row),
    )
    return row


def deactivate_customer_memory(
    db: Session,
    *,
    tenant_key: str,
    customer_id: int,
    memory_id: int,
    actor_id: int | None,
) -> CustomerMemoryFact:
    row = (
        db.query(CustomerMemoryFact)
        .filter(
            CustomerMemoryFact.id == memory_id,
            CustomerMemoryFact.tenant_key == _tenant(tenant_key),
            CustomerMemoryFact.customer_id == customer_id,
        )
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="customer_memory_not_found")
    old = _out(row)
    row.is_active = False
    row.updated_by = actor_id
    row.updated_at = utc_now()
    db.flush()
    log_admin_audit(
        db,
        actor_id=actor_id,
        action="customer_memory.deactivate",
        target_type="customer_memory_fact",
        target_id=row.id,
        old_value=old,
        new_value=_out(row),
    )
    return row


def forget_customer_memory(
    db: Session,
    *,
    tenant_key: str,
    customer_id: int,
    actor_id: int | None,
) -> int:
    _customer_or_404(db, tenant_key=tenant_key, customer_id=customer_id)
    rows = (
        db.query(CustomerMemoryFact)
        .filter(
            CustomerMemoryFact.tenant_key == _tenant(tenant_key),
            CustomerMemoryFact.customer_id == customer_id,
        )
        .all()
    )
    ids = [row.id for row in rows]
    for row in rows:
        db.delete(row)
    db.flush()
    log_admin_audit(
        db,
        actor_id=actor_id,
        action="customer_memory.forget_all",
        target_type="customer",
        target_id=customer_id,
        old_value={"memory_fact_ids": ids, "count": len(ids)},
        new_value={"count": 0},
    )
    return len(ids)


def _customer_or_404(db: Session, *, tenant_key: str, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=404, detail="customer_not_found")
    normalized = _tenant(tenant_key)
    customer_tenant_key = getattr(getattr(customer, "tenant", None), "tenant_key", None)
    if customer_tenant_key and customer_tenant_key != normalized:
        raise HTTPException(status_code=404, detail="customer_not_found")
    return customer


def _out(row: CustomerMemoryFact | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row.id,
        "tenant_key": row.tenant_key,
        "customer_id": row.customer_id,
        "memory_key": row.memory_key,
        "value_text": row.value_text,
        "source_type": row.source_type,
        "source_reference": row.source_reference,
        "consent_basis": row.consent_basis,
        "confidence": row.confidence,
        "sensitivity": row.sensitivity,
        "is_active": row.is_active,
        "expires_at": row.expires_at,
        "last_confirmed_at": row.last_confirmed_at,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _tenant(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned or cleaned == "default":
        raise HTTPException(status_code=400, detail="tenant_key_required")
    return cleaned[:80]


def _memory_key(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    if not _SAFE_MEMORY_KEY.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="customer_memory_key_invalid")
    return cleaned


def _safe_value(value: str) -> str:
    cleaned = " ".join(str(value or "").strip().split())
    if not cleaned:
        raise HTTPException(status_code=400, detail="customer_memory_value_required")
    if len(cleaned) > 2000:
        raise HTTPException(status_code=400, detail="customer_memory_value_too_long")
    if any(pattern.search(cleaned) for pattern in _SECRET_PATTERNS):
        raise HTTPException(status_code=400, detail="customer_memory_sensitive_value_forbidden")
    return cleaned


def _source_type(value: str) -> str:
    cleaned = str(value or "operator").strip().lower()
    if cleaned not in {"customer", "operator", "system", "import"}:
        raise HTTPException(status_code=400, detail="customer_memory_source_invalid")
    return cleaned


def _optional_text(value: Any, limit: int) -> str | None:
    cleaned = " ".join(str(value or "").strip().split())
    return cleaned[:limit] if cleaned else None
