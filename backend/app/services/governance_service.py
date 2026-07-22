from __future__ import annotations

import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..enums import TicketStatus, UserRole
from ..models import (
    AIConfigResource,
    ChannelAccount,
    Market,
    MarketBulletin,
    OutboundEmailAccount,
    Team,
    Ticket,
    User,
)
from ..models_agent_control import AgentDeployment
from ..models_control_plane import KnowledgeItem, PersonaProfile
from ..models_governance import (
    CountryCatalog,
    MarketCountry,
    MarketGovernanceProfile,
    MarketLanguage,
    RoleTemplate,
    RoleTemplateAssignment,
    RoleTemplateVersion,
)
from ..services.identity_tenant_scope import actor_tenant_id, apply_tenant_scope
from ..services.permissions import ALL_CAPABILITIES, resolve_capabilities
from ..utils.time import utc_now


_ROLE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,119}$")
_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_MARKET_STATUSES = {"draft", "active", "paused", "retiring", "retired"}
_RISK_LEVELS = {"standard", "sensitive", "administrator"}
_UNSET = object()


def clean_role_key(value: str) -> str:
    key = str(value or "").strip().lower()
    if not _ROLE_KEY_RE.fullmatch(key):
        raise HTTPException(status_code=400, detail="role_template_key_invalid")
    return key


def clean_capabilities(values: list[str]) -> list[str]:
    requested = {str(item).strip() for item in values if str(item).strip()}
    unknown = sorted(requested - set(ALL_CAPABILITIES))
    if unknown:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "unknown_capabilities", "capabilities": unknown},
        )
    return sorted(requested)


def validate_base_role(value: str) -> UserRole:
    try:
        return UserRole(str(value or "").strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="base_role_invalid") from exc


def validate_risk_level(value: str) -> str:
    cleaned = str(value or "standard").strip().lower()
    if cleaned not in _RISK_LEVELS:
        raise HTTPException(status_code=400, detail="role_template_risk_invalid")
    return cleaned


def list_role_templates(db: Session, actor: User) -> list[dict]:
    tenant_id = actor_tenant_id(db, actor)
    rows = (
        db.query(RoleTemplate)
        .filter(or_(RoleTemplate.tenant_id == tenant_id, RoleTemplate.tenant_id.is_(None)))
        .order_by(RoleTemplate.is_system_protected.desc(), RoleTemplate.display_name.asc())
        .all()
    )
    assignment_query = (
        db.query(
            RoleTemplateAssignment.template_id,
            func.count(RoleTemplateAssignment.user_id).label("count"),
        )
        .join(User, User.id == RoleTemplateAssignment.user_id)
    )
    assignment_query = apply_tenant_scope(assignment_query, User, tenant_id)
    assignments = {
        row.template_id: int(row.count or 0)
        for row in assignment_query.group_by(RoleTemplateAssignment.template_id).all()
    }
    return [
        role_template_payload(
            row,
            assignment_count=assignments.get(row.id, 0),
            can_manage=(row.tenant_id == tenant_id and not row.is_system_protected),
        )
        for row in rows
    ]


def role_template_payload(
    row: RoleTemplate,
    *,
    assignment_count: int = 0,
    can_manage: bool = True,
) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "role_key": row.role_key,
        "display_name": row.display_name,
        "description": row.description,
        "base_role": row.base_role,
        "risk_level": row.risk_level,
        "is_system_protected": row.is_system_protected,
        "is_active": row.is_active,
        "draft_capabilities": list(row.draft_capabilities_json or []),
        "published_capabilities": list(row.published_capabilities_json or []),
        "published_version": row.published_version,
        "published_at": row.published_at,
        "assignment_count": assignment_count,
        "can_manage": can_manage,
        "updated_at": row.updated_at,
    }


