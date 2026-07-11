from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ..enums import TicketStatus, UserRole
from ..models import Ticket
from ..models_operations_dispatch import OperationsDispatchOutboxRecord
from ..settings import get_settings
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from .operator_queue_scope import (
    authorize_operator_scope,
    scope_grant_version,
    tenant_scope_hash,
)

_SOURCE_RANK = {"handoff": 0, "ticket": 1, "dispatch": 2}
_ACTIVE_HANDOFF = {"requested", "accepted"}
_ACTIVE_TICKET = {
    TicketStatus.new,
    TicketStatus.pending_assignment,
    TicketStatus.in_progress,
    TicketStatus.waiting_customer,
    TicketStatus.waiting_internal,
    TicketStatus.escalated,
}
_ACTIVE_DISPATCH = {"pending", "processing", "retryable"}
_PRIORITY_RANK = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
_ALLOWED = {
    "state": {None, "active", "terminal"},
    "source_type": {None, "handoff", "ticket", "dispatch"},
    "owner": {None, "any", "mine", "unassigned", "team"},
    "priority": {None, "low", "medium", "high", "urgent"},
    "sla": {None, "healthy", "at_risk", "breached", "paused", "not_applicable", "unavailable"},
    "retry": {None, "not_applicable", "pending", "processing", "retry_scheduled", "exhausted", "settled"},
    "sort": {"oldest", "newest"},
}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _utc(value).isoformat().replace("+00:00", "Z")


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _bounded_code(value: str | None, limit: int = 80) -> str | None:
    if not value:
        return None
    normalized = "".join(ch for ch in str(value).strip().lower() if ch.isalnum() or ch in "._-")
    return normalized[:limit] or None


def _visibility_filter(query, *, current_user):
    if current_user.role in {UserRole.admin, UserRole.manager, UserRole.auditor}:
        return query
    predicates = [Ticket.assignee_id == int(current_user.id)]
    if getattr(current_user, "team_id", None):
        predicates.append(Ticket.team_id == int(current_user.team_id))
    return query.filter(or_(*predicates))


def _owner(ticket: Ticket | None, *, assigned_user_id: int | None = None, worker: bool = False) -> dict[str, Any]:
    if assigned_user_id:
        return {"kind": "user", "user_id": int(assigned_user_id), "team_id": None}
    if worker:
        return {"kind": "worker_lease", "user_id": None, "team_id": None}
    if ticket is not None and ticket.assignee_id:
        return {"kind": "user", "user_id": int(ticket.assignee_id), "team_id": None}
    if ticket is not None and ticket.team_id:
        return {"kind": "team", "user_id": None, "team_id": int(ticket.team_id)}
    return {"kind": "unassigned", "user_id": None, "team_id": None}


def _sla(ticket: Ticket | None, *, terminal: bool, now: datetime) -> dict[str, Any]:
    if terminal:
        return {"state": "not_applicable", "due_at": None, "seconds_remaining": None}
    if ticket is None:
        return {"state": "unavailable", "due_at": None, "seconds_remaining": None}
    if bool(ticket.sla_paused):
        return {"state": "paused", "due_at": None, "seconds_remaining": None}
    due = ticket.first_response_due_at if ticket.first_response_at is None else ticket.resolution_due_at
    if bool(ticket.first_response_breached) or bool(ticket.resolution_breached):
        state = "breached"
    elif due is None:
        return {"state": "unavailable", "due_at": None, "seconds_remaining": None}
    else:
        seconds = max(-31_536_000, min(31_536_000, int((_utc(due) - now).total_seconds())))
        state = "breached" if seconds <= 0 else "at_risk" if seconds <= 1800 else "healthy"
        return {"state": state, "due_at": _iso(due), "seconds_remaining": seconds}
    return {"state": state, "due_at": _iso(due), "seconds_remaining": None}


def _retry(row: OperationsDispatchOutboxRecord | None) -> dict[str, Any]:
    if row is None:
        return {
            "state": "not_applicable",
            "attempt_count": 0,
            "max_attempts": 0,
            "next_retry_at": None,
            "error_category": None,
        }
    if row.status == "pending":
        state = "pending"
    elif row.status == "processing":
        state = "processing"
    elif row.status == "retryable":
        state = "retry_scheduled"
    elif row.status in {"failed", "dead_letter"}:
        state = "exhausted"
    else:
        state = "settled"
    return {
        "state": state,
        "attempt_count": min(1000, max(0, int(row.attempt_count or 0))),
        "max_attempts": min(1000, max(0, int(row.max_attempts or 0))),
        "next_retry_at": _iso(row.next_retry_at),
        "error_category": _bounded_code(row.error_category),
    }


