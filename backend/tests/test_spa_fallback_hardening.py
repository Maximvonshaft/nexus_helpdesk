from __future__ import annotations

from app.services.spa_fallback_hardening import should_block_spa_fallback


SUSPICIOUS_PATHS = [
    "/vendor/phpunit/phpunit/src/Util/PHP/eval-stdin.php",
    "/phpunit/phpunit/Util/PHP/eval-stdin.php",
    "/index.php",
    "/containers/json",
    "/.env",
    "/.git/config",
    "/wp-login.php",
    "/xmlrpc.php",
    "/wp-admin/setup-config.php",
    "/docker/version",
    "/actuator/env",
    "/boaform/admin/formLogin",
    "/cgi-bin/luci",
    "/shell",
    "/server-status",
    "/phpmyadmin/index.php",
    "/pma/index.php",
    "/HNAP1",
    "/static/%2e%2e/.env",
    "/static/../.env",
]

RETIRED_PATHS = [
    "/speedy-console",
    "/speedy-console/",
    "/speedy-console/conversations",
    "/research",
    "/research/archive",
    "/dsp",
    "/dsp/legacy",
]

SPA_PRODUCT_PATHS = [
    "",
    "/workspace",
    "/webchat",
    "/bulletins",
    "/ai-control",
    "/control-plane",
    "/accounts",
    "/outbound-email",
    "/users",
    "/runtime",
    "/webcall-ai",
    "/webchat-voice",
]

STATIC_OR_API_PATHS = [
    "/api/unknown",
    "/webchat/widget.js",
    "/static/webchat/widget.js",
]


def test_suspicious_scanner_paths_are_blocked_from_spa_fallback() -> None:
    for path in SUSPICIOUS_PATHS:
        assert should_block_spa_fallback(path), path


def test_retired_public_surfaces_are_blocked_from_spa_fallback() -> None:
    for path in RETIRED_PATHS:
        assert should_block_spa_fallback(path), path


def test_product_spa_routes_are_not_blocked_by_suspicious_path_filter() -> None:
    for path in SPA_PRODUCT_PATHS:
        assert not should_block_spa_fallback(path), path


def test_api_and_embeddable_static_paths_are_left_to_existing_route_exclusions() -> None:
    # The fallback route has existing prefix exclusions for these paths. The
    # hardening helper must not overreach and block legitimate WebChat assets.
    for path in STATIC_OR_API_PATHS:
        assert not should_block_spa_fallback(path), path
