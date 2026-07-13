from __future__ import annotations

import pytest

from app.services.resolved_scope import (
    RESOLVED_SCOPE_SCHEMA,
    ProtectedOperation,
    ScopeAuthority,
    ScopeConflictError,
    ScopeMissingError,
    ScopeProjection,
    UnsafeLegacyScopeError,
    resolve_legacy_scope,
    resolve_scope,
)


def _authority(**overrides):
    values = {
        "tenant_id": 41,
        "tenant_key": "speedaf-ch",
        "market_id": 7,
        "market_key": "ch",
        "country": "CH",
        "channel": "webchat",
    }
    values.update(overrides)
    return ScopeAuthority(**values)


def test_resolved_scope_is_versioned_normalized_and_stable() -> None:
    scope = resolve_scope(
        authority=_authority(tenant_key=" SpeedAF-CH ", country="ch", channel=" WebChat "),
        projection=ScopeProjection(
            brand=" SPEEDAF ",
            locale="de_CH",
            audience=" Customer ",
            visibility=" Customer ",
            shareability=" Customer_Visible ",
        ),
        operation=ProtectedOperation.RUNTIME,
    )

    assert scope.schema == RESOLVED_SCOPE_SCHEMA == "nexus.resolved-scope.v1"
    assert scope.tenant_id == 41
    assert scope.tenant_key == "speedaf-ch"
    assert scope.brand == "speedaf"
    assert scope.market_id == 7
    assert scope.market == "ch"
    assert scope.country == "CH"
    assert scope.channel == "webchat"
    assert scope.locale == "de-CH"
    assert scope.audience == "customer"
    assert scope.visibility == "customer"
    assert scope.shareability == "customer_visible"
    assert scope.fingerprint.startswith("sha256:")
    assert scope.as_dict()["schema"] == RESOLVED_SCOPE_SCHEMA


def test_brand_fallback_is_bounded_to_tenant_not_global() -> None:
    scope = resolve_scope(
        authority=_authority(),
        projection=ScopeProjection(
            locale="de-CH",
            audience="customer",
            visibility="customer",
            shareability="customer_visible",
        ),
        operation=ProtectedOperation.KNOWLEDGE_READ,
    )

    assert scope.brand == "speedaf-ch"
    assert scope.fallbacks == ("brand<-tenant_key",)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_key", "default"),
        ("brand", "GLOBAL"),
        ("country", "all"),
        ("channel", "all"),
        ("audience", "global"),
    ],
)
def test_uncontrolled_security_wildcards_fail_closed(field: str, value: str) -> None:
    authority = _authority(**({field: value} if field in {"tenant_key", "country", "channel"} else {}))
    projection = ScopeProjection(
        brand=value if field == "brand" else "speedaf",
        locale="de-CH",
        audience=value if field == "audience" else "customer",
        visibility="customer",
        shareability="customer_visible",
    )

    with pytest.raises(UnsafeLegacyScopeError):
        resolve_scope(
            authority=authority,
            projection=projection,
            operation=ProtectedOperation.KNOWLEDGE_READ,
        )


def test_authority_projection_conflict_fails_before_operation() -> None:
    with pytest.raises(ScopeConflictError, match="country"):
        resolve_scope(
            authority=_authority(country="CH"),
            projection=ScopeProjection(
                country="DE",
                brand="speedaf",
                locale="de-DE",
                audience="customer",
                visibility="customer",
                shareability="customer_visible",
            ),
            operation=ProtectedOperation.OUTBOX_WRITE,
        )


def test_missing_tenant_or_channel_fails_closed() -> None:
    with pytest.raises(ScopeMissingError):
        resolve_scope(
            authority=_authority(tenant_id=None, tenant_key=None, channel=None),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="customer",
                shareability="customer_visible",
            ),
            operation=ProtectedOperation.TOOL_DECISION,
        )