def _priority(ticket: Ticket | None) -> str:
    value = _enum_value(ticket.priority) if ticket is not None else "medium"
    return value if value in _PRIORITY_RANK else "medium"


def _case_key(ticket_id: int | None) -> str | None:
    return f"ticket:{int(ticket_id)}" if ticket_id else None


def _links(*, ticket_id: int | None, conversation_id: int | None, handoff_id: int | None, dispatch_id: int | None) -> dict[str, str | None]:
    return {
        "ticket": f"/api/tickets/{ticket_id}" if ticket_id else None,
        "conversation": f"/api/webchat/admin/tickets/{ticket_id}/thread" if conversation_id and ticket_id else None,
        "handoff": "/api/webchat/admin/handoff/queue" if handoff_id else None,
        # There is deliberately no mutable dispatch detail endpoint yet.  The
        # canonical queue id is the only safe linkage until #526 adds governed
        # retry/cancel operations.
        "dispatch": None,
    }


def _filter_hash(values: dict[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _cursor_key() -> bytes:
    secret = get_settings().jwt_secret_key
    if not secret:
        raise HTTPException(status_code=503, detail="operator_queue_cursor_key_unavailable")
    return f"operator-queue-v1:{secret}".encode("utf-8")


def _encode_cursor(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    signature = hmac.new(_cursor_key(), body, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(body + b"." + signature).decode("ascii").rstrip("=")


def _decode_cursor(raw: str) -> dict[str, Any]:
    if not raw or len(raw) > 2048:
        raise HTTPException(status_code=400, detail="invalid_operator_queue_cursor")
    try:
        padded = raw + "=" * (-len(raw) % 4)
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
        if len(decoded) < 34 or decoded[-33] != ord("."):
            raise ValueError("framing")
        body, signature = decoded[:-33], decoded[-32:]
        if len(body) > 1400:
            raise ValueError("bounded cursor")
        expected = hmac.new(_cursor_key(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise ValueError("signature")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or set(payload) != {"v", "sort", "as_of", "created_at", "source", "id", "filter_hash", "actor_id", "grant_version"}:
            raise ValueError("shape")
        if payload["v"] != 1 or payload["source"] not in _SOURCE_RANK or not isinstance(payload["id"], int) or payload["id"] <= 0:
            raise ValueError("values")
        for key in ("as_of", "created_at"):
            value = datetime.fromisoformat(str(payload[key]).replace("Z", "+00:00"))
            if value.tzinfo is None:
                raise ValueError("timezone")
        return payload
    except HTTPException:
        raise
    except (UnicodeDecodeError, ValueError, TypeError, KeyError, json.JSONDecodeError, RecursionError) as exc:
        raise HTTPException(status_code=400, detail="invalid_operator_queue_cursor") from exc


def _cursor_allows(item: dict[str, Any], payload: dict[str, Any] | None, *, sort: str) -> bool:
    if payload is None:
        return True
    item_key = (_utc(item["_created"]), _SOURCE_RANK[item["source_type"]], int(item["source_id"]))
    cursor_key = (
        datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc),
        _SOURCE_RANK[str(payload["source"])],
        int(payload["id"]),
    )
    return item_key > cursor_key if sort == "oldest" else item_key < cursor_key


def _apply_cursor_query(query, *, created_column, id_column, source_type: str, payload: dict[str, Any] | None, sort: str):
    if payload is None:
        return query
    cursor_time = datetime.fromisoformat(str(payload["created_at"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    source_rank = _SOURCE_RANK[source_type]
    cursor_rank = _SOURCE_RANK[str(payload["source"])]
    cursor_id = int(payload["id"])
    if sort == "oldest":
        tie = id_column > cursor_id if source_rank == cursor_rank else source_rank > cursor_rank
        return query.filter(or_(created_column > cursor_time, and_(created_column == cursor_time, tie)))
    tie = id_column < cursor_id if source_rank == cursor_rank else source_rank < cursor_rank
    return query.filter(or_(created_column < cursor_time, and_(created_column == cursor_time, tie)))


def _ordered(query, *, created_column, id_column, sort: str):
    if sort == "newest":
        return query.order_by(created_column.desc(), id_column.desc())
    return query.order_by(created_column.asc(), id_column.asc())


def _matches_filters(item: dict[str, Any], filters: dict[str, Any], *, current_user) -> bool:
    for key in ("state", "source_type", "priority"):
        if filters[key] and item[key] != filters[key]:
            return False
    if filters["sla"] and item["sla"]["state"] != filters["sla"]:
        return False
    if filters["retry"] and item["retry"]["state"] != filters["retry"]:
        return False
    owner = filters["owner"]
    if owner == "mine" and item["owner"].get("user_id") != int(current_user.id):
        return False
    if owner == "unassigned" and item["owner"]["kind"] != "unassigned":
        return False
    if owner == "team" and item["owner"]["kind"] != "team":
        return False
    return True


def _ticket_tenant_provenance(db: Session, ticket_ids: list[int]) -> dict[int, set[str]]:
    result = {ticket_id: set() for ticket_id in ticket_ids}
    if not ticket_ids:
        return result
    for ticket_id, tenant in (
        db.query(WebchatConversation.ticket_id, WebchatConversation.tenant_key)
        .filter(WebchatConversation.ticket_id.in_(ticket_ids))
        .all()
    ):
        if ticket_id and tenant:
            result[int(ticket_id)].add(str(tenant))
    for ticket_id, tenant in (
        db.query(OperationsDispatchOutboxRecord.ticket_id, OperationsDispatchOutboxRecord.tenant_key)
        .filter(OperationsDispatchOutboxRecord.ticket_id.in_(ticket_ids))
        .all()
    ):
        if ticket_id and tenant:
            result[int(ticket_id)].add(str(tenant))
    return result


def list_unified_operator_queue(
    db: Session,
    *,
    current_user,
    tenant_key: str,
    country_code: str,
    channel_key: str,
    state: str | None = None,
    source_type: str | None = None,
    owner: str | None = None,
    priority: str | None = None,
    sla: str | None = None,
    retry: str | None = None,
    sort: str = "oldest",
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    filters = {
        "state": state,
        "source_type": source_type,
        "owner": owner or "any",
        "priority": priority,
        "sla": sla,
        "retry": retry,
        "sort": sort,
    }
    for key, value in filters.items():
        if value not in _ALLOWED[key]:
            raise HTTPException(status_code=400, detail=f"invalid_operator_queue_{key}_filter")
    if not isinstance(limit, int) or limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail="invalid_operator_queue_limit")

    tenant, country, channel, grant = authorize_operator_scope(
        db,
        current_user=current_user,
        tenant_key=tenant_key,
        country_code=country_code,
        channel_key=channel_key,
    )
    grant_version = scope_grant_version(grant, current_user=current_user)
    cursor_filters = {**filters, "tenant_hash": tenant_scope_hash(tenant), "country": country, "channel": channel}
    fingerprint = _filter_hash(cursor_filters)
    cursor_payload = _decode_cursor(cursor) if cursor else None
    if cursor_payload:
        if (
            cursor_payload["sort"] != sort
            or cursor_payload["filter_hash"] != fingerprint
            or cursor_payload["actor_id"] != int(current_user.id)
            or cursor_payload["grant_version"] != grant_version
        ):
            raise HTTPException(status_code=400, detail="operator_queue_cursor_context_mismatch")
        as_of = datetime.fromisoformat(str(cursor_payload["as_of"]).replace("Z", "+00:00")).astimezone(timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)

    fetch_limit = min(301, max(101, limit * 3 + 1))
    items: list[dict[str, Any]] = []
    ambiguous_ticket_scope = 0

    if source_type in {None, "handoff"}:
        query = (
            db.query(WebchatHandoffRequest, WebchatConversation, Ticket)
            .join(WebchatConversation, WebchatConversation.id == WebchatHandoffRequest.conversation_id)
            .join(
                Ticket,
                and_(
                    Ticket.id == WebchatHandoffRequest.ticket_id,
                    WebchatConversation.ticket_id == WebchatHandoffRequest.ticket_id,
                ),
            )
            .filter(
                WebchatConversation.tenant_key == tenant,
                WebchatConversation.channel_key == channel,
                Ticket.country_code == country,
                WebchatHandoffRequest.created_at <= as_of,
            )
        )
        query = _visibility_filter(query, current_user=current_user)
        query = _apply_cursor_query(
            query,
            created_column=WebchatHandoffRequest.created_at,
            id_column=WebchatHandoffRequest.id,
            source_type="handoff",
            payload=cursor_payload,
            sort=sort,
        )
        query = _ordered(query, created_column=WebchatHandoffRequest.created_at, id_column=WebchatHandoffRequest.id, sort=sort)
        handoff_rows = query.limit(fetch_limit).all()
        handoff_provenance = _ticket_tenant_provenance(
            db,
            sorted({int(ticket.id) for _, _, ticket in handoff_rows}),
        )
        for handoff, conversation, ticket in handoff_rows:
            if handoff_provenance.get(ticket.id) != {tenant}:
                ambiguous_ticket_scope = min(1000, ambiguous_ticket_scope + 1)
                continue
            terminal = handoff.status not in _ACTIVE_HANDOFF
            item = {
                "queue_id": f"handoff:{handoff.id}",
                "case_key": _case_key(ticket.id),
                "source_type": "handoff",
                "source_id": handoff.id,
                "ticket_id": ticket.id,
                "conversation_id": conversation.id,
                "country_code": country,
                "channel_key": channel,
                "state": "terminal" if terminal else "active",
                "source_status": _bounded_code(handoff.status, 40) or "unknown",
                "priority": _priority(ticket),
                "owner": _owner(ticket, assigned_user_id=handoff.assigned_agent_id),
                "sla": _sla(ticket, terminal=terminal, now=as_of),
                "retry": _retry(None),
                "created_at": _iso(handoff.created_at),
                "updated_at": _iso(handoff.updated_at),
                "source_links": _links(ticket_id=ticket.id, conversation_id=conversation.id, handoff_id=handoff.id, dispatch_id=None),
                "_created": handoff.created_at,
            }
            if _matches_filters(item, filters, current_user=current_user) and _cursor_allows(item, cursor_payload, sort=sort):
                items.append(item)

    if source_type in {None, "ticket"}:
        query = (
            db.query(Ticket, WebchatConversation)
            .join(WebchatConversation, WebchatConversation.ticket_id == Ticket.id)
            .filter(
                WebchatConversation.tenant_key == tenant,
                WebchatConversation.channel_key == channel,
                Ticket.country_code == country,
                Ticket.created_at <= as_of,
            )
        )
        query = _visibility_filter(query, current_user=current_user)
        query = _apply_cursor_query(
            query,
            created_column=Ticket.created_at,
            id_column=Ticket.id,
            source_type="ticket",
            payload=cursor_payload,
            sort=sort,
        )
        query = _ordered(query, created_column=Ticket.created_at, id_column=Ticket.id, sort=sort)
        rows = query.limit(fetch_limit * 2).all()
        ticket_ids = sorted({int(ticket.id) for ticket, _ in rows})
        provenance = _ticket_tenant_provenance(db, ticket_ids)
        seen: set[int] = set()
        for ticket, conversation in rows:
            if ticket.id in seen:
                continue
            seen.add(ticket.id)
            if provenance.get(ticket.id) != {tenant}:
                ambiguous_ticket_scope = min(1000, ambiguous_ticket_scope + 1)
                continue
            terminal = ticket.status not in _ACTIVE_TICKET
            item = {
                "queue_id": f"ticket:{ticket.id}",
                "case_key": _case_key(ticket.id),
                "source_type": "ticket",
                "source_id": ticket.id,
                "ticket_id": ticket.id,
                "conversation_id": conversation.id,
                "country_code": country,
                "channel_key": channel,
                "state": "terminal" if terminal else "active",
                "source_status": _enum_value(ticket.status),
                "priority": _priority(ticket),
                "owner": _owner(ticket),
                "sla": _sla(ticket, terminal=terminal, now=as_of),
                "retry": _retry(None),
                "created_at": _iso(ticket.created_at),
                "updated_at": _iso(ticket.updated_at),
                "source_links": _links(ticket_id=ticket.id, conversation_id=conversation.id, handoff_id=None, dispatch_id=None),
                "_created": ticket.created_at,
            }
            if _matches_filters(item, filters, current_user=current_user) and _cursor_allows(item, cursor_payload, sort=sort):
                items.append(item)

    if source_type in {None, "dispatch"}:
        query = (
            db.query(OperationsDispatchOutboxRecord, Ticket)
            .outerjoin(Ticket, Ticket.id == OperationsDispatchOutboxRecord.ticket_id)
            .filter(
                OperationsDispatchOutboxRecord.tenant_key == tenant,
                OperationsDispatchOutboxRecord.country_code == country,
                OperationsDispatchOutboxRecord.channel_key == channel,
                OperationsDispatchOutboxRecord.created_at <= as_of,
            )
        )
        if current_user.role not in {UserRole.admin, UserRole.manager, UserRole.auditor}:
            visible = [Ticket.assignee_id == int(current_user.id), Ticket.id.is_(None)]
            if getattr(current_user, "team_id", None):
                visible.append(Ticket.team_id == int(current_user.team_id))
            query = query.filter(or_(*visible))
        query = _apply_cursor_query(
            query,
            created_column=OperationsDispatchOutboxRecord.created_at,
            id_column=OperationsDispatchOutboxRecord.id,
            source_type="dispatch",
            payload=cursor_payload,
            sort=sort,
        )
        query = _ordered(
            query,
            created_column=OperationsDispatchOutboxRecord.created_at,
            id_column=OperationsDispatchOutboxRecord.id,
            sort=sort,
        )
        dispatch_rows = query.limit(fetch_limit).all()
        dispatch_provenance = _ticket_tenant_provenance(
            db,
            sorted({int(ticket.id) for _, ticket in dispatch_rows if ticket is not None}),
        )
        for dispatch, ticket in dispatch_rows:
            if ticket is not None and dispatch_provenance.get(ticket.id) != {tenant}:
                ambiguous_ticket_scope = min(1000, ambiguous_ticket_scope + 1)
                continue
            if ticket is not None and ticket.country_code and str(ticket.country_code).upper() != country:
                continue
            terminal = dispatch.status not in _ACTIVE_DISPATCH
            item = {
                "queue_id": f"dispatch:{dispatch.id}",
                "case_key": _case_key(dispatch.ticket_id),
                "source_type": "dispatch",
                "source_id": dispatch.id,
                "ticket_id": dispatch.ticket_id,
                "conversation_id": None,
                "country_code": country,
                "channel_key": channel,
                "state": "terminal" if terminal else "active",
                "source_status": _bounded_code(dispatch.status, 40) or "unknown",
                "priority": _priority(ticket),
                "owner": _owner(ticket, worker=dispatch.status == "processing"),
                "sla": _sla(ticket, terminal=terminal, now=as_of),
                "retry": _retry(dispatch),
                "created_at": _iso(dispatch.created_at),
                "updated_at": _iso(dispatch.updated_at),
                "source_links": _links(ticket_id=dispatch.ticket_id, conversation_id=None, handoff_id=None, dispatch_id=dispatch.id),
                "_created": dispatch.created_at,
            }
            if _matches_filters(item, filters, current_user=current_user) and _cursor_allows(item, cursor_payload, sort=sort):
                items.append(item)

    reverse = sort == "newest"
    items.sort(key=lambda item: (_utc(item["_created"]), _SOURCE_RANK[item["source_type"]], int(item["source_id"])), reverse=reverse)
    has_more = len(items) > limit
    page = items[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(
            {
                "v": 1,
                "sort": sort,
                "as_of": _iso(as_of),
                "created_at": _iso(last["_created"]),
                "source": last["source_type"],
                "id": int(last["source_id"]),
                "filter_hash": fingerprint,
                "actor_id": int(current_user.id),
                "grant_version": grant_version,
            }
        )
    for item in page:
        item.pop("_created", None)
    return {
        "items": page,
        "next_cursor": next_cursor,
        "scope": {"tenant_hash": tenant_scope_hash(tenant), "country_code": country, "channel_key": channel},
        "filters": filters,
        "omitted": {"ambiguous_ticket_scope": ambiguous_ticket_scope},
    }