def create_role_template(
    db: Session,
    *,
    actor: User,
    role_key: str,
    display_name: str,
    description: str | None,
    base_role: str,
    risk_level: str,
    capabilities: list[str],
) -> RoleTemplate:
    tenant_id = actor_tenant_id(db, actor)
    key = clean_role_key(role_key)
    duplicate = (
        db.query(RoleTemplate)
        .filter(RoleTemplate.tenant_id == tenant_id, RoleTemplate.role_key == key)
        .one_or_none()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="role_template_key_exists")
    row = RoleTemplate(
        tenant_id=tenant_id,
        role_key=key,
        display_name=str(display_name or "").strip(),
        description=(str(description).strip() or None) if description is not None else None,
        base_role=validate_base_role(base_role).value,
        risk_level=validate_risk_level(risk_level),
        is_system_protected=False,
        is_active=True,
        draft_capabilities_json=clean_capabilities(capabilities),
        published_capabilities_json=None,
        published_version=0,
        created_by=actor.id,
        updated_by=actor.id,
    )
    if not row.display_name:
        raise HTTPException(status_code=400, detail="role_template_name_required")
    db.add(row)
    db.flush()
    return row


def update_role_template(
    db: Session,
    *,
    row: RoleTemplate,
    actor: User,
    display_name: str | None = None,
    description: str | None | object = _UNSET,
    base_role: str | None = None,
    risk_level: str | None = None,
    capabilities: list[str] | None = None,
    is_active: bool | None = None,
) -> RoleTemplate:
    if row.is_system_protected:
        raise HTTPException(status_code=409, detail="system_role_template_protected")
    tenant_id = actor_tenant_id(db, actor)
    if row.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail="cross_tenant_role_template_forbidden")
    if display_name is not None:
        cleaned = str(display_name).strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="role_template_name_required")
        row.display_name = cleaned
    if description is not _UNSET:
        row.description = (
            str(description).strip() or None if description is not None else None
        )
    if base_role is not None:
        row.base_role = validate_base_role(base_role).value
    if risk_level is not None:
        row.risk_level = validate_risk_level(risk_level)
    if capabilities is not None:
        row.draft_capabilities_json = clean_capabilities(capabilities)
    if is_active is not None:
        if not is_active:
            assignment_count = (
                db.query(func.count(RoleTemplateAssignment.user_id))
                .filter(RoleTemplateAssignment.template_id == row.id)
                .scalar()
                or 0
            )
            if assignment_count:
                raise HTTPException(
                    status_code=409, detail="move_users_before_disabling_role_template"
                )
        row.is_active = bool(is_active)
    row.updated_by = actor.id
    row.updated_at = utc_now()
    db.flush()
    return row


def publish_role_template(
    db: Session, *, row: RoleTemplate, actor: User, notes: str | None
) -> RoleTemplateVersion:
    tenant_id = actor_tenant_id(db, actor)
    if row.tenant_id not in {tenant_id, None}:
        raise HTTPException(status_code=403, detail="cross_tenant_role_template_forbidden")
    if row.is_system_protected:
        raise HTTPException(status_code=409, detail="system_role_template_protected")
    capabilities = clean_capabilities(list(row.draft_capabilities_json or []))
    if not capabilities:
        raise HTTPException(status_code=400, detail="role_template_capabilities_required")
    version = int(row.published_version or 0) + 1
    published_at = utc_now()
    snapshot = {
        "role_key": row.role_key,
        "display_name": row.display_name,
        "description": row.description,
        "base_role": row.base_role,
        "risk_level": row.risk_level,
        "capabilities": capabilities,
        "version": version,
        "published_at": published_at.isoformat(),
    }
    version_row = RoleTemplateVersion(
        template_id=row.id,
        version=version,
        snapshot_json=snapshot,
        notes=str(notes).strip() or None if notes is not None else None,
        published_by=actor.id,
        published_at=published_at,
    )
    row.published_capabilities_json = capabilities
    row.published_version = version
    row.published_at = published_at
    row.published_by = actor.id
    row.updated_by = actor.id
    db.add(version_row)
    db.flush()
    return version_row


def role_template_version_values(
    db: Session, *, template_id: int, version: int
) -> tuple[UserRole, list[str]]:
    version_row = (
        db.query(RoleTemplateVersion)
        .filter(
            RoleTemplateVersion.template_id == template_id,
            RoleTemplateVersion.version == version,
        )
        .one_or_none()
    )
    if version_row is None:
        raise HTTPException(status_code=409, detail="role_template_published_version_missing")
    snapshot = dict(version_row.snapshot_json or {})
    base_role = validate_base_role(str(snapshot.get("base_role") or ""))
    capabilities = clean_capabilities(list(snapshot.get("capabilities") or []))
    if not capabilities:
        raise HTTPException(status_code=409, detail="role_template_published_version_invalid")
    return base_role, capabilities