def test_customer_visible_operation_rejects_internal_projection() -> None:
    with pytest.raises(ScopeConflictError, match="visibility"):
        resolve_scope(
            authority=_authority(),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="internal",
                shareability="internal_only",
            ),
            operation=ProtectedOperation.KNOWLEDGE_READ,
        )


def test_legacy_scope_requires_explicit_mapping_and_never_widens() -> None:
    with pytest.raises(UnsafeLegacyScopeError):
        resolve_legacy_scope(
            tenant_id="default",
            brand_id="default",
            country_scope="GLOBAL",
            channel_scope="all",
            market_id=None,
            channel=None,
            locale=None,
            audience_scope="customer",
            visibility="customer",
            shareability="customer_visible",
            operation=ProtectedOperation.KNOWLEDGE_READ,
        )

    scope = resolve_legacy_scope(
        tenant_id="speedaf-ch",
        brand_id="speedaf",
        country_scope="CH",
        channel_scope="webchat",
        market_id=7,
        channel="webchat",
        locale="de-CH",
        audience_scope="customer",
        visibility="customer",
        shareability="customer_visible",
        operation=ProtectedOperation.KNOWLEDGE_READ,
        tenant_pk=41,
        market_key="ch",
    )
    assert scope.tenant_id == 41
    assert scope.country == "CH"
    assert scope.channel == "webchat"


def test_cross_scope_match_rejects_each_protected_dimension() -> None:
    base = resolve_scope(
        authority=_authority(),
        projection=ScopeProjection(
            brand="speedaf",
            locale="de-CH",
            audience="customer",
            visibility="customer",
            shareability="customer_visible",
        ),
        operation=ProtectedOperation.AUTHORIZED_QUERY,
    )

    for field, value in (
        ("tenant_id", 99),
        ("brand", "other-brand"),
        ("country", "DE"),
        ("channel", "whatsapp"),
        ("audience", "operator"),
    ):
        changed = base.as_dict()
        changed[field] = value
        with pytest.raises(ScopeConflictError, match=field):
            base.assert_matches(changed)


def test_fingerprint_is_stable_after_normalization() -> None:
    first = resolve_scope(
        authority=_authority(tenant_key=" SpeedAF-CH ", country="ch", channel=" WebChat "),
        projection=ScopeProjection(
            brand=" SPEEDAF ",
            locale="de_CH",
            audience=" Customer ",
            visibility=" Customer ",
            shareability=" Customer_Visible ",
        ),
        operation=ProtectedOperation.RUNTIME,
    )
    second = resolve_scope(
        authority=_authority(tenant_key="speedaf-ch", country="CH", channel="webchat"),
        projection=ScopeProjection(
            brand="speedaf",
            locale="de-CH",
            audience="customer",
            visibility="customer",
            shareability="customer_visible",
        ),
        operation=ProtectedOperation.RUNTIME,
    )

    assert first.as_dict() == second.as_dict()
    assert first.fingerprint == second.fingerprint


def test_scope_match_requires_every_protected_dimension() -> None:
    scope = resolve_scope(
        authority=_authority(),
        projection=ScopeProjection(
            brand="speedaf",
            locale="de-CH",
            audience="customer",
            visibility="customer",
            shareability="customer_visible",
        ),
        operation=ProtectedOperation.AUTHORIZED_QUERY,
    )
    candidate = scope.as_dict()
    candidate.pop("channel")

    with pytest.raises(ScopeMissingError, match="channel"):
        scope.assert_matches(candidate)


@pytest.mark.parametrize("tenant_id", [True, False, 0, -1, "not-an-id"])
def test_invalid_tenant_identity_fails_closed(tenant_id) -> None:
    with pytest.raises(UnsafeLegacyScopeError, match="tenant_id"):
        resolve_scope(
            authority=_authority(tenant_id=tenant_id),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="customer",
                shareability="customer_visible",
            ),
            operation=ProtectedOperation.RUNTIME,
        )


