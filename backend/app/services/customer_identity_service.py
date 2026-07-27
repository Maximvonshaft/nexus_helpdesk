from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Customer, Tenant
from ..models_channel_intake import CustomerIdentityBinding
from ..utils.normalize import normalize_email, normalize_phone
from ..utils.time import utc_now
from .tenant_authority import stamp_runtime_tenant

_SUPPORTED_IDENTITY_TYPES = frozenset({"email", "phone", "external_ref"})


class CustomerIdentityError(RuntimeError):
    pass


@dataclass(frozen=True)
class CustomerIdentity:
    identity_type: str
    normalized_value: str


def normalize_customer_identity(
    identity_type: str,
    identity_value: str,
) -> CustomerIdentity:
    kind = str(identity_type or "").strip().lower()
    if kind not in _SUPPORTED_IDENTITY_TYPES:
        raise CustomerIdentityError("unsupported_customer_identity_type")
    raw = str(identity_value or "").strip()
    if kind == "email":
        normalized = normalize_email(raw) or ""
    elif kind == "phone":
        normalized = normalize_phone(raw) or ""
    else:
        normalized = raw.casefold()
    if not normalized:
        raise CustomerIdentityError("customer_identity_value_required")
    return CustomerIdentity(kind, normalized[:320])


def _active_tenant(db: Session, tenant_id: int) -> Tenant:
    tenant = db.get(Tenant, int(tenant_id))
    if tenant is None or not tenant.is_active:
        raise CustomerIdentityError("customer_identity_tenant_missing")
    return tenant


def _binding(
    db: Session,
    *,
    tenant_id: int,
    identity: CustomerIdentity,
) -> CustomerIdentityBinding | None:
    return (
        db.query(CustomerIdentityBinding)
        .filter(
            CustomerIdentityBinding.tenant_id == tenant_id,
            CustomerIdentityBinding.identity_type == identity.identity_type,
            CustomerIdentityBinding.normalized_value == identity.normalized_value,
        )
        .first()
    )


def _legacy_customer_candidates(
    db: Session,
    *,
    tenant_id: int,
    identity: CustomerIdentity,
) -> list[Customer]:
    query = db.query(Customer).filter(Customer.tenant_id == tenant_id)
    if identity.identity_type == "email":
        query = query.filter(Customer.email_normalized == identity.normalized_value)
    elif identity.identity_type == "phone":
        query = query.filter(Customer.phone_normalized == identity.normalized_value)
    else:
        query = query.filter(Customer.external_ref == identity.normalized_value)
    return query.order_by(Customer.id.asc()).limit(2).all()


def _apply_profile(
    customer: Customer,
    *,
    identity: CustomerIdentity,
    display_name: str | None,
) -> None:
    name = str(display_name or "").strip()
    if name and (not customer.name or customer.name.startswith("Customer ")):
        customer.name = name[:160]
    if identity.identity_type == "email" and not customer.email_normalized:
        customer.email = identity.normalized_value[:200]
        customer.email_normalized = identity.normalized_value[:200]
    elif identity.identity_type == "phone" and not customer.phone_normalized:
        customer.phone = identity.normalized_value[:60]
        customer.phone_normalized = identity.normalized_value[:60]
    elif identity.identity_type == "external_ref" and not customer.external_ref:
        customer.external_ref = identity.normalized_value[:120]
    customer.updated_at = utc_now()


def _load_bound_customer(
    db: Session,
    binding: CustomerIdentityBinding,
) -> Customer:
    customer = db.get(Customer, binding.customer_id)
    if customer is None or customer.tenant_id != binding.tenant_id:
        raise CustomerIdentityError("customer_identity_binding_conflict")
    return customer


def resolve_or_create_customer(
    db: Session,
    *,
    tenant_id: int,
    identity_type: str,
    identity_value: str,
    display_name: str | None = None,
    source: str,
) -> Customer:
    """Resolve one Customer through the sole Tenant-scoped identity authority.

    The unique binding is the concurrency boundary. Existing pre-authority
    Customer rows are adopted only when the Tenant-scoped match is unambiguous.
    A competing writer loses the nested transaction and then reads the winning
    binding, so duplicate Customers are not retained as a compatibility path.
    """

    _active_tenant(db, tenant_id)
    identity = normalize_customer_identity(identity_type, identity_value)
    existing = _binding(db, tenant_id=tenant_id, identity=identity)
    if existing is not None:
        customer = _load_bound_customer(db, existing)
        _apply_profile(customer, identity=identity, display_name=display_name)
        db.flush()
        return customer

    legacy = _legacy_customer_candidates(
        db,
        tenant_id=tenant_id,
        identity=identity,
    )
    if len(legacy) > 1:
        raise CustomerIdentityError("customer_identity_legacy_collision")

    source_value = str(source or "channel_intake").strip()[:40] or "channel_intake"
    try:
        with db.begin_nested():
            if legacy:
                customer = legacy[0]
            else:
                customer = Customer(
                    name=(
                        str(display_name or "").strip()
                        or identity.normalized_value
                    )[:160],
                )
                stamp_runtime_tenant(customer, tenant_id)
                db.add(customer)
                db.flush()
            _apply_profile(customer, identity=identity, display_name=display_name)
            db.add(
                CustomerIdentityBinding(
                    tenant_id=tenant_id,
                    customer_id=customer.id,
                    identity_type=identity.identity_type,
                    normalized_value=identity.normalized_value,
                    source=source_value,
                )
            )
            db.flush()
            return customer
    except IntegrityError:
        winner = _binding(db, tenant_id=tenant_id, identity=identity)
        if winner is None:
            raise
        customer = _load_bound_customer(db, winner)
        _apply_profile(customer, identity=identity, display_name=display_name)
        db.flush()
        return customer


__all__ = [
    "CustomerIdentity",
    "CustomerIdentityError",
    "normalize_customer_identity",
    "resolve_or_create_customer",
]