def role_assignment_payload(db: Session, user: User) -> dict | None:
    assignment = db.get(RoleTemplateAssignment, user.id)
    if assignment is None:
        return None
    template = db.get(RoleTemplate, assignment.template_id)
    version = (
        db.query(RoleTemplateVersion)
        .filter(
            RoleTemplateVersion.template_id == assignment.template_id,
            RoleTemplateVersion.version == assignment.template_version,
        )
        .one_or_none()
    )
    snapshot = dict(version.snapshot_json or {}) if version else {}
    expected = set(snapshot.get("capabilities") or [])
    effective = resolve_capabilities(user, db)
    return {
        "template_id": assignment.template_id,
        "template_version": assignment.template_version,
        "template_name": template.display_name if template else "已删除角色模板",
        "assigned_at": assignment.assigned_at,
        "drifted": expected != effective,
    }


def validate_country_codes(db: Session, values: list[str]) -> list[str]:
    codes = []
    for value in values:
        code = str(value or "").strip().upper()
        if code and code not in codes:
            codes.append(code)
    if not codes:
        raise HTTPException(status_code=400, detail="market_country_required")
    existing = {
        row.iso_alpha2
        for row in db.query(CountryCatalog)
        .filter(
            CountryCatalog.iso_alpha2.in_(codes),
            CountryCatalog.is_available.is_(True),
        )
        .all()
    }
    missing = sorted(set(codes) - existing)
    if missing:
        raise HTTPException(
            status_code=400,
            detail={"error_code": "country_not_available", "countries": missing},
        )
    return codes


def validate_languages(values: list[str]) -> list[str]:
    languages: list[str] = []
    for value in values:
        cleaned = str(value or "").strip()
        if not cleaned:
            continue
        if not _LANGUAGE_RE.fullmatch(cleaned):
            raise HTTPException(status_code=400, detail="language_code_invalid")
        normalized = cleaned.lower()
        if normalized not in languages:
            languages.append(normalized)
    if not languages:
        raise HTTPException(status_code=400, detail="market_language_required")
    return languages


def validate_currency(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip().upper()
    if not _CURRENCY_RE.fullmatch(cleaned):
        raise HTTPException(status_code=400, detail="currency_code_invalid")
    return cleaned


def validate_timezone(value: str | None) -> str | None:
    if value is None or not str(value).strip():
        return None
    cleaned = str(value).strip()
    try:
        ZoneInfo(cleaned)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="timezone_invalid") from exc
    return cleaned


def market_for_actor(db: Session, actor: User, market_id: int) -> Market:
    tenant_id = actor_tenant_id(db, actor)
    query = apply_tenant_scope(db.query(Market), Market, tenant_id)
    row = query.filter(Market.id == market_id).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="market_not_found")
    return row


def ensure_market_profile(db: Session, market: Market, actor_id: int | None) -> MarketGovernanceProfile:
    row = db.get(MarketGovernanceProfile, market.id)
    if row is not None:
        return row
    row = MarketGovernanceProfile(
        market_id=market.id,
        status="active" if market.is_active else "paused",
        created_by=actor_id,
        updated_by=actor_id,
    )
    db.add(row)
    db.flush()
    return row


