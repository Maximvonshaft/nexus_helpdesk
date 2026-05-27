from __future__ import annotations

from urllib.parse import unquote

SUSPICIOUS_EXACT_PATHS = {
    ".env",
    "containers/json",
    "index.php",
    "server-status",
    "wp-login.php",
    "xmlrpc.php",
    "hnap1",
}

SUSPICIOUS_SEGMENTS = {
    ".git",
    "actuator",
    "boaform",
    "cgi-bin",
    "docker",
    "phpmyadmin",
    "phpunit",
    "pma",
    "shell",
    "vendor",
    "wp-admin",
}

SUSPICIOUS_PREFIXES = (
    ".env/",
    ".git/",
    "actuator/",
    "boaform/",
    "cgi-bin/",
    "containers/json/",
    "docker/",
    "phpmyadmin/",
    "pma/",
    "server-status/",
    "shell/",
    "wp-admin/",
)


def _normalized_path(path: str) -> str:
    decoded = unquote(str(path or "")).replace("\\", "/")
    return decoded.lstrip("/").lower()


def should_block_spa_fallback(path: str) -> bool:
    """Return True for obvious scanner/probe paths that must not receive the SPA.

    The SPA fallback intentionally serves product routes such as /workspace and
    /runtime. It should not serve common exploit probes because a 200 HTML
    response makes scanners think the target path may exist and pollutes logs.
    """

    normalized = _normalized_path(path)
    raw_lower = str(path or "").lower()
    if not normalized:
        return False

    if ".." in normalized or "%2e%2e" in raw_lower:
        return True

    if normalized.endswith(".php") or ".php/" in normalized:
        return True

    if normalized in SUSPICIOUS_EXACT_PATHS:
        return True

    if any(normalized.startswith(prefix) for prefix in SUSPICIOUS_PREFIXES):
        return True

    segments = {segment for segment in normalized.split("/") if segment}
    return bool(segments.intersection(SUSPICIOUS_SEGMENTS))
