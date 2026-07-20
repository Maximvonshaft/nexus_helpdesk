from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models_agent_routing import ConversationControl
from ..webchat_models import WebchatConversation, WebchatHandoffRequest
from . import operator_work_queue_core as _core
from .operator_queue_scope import (
    authorize_operator_scope,
    scope_grant_version,
    tenant_scope_hash,
)


def _ticketless_handoff_items(
    db: Session,
    *,
    tenant: str,
    country: str,
    channel: str,
    current_user,
    filters: dict[str, Any],
    cursor_payload: dict[str, Any] | None,
    as_of: datetime,
    sort: str,
    fetch_limit: int,
) -> list[dict[str, Any]]:
    if filters["source_type"] not in {None, "handoff"}:
        return []
    if filters["retry"] not in {None, "not_applicable"}:
        return []
    if filters["priority"] not in {None, "medium"}:
        return []

    query = (
        db.query(WebchatHandoffRequest, WebchatConversation, ConversationControl)
        .join(
            WebchatConversation,
            WebchatConversation.id == WebchatHandoffRequest.conversation_id,
        )
        .join(
            ConversationControl,
            ConversationControl.conversation_id == WebchatConversation.id,
        )
        .filter(
            WebchatHandoffRequest.ticket_id.is_(None),
            ConversationControl.tenant_key == tenant,
            ConversationControl.country_code == country,
            ConversationControl.channel_key == channel,
            WebchatHandoffRequest.created_at <= as_of,
            WebchatHandoffRequest.updated_at <= as_of,
            WebchatConversation.updated_at <= as_of,
        )
    )
    query = _core._apply_state_sql(
        query,
        status_column=WebchatHandoffRequest.status,
        active_statuses=tuple(_core._ACTIVE_HANDOFF),
        requested=filters["state"],
    )
    owner = filters["owner"]
    if owner == "mine":
        query = query.filter(
            WebchatHandoffRequest.assigned_agent_id == int(current_user.id)
        )
    elif owner == "unassigned":
        query = query.filter(WebchatHandoffRequest.assigned_agent_id.is_(None))
    elif owner == "team":
        return []

    if filters["sla"] is not None:
        query = _core._apply_sla_sql(
            query,
            status_column=WebchatHandoffRequest.status,
            active_statuses=tuple(_core._ACTIVE_HANDOFF),
            requested=filters["sla"],
            now=as_of,
            source_updated_column=WebchatHandoffRequest.updated_at,
        )
    query = _core._apply_cursor_query(
        query,
        created_column=WebchatHandoffRequest.created_at,
        id_column=WebchatHandoffRequest.id,
        source_type="handoff",
        payload=cursor_payload,
        sort=sort,
    )
    query = _core._ordered(
        query,
        created_column=WebchatHandoffRequest.created_at,
        id_column=WebchatHandoffRequest.id,
        sort=sort,
    )

    items: list[dict[str, Any]] = []
    for handoff, conversation, _control in query.limit(fetch_limit).all():
        terminal = handoff.status not in _core._ACTIVE_HANDOFF
        item = {
            "queue_id": f"handoff:{handoff.id}",
            "case_key": f"conversation:{conversation.id}",
            "source_type": "handoff",
            "source_id": handoff.id,
            "ticket_id": None,
            "conversation_id": conversation.id,
            "country_code": country,
            "channel_key": channel,
            "state": "terminal" if terminal else "active",
            "source_status": (
                handoff.status
                if handoff.status in _core._HANDOFF_STATUSES
                else "unknown"
            ),
            "reopened": False,
            "priority": "medium",
            "owner": _core._owner(
                None,
                assigned_user_id=handoff.assigned_agent_id,
            ),
            "sla": _core._sla(
                None,
                terminal=terminal,
                now=as_of,
                source_updated_at=handoff.updated_at,
            ),
            "retry": _core._retry(None),
            "created_at": _core._iso(handoff.created_at),
            "updated_at": _core._iso(handoff.updated_at),
            "source_links": {
                "ticket": None,
                "conversation": (
                    f"/api/operator/conversations/{conversation.public_id}/thread"
                ),
                "handoff": "/api/webchat/admin/handoff/queue",
                "dispatch": None,
            },
            "_created": handoff.created_at,
        }
        if _core._matches_filters(
            item,
            filters,
            current_user=current_user,
        ) and _core._cursor_allows(item, cursor_payload, sort=sort):
            items.append(item)
    return items


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
        if value not in _core._ALLOWED[key]:
            raise HTTPException(
                status_code=400,
                detail=f"invalid_operator_queue_{key}_filter",
            )
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
    cursor_filters = {
        **filters,
        "tenant_hash": tenant_scope_hash(tenant),
        "country": country,
        "channel": channel,
    }
    fingerprint = _core._filter_hash(cursor_filters)
    cursor_payload = _core._decode_cursor(cursor) if cursor else None
    if cursor_payload:
        if (
            cursor_payload["sort"] != sort
            or cursor_payload["filter_hash"] != fingerprint
            or cursor_payload["actor_id"] != int(current_user.id)
            or cursor_payload["grant_version"] != grant_version
        ):
            raise HTTPException(
                status_code=400,
                detail="operator_queue_cursor_context_mismatch",
            )
        as_of = datetime.fromisoformat(
            str(cursor_payload["as_of"]).replace("Z", "+00:00")
        ).astimezone(timezone.utc)
    else:
        as_of = datetime.now(timezone.utc)

    legacy = _core.list_unified_operator_queue(
        db,
        current_user=current_user,
        tenant_key=tenant,
        country_code=country,
        channel_key=channel,
        state=state,
        source_type=source_type,
        owner=owner,
        priority=priority,
        sla=sla,
        retry=retry,
        sort=sort,
        cursor=cursor,
        limit=100,
    )
    legacy_items = list(legacy.get("items") or [])
    for item in legacy_items:
        item["_created"] = datetime.fromisoformat(
            str(item["created_at"]).replace("Z", "+00:00")
        )

    fetch_limit = limit + 1
    ticketless = _ticketless_handoff_items(
        db,
        tenant=tenant,
        country=country,
        channel=channel,
        current_user=current_user,
        filters=filters,
        cursor_payload=cursor_payload,
        as_of=as_of,
        sort=sort,
        fetch_limit=fetch_limit,
    )
    items = [*legacy_items, *ticketless]
    items.sort(
        key=lambda item: (
            _core._utc(item["_created"]),
            _core._SOURCE_RANK[item["source_type"]],
            int(item["source_id"]),
        ),
        reverse=sort == "newest",
    )
    has_more = len(items) > limit or bool(legacy.get("next_cursor"))
    page = items[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _core._encode_cursor(
            {
                "v": 1,
                "sort": sort,
                "as_of": _core._iso(as_of),
                "created_at": _core._iso(last["_created"]),
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
        "scope": {
            "tenant_hash": tenant_scope_hash(tenant),
            "country_code": country,
            "channel_key": channel,
        },
        "filters": filters,
    }