def update_market_governance(
    db: Session,
    *,
    market: Market,
    actor: User,
    name: str | None = None,
    timezone: str | None | object = _UNSET,
    status: str | None = None,
    default_currency: str | None | object = _UNSET,
    owner_team_id: int | None | object = _UNSET,
    data_region: str | None | object = _UNSET,
    notes: str | None | object = _UNSET,
    country_codes: list[str] | None = None,
    language_codes: list[str] | None = None,
    expected_version: int | None = None,
) -> MarketGovernanceProfile:
    tenant_id = actor_tenant_id(db, actor)
    profile_was_missing = db.get(MarketGovernanceProfile, market.id) is None
    profile = ensure_market_profile(db, market, actor.id)
    version_claimed = False
    if expected_version is not None:
        expected = int(expected_version)
        if profile_was_missing:
            if expected != 0:
                raise HTTPException(status_code=409, detail="market_configuration_changed")
            version_claimed = True
        else:
            claimed = (
                db.query(MarketGovernanceProfile)
                .filter(
                    MarketGovernanceProfile.market_id == market.id,
                    MarketGovernanceProfile.version == expected,
                )
                .update(
                    {
                        MarketGovernanceProfile.version: expected + 1,
                        MarketGovernanceProfile.updated_by: actor.id,
                        MarketGovernanceProfile.updated_at: utc_now(),
                    },
                    synchronize_session=False,
                )
            )
            if claimed != 1:
                raise HTTPException(status_code=409, detail="market_configuration_changed")
            db.refresh(profile)
            version_claimed = True
    if name is not None:
        cleaned_name = str(name).strip()
        if not cleaned_name:
            raise HTTPException(status_code=400, detail="market_name_required")
        duplicate = (
            db.query(Market)
            .filter(func.lower(Market.name) == cleaned_name.lower(), Market.id != market.id)
            .first()
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="market_name_exists")
        market.name = cleaned_name
    if timezone is not _UNSET:
        market.timezone = validate_timezone(
            None if timezone is None else str(timezone)
        )
    if owner_team_id is not _UNSET:
        if owner_team_id is None:
            profile.owner_team_id = None
        else:
            team = (
                apply_tenant_scope(db.query(Team), Team, tenant_id)
                .filter(Team.id == int(owner_team_id), Team.is_active.is_(True))
                .one_or_none()
            )
            if team is None:
                raise HTTPException(status_code=400, detail="owner_team_not_available")
            profile.owner_team_id = team.id
    if default_currency is not _UNSET:
        profile.default_currency = validate_currency(
            None if default_currency is None else str(default_currency)
        )
    if data_region is not _UNSET:
        profile.data_region = (
            str(data_region).strip() or None if data_region is not None else None
        )
    if notes is not _UNSET:
        profile.notes = str(notes).strip() or None if notes is not None else None

    if country_codes is not None:
        codes = validate_country_codes(db, country_codes)
        db.query(MarketCountry).filter(MarketCountry.market_id == market.id).delete()
        for index, code in enumerate(codes):
            db.add(
                MarketCountry(
                    market_id=market.id,
                    country_code=code,
                    is_primary=index == 0,
                )
            )
        market.country_code = codes[0]
    elif not db.query(MarketCountry).filter(MarketCountry.market_id == market.id).first():
        validate_country_codes(db, [market.country_code])
        db.add(
            MarketCountry(
                market_id=market.id,
                country_code=market.country_code.upper(),
                is_primary=True,
            )
        )

    if language_codes is not None:
        languages = validate_languages(language_codes)
        db.query(MarketLanguage).filter(MarketLanguage.market_id == market.id).delete()
        for index, language in enumerate(languages):
            db.add(
                MarketLanguage(
                    market_id=market.id,
                    language_code=language,
                    is_primary=index == 0,
                )
            )
        market.language_code = languages[0]
    elif market.language_code and not db.query(MarketLanguage).filter(
        MarketLanguage.market_id == market.id
    ).first():
        db.add(
            MarketLanguage(
                market_id=market.id,
                language_code=market.language_code.lower(),
                is_primary=True,
            )
        )

    if status is not None:
        normalized_status = str(status).strip().lower()
        if normalized_status not in _MARKET_STATUSES:
            raise HTTPException(status_code=400, detail="market_status_invalid")
        if normalized_status == "retired":
            impact = market_impact(db, market.id)
            blockers = {key: value for key, value in impact.items() if value}
            if blockers:
                raise HTTPException(
                    status_code=409,
                    detail={"error_code": "market_has_active_dependencies", "impact": blockers},
                )
            profile.retired_at = utc_now()
            profile.retired_by = actor.id
            market.is_active = False
        elif normalized_status in {"draft", "paused", "retiring"}:
            market.is_active = False
            profile.retired_at = None
            profile.retired_by = None
        else:
            market.is_active = True
            profile.retired_at = None
            profile.retired_by = None
        profile.status = normalized_status

    if not version_claimed:
        profile.version = int(profile.version or 0) + 1
    profile.updated_by = actor.id
    profile.updated_at = utc_now()
    market.updated_at = utc_now()
    db.flush()
    return profile


