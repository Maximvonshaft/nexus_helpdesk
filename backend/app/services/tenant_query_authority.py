from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Query, Session

from ..models import (
    BackgroundJob,
    Customer,
    Market,
    Team,
    Tenant,
    Ticket,
    TicketOutboundMessage,
    User,
)
from ..models_job_scope import BackgroundJobScope
from ..operator_models import OperatorTask
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .tenant_authority import resolve_actor_tenant_id


class TenantQueryScopeError(RuntimeError):
    pass


class TenantOperatorTaskQuery(Query):
    """OperatorTask Query whose canonical Ticket relation is idempotent.

    The Tenant Query Authority owns the single LEFT JOIN needed by ticket-backed
    and ticketless tasks. Legacy consumers may still request the same join while
    they are being converged; this subclass absorbs only that exact duplicate and
    delegates every other join to SQLAlchemy unchanged.
    """

    _nexus_ticket_joined: bool = False

    def outerjoin(self, target, *props, **kwargs):  # noqa: ANN001
        if target is Ticket and self._nexus_ticket_joined:
            return self
        query = super().outerjoin(target, *props, **kwargs)
        if target is Ticket:
            query._nexus_ticket_joined = True
        return query


@dataclass(frozen=True)
class ActorTenantQueryScope:
    """The only Tenant boundary for actor-facing read models.

    Capabilities may broaden visibility only *inside* this scope. Platform-global
    reads require a separate Principal and must not be synthesized from Tenant
    roles or capabilities. ``tenant_id=None`` is the isolated legacy-shadow
    domain, not an unbounded query.
    """

    tenant_id: int | None
    tenant_key: str

    @property
    def is_legacy_shadow(self) -> bool:
        return self.tenant_id is None

    def model_predicate(self, model: Any):
        column = getattr(model, "tenant_id", None)
        if column is None:
            raise TenantQueryScopeError(
                f"model_has_no_tenant_authority:{getattr(model, '__name__', model)}"
            )
        return (
            column == self.tenant_id
            if self.tenant_id is not None
            else column.is_(None)
        )

    def query(self, db: Session, model: Any) -> Query:
        return db.query(model).filter(self.model_predicate(model))

    def tickets(self, db: Session) -> Query:
        return self.query(db, Ticket)

    def customers(self, db: Session) -> Query:
        return self.query(db, Customer)

    def users(self, db: Session) -> Query:
        return self.query(db, User)

    def teams(self, db: Session) -> Query:
        return self.query(db, Team)

    def markets(self, db: Session) -> Query:
        return self.query(db, Market)

    def active_market_ids(self):
        return select(Market.id).where(
            self.model_predicate(Market),
            Market.is_active.is_(True),
        )

    def webchat_conversations(self, db: Session) -> Query:
        return db.query(WebchatConversation).filter(
            WebchatConversation.tenant_key == self.tenant_key
        )

    def handoff_requests(self, db: Session) -> Query:
        return (
            db.query(WebchatHandoffRequest)
            .join(
                WebchatConversation,
                WebchatConversation.id == WebchatHandoffRequest.conversation_id,
            )
            .filter(WebchatConversation.tenant_key == self.tenant_key)
        )

    def operator_tasks(self, db: Session) -> Query:
        """Return Tenant-scoped tasks with one optional Ticket relation.

        Operator tasks may be ticketless, so this is deliberately a LEFT JOIN.
        The specialized Query makes the exact relation idempotent, preventing a
        downstream bounded-visibility filter from generating a second same-name
        ``tickets`` join while preserving the historical public method contract.
        """

        query = TenantOperatorTaskQuery([OperatorTask], session=db)
        query = query.outerjoin(Ticket, Ticket.id == OperatorTask.ticket_id)
        return query.filter(self.model_predicate(OperatorTask))

    def background_jobs(
        self,
        db: Session,
        *,
        purpose: str | None = None,
    ) -> Query:
        query = db.query(BackgroundJob).join(
            BackgroundJobScope,
            BackgroundJobScope.job_id == BackgroundJob.id,
        )
        if self.is_legacy_shadow:
            query = query.filter(
                BackgroundJobScope.scope_type == "shadow",
                BackgroundJobScope.tenant_id.is_(None),
            )
        else:
            query = query.filter(
                BackgroundJobScope.scope_type == "tenant",
                BackgroundJobScope.tenant_id == self.tenant_id,
            )
        if purpose is not None:
            query = query.filter(BackgroundJobScope.purpose == purpose)
        return query

    def outbound_messages(self, db: Session) -> Query:
        return (
            db.query(TicketOutboundMessage)
            .join(Ticket, TicketOutboundMessage.ticket_id == Ticket.id)
            .filter(self.model_predicate(Ticket))
        )


def actor_tenant_query_scope(
    db: Session,
    actor: User,
    *,
    require_bound_tenant: bool = False,
) -> ActorTenantQueryScope:
    tenant_id = resolve_actor_tenant_id(db, actor)
    if tenant_id is None:
        if require_bound_tenant:
            raise TenantQueryScopeError("actor_tenant_required")
        return ActorTenantQueryScope(tenant_id=None, tenant_key="default")
    tenant = db.get(Tenant, int(tenant_id))
    if tenant is None or not tenant.is_active:
        raise TenantQueryScopeError("actor_tenant_unavailable")
    tenant_key = str(tenant.tenant_key or "").strip().lower()
    if not tenant_key or tenant_key == "default":
        raise TenantQueryScopeError("actor_tenant_key_invalid")
    return ActorTenantQueryScope(
        tenant_id=int(tenant.id),
        tenant_key=tenant_key,
    )


__all__ = [
    "ActorTenantQueryScope",
    "TenantOperatorTaskQuery",
    "TenantQueryScopeError",
    "actor_tenant_query_scope",
]