def test_tenant_key_is_bounded_and_wildcards_are_case_insensitive() -> None:
    for value in ("x" * 81, " DEFAULT ", "*", "Any"):
        with pytest.raises(UnsafeLegacyScopeError):
            resolve_scope(
                authority=_authority(tenant_key=value),
                projection=ScopeProjection(
                    brand="speedaf",
                    locale="de-CH",
                    audience="customer",
                    visibility="customer",
                    shareability="customer_visible",
                ),
                operation=ProtectedOperation.RUNTIME,
            )


def test_direct_resolved_scope_construction_cannot_bypass_validation() -> None:
    from app.services.resolved_scope import ResolvedScope

    base = dict(
        tenant_id=41,
        tenant_key="speedaf-ch",
        brand="speedaf",
        market_id=7,
        market="ch",
        country="CH",
        channel="webchat",
        locale="de-CH",
        audience="customer",
        visibility="customer",
        shareability="customer_visible",
        operation=ProtectedOperation.RUNTIME,
    )
    for overrides in (
        {"tenant_id": 0},
        {"tenant_key": "default"},
        {"channel": "all"},
        {"schema": "nexus.resolved-scope.v0"},
        {"fallbacks": ("brand<-global",)},
    ):
        with pytest.raises(UnsafeLegacyScopeError):
            ResolvedScope(**(base | overrides))


def test_scope_match_rejects_all_protected_dimensions() -> None:
    scope = resolve_scope(
        authority=_authority(),
        projection=ScopeProjection(
            brand="speedaf",
            locale="de-CH",
            audience="customer",
            visibility="customer",
            shareability="customer_visible",
        ),
        operation=ProtectedOperation.AUTHORIZED_QUERY,
    )
    for field, value in (
        ("tenant_key", "other-tenant"),
        ("market_id", 99),
        ("market", "de"),
        ("locale", "de-DE"),
        ("visibility", "internal"),
        ("shareability", "internal_only"),
    ):
        candidate = scope.as_dict()
        candidate[field] = value
        with pytest.raises(ScopeConflictError, match=field):
            scope.assert_matches(candidate)


def test_authoritative_tenant_key_rejects_internal_whitespace() -> None:
    with pytest.raises(UnsafeLegacyScopeError, match="tenant_key"):
        resolve_scope(
            authority=_authority(tenant_key="speedaf ch"),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="customer",
                shareability="customer_visible",
            ),
            operation=ProtectedOperation.RUNTIME,
        )


def test_locale_wildcards_fail_closed() -> None:
    for locale in ("all", "GLOBAL", "default"):
        with pytest.raises(UnsafeLegacyScopeError, match="locale"):
            resolve_scope(
                authority=_authority(),
                projection=ScopeProjection(
                    brand="speedaf",
                    locale=locale,
                    audience="customer",
                    visibility="customer",
                    shareability="customer_visible",
                ),
                operation=ProtectedOperation.RUNTIME,
            )


def test_string_operation_cannot_bypass_customer_visible_policy() -> None:
    with pytest.raises(ScopeConflictError, match="visibility"):
        resolve_scope(
            authority=_authority(),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="internal",
                shareability="internal_only",
            ),
            operation="knowledge_read",  # type: ignore[arg-type]
        )


def test_direct_construction_enforces_operation_policy() -> None:
    from app.services.resolved_scope import ResolvedScope

    with pytest.raises(ScopeConflictError, match="visibility"):
        ResolvedScope(
            tenant_id=41,
            tenant_key="speedaf-ch",
            brand="speedaf",
            market_id=7,
            market="ch",
            country="CH",
            channel="webchat",
            locale="de-CH",
            audience="customer",
            visibility="internal",
            shareability="internal_only",
            operation=ProtectedOperation.KNOWLEDGE_READ,
        )


def test_invalid_operation_fails_closed() -> None:
    with pytest.raises(UnsafeLegacyScopeError, match="operation"):
        resolve_scope(
            authority=_authority(),
            projection=ScopeProjection(
                brand="speedaf",
                locale="de-CH",
                audience="customer",
                visibility="customer",
                shareability="customer_visible",
            ),
            operation="not-an-operation",  # type: ignore[arg-type]
        )