def market_impact(db: Session, market_id: int) -> dict[str, int]:
    return {
        "teams": db.query(func.count(Team.id))
        .filter(Team.market_id == market_id, Team.is_active.is_(True))
        .scalar()
        or 0,
        "tickets": db.query(func.count(Ticket.id))
        .filter(
            Ticket.market_id == market_id,
            Ticket.status.notin_(
                [TicketStatus.resolved, TicketStatus.closed, TicketStatus.canceled]
            ),
        )
        .scalar()
        or 0,
        "knowledge": db.query(func.count(KnowledgeItem.id))
        .filter(KnowledgeItem.market_id == market_id, KnowledgeItem.status == "active")
        .scalar()
        or 0,
        "personas": db.query(func.count(PersonaProfile.id))
        .filter(PersonaProfile.market_id == market_id, PersonaProfile.is_active.is_(True))
        .scalar()
        or 0,
        "agent_configs": db.query(func.count(AIConfigResource.id))
        .filter(AIConfigResource.market_id == market_id, AIConfigResource.is_active.is_(True))
        .scalar()
        or 0,
        "deployments": db.query(func.count(AgentDeployment.id))
        .filter(AgentDeployment.market_id == market_id, AgentDeployment.is_active.is_(True))
        .scalar()
        or 0,
        "channels": db.query(func.count(ChannelAccount.id))
        .filter(ChannelAccount.market_id == market_id, ChannelAccount.is_active.is_(True))
        .scalar()
        or 0,
        "email_accounts": db.query(func.count(OutboundEmailAccount.id))
        .filter(
            OutboundEmailAccount.market_id == market_id,
            OutboundEmailAccount.is_active.is_(True),
        )
        .scalar()
        or 0,
        "bulletins": db.query(func.count(MarketBulletin.id))
        .filter(MarketBulletin.market_id == market_id, MarketBulletin.is_active.is_(True))
        .scalar()
        or 0,
    }


def market_payload(db: Session, market: Market) -> dict:
    profile = db.get(MarketGovernanceProfile, market.id)
    countries = (
        db.query(MarketCountry)
        .filter(MarketCountry.market_id == market.id)
        .order_by(MarketCountry.is_primary.desc(), MarketCountry.country_code.asc())
        .all()
    )
    languages = (
        db.query(MarketLanguage)
        .filter(MarketLanguage.market_id == market.id)
        .order_by(MarketLanguage.is_primary.desc(), MarketLanguage.language_code.asc())
        .all()
    )
    return {
        "id": market.id,
        "tenant_id": market.tenant_id,
        "code": market.code,
        "name": market.name,
        "country_code": market.country_code,
        "language_code": market.language_code,
        "timezone": market.timezone,
        "is_active": market.is_active,
        "status": profile.status if profile else ("active" if market.is_active else "paused"),
        "default_currency": profile.default_currency if profile else None,
        "owner_team_id": profile.owner_team_id if profile else None,
        "data_region": profile.data_region if profile else None,
        "notes": profile.notes if profile else None,
        "version": profile.version if profile else 0,
        "countries": [row.country_code for row in countries] or [market.country_code],
        "languages": [row.language_code for row in languages]
        or ([market.language_code] if market.language_code else []),
        "impact": market_impact(db, market.id),
        "updated_at": market.updated_at,
    }


def list_markets_for_governance(db: Session, actor: User) -> list[dict]:
    tenant_id = actor_tenant_id(db, actor)
    rows = (
        apply_tenant_scope(db.query(Market), Market, tenant_id)
        .order_by(Market.is_active.desc(), Market.name.asc())
        .all()
    )
    return [market_payload(db, row) for row in rows]


def country_payload(row: CountryCatalog) -> dict:
    return {
        "iso_alpha2": row.iso_alpha2,
        "iso_alpha3": row.iso_alpha3,
        "iso_numeric": row.iso_numeric,
        "canonical_name": row.canonical_name,
        "calling_code": row.calling_code,
        "default_currency": row.default_currency,
        "is_available": row.is_available,
    }
