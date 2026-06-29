from __future__ import annotations

import os
from collections.abc import Mapping

REQUIRED_RELEASE_METADATA_FIELDS = (
    "git_sha",
    "image_tag",
    "build_time",
    "frontend_build_sha",
)


def _first_non_empty(
    env: Mapping[str, str],
    *keys: str,
    default: str = "unknown",
) -> str:
    for key in keys:
        value = env.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def runtime_identity(
    *,
    env: Mapping[str, str] | None = None,
    default_app_version: str = "server",
) -> dict[str, str]:
    """Return safe release metadata for health/readiness endpoints.

    Precedence is intentionally explicit so deploy tooling can evolve without
    making /healthz and /readyz drift from the actual runtime image.
    """

    source = os.environ if env is None else env

    git_sha = _first_non_empty(
        source,
        "GIT_SHA",
        "COMMIT_SHA",
        "APP_GIT_SHA",
    )

    build_time = _first_non_empty(
        source,
        "BUILD_TIME",
        "APP_BUILD_TIME",
    )

    image_tag = _first_non_empty(
        source,
        "IMAGE_TAG",
        "APP_IMAGE_TAG",
    )

    frontend_build_sha = _first_non_empty(
        source,
        "FRONTEND_BUILD_SHA",
        "GIT_SHA",
        "COMMIT_SHA",
        "APP_GIT_SHA",
    )

    app_version = _first_non_empty(
        source,
        "APP_VERSION",
        default=default_app_version,
    )

    return {
        "app_version": app_version,
        "git_sha": git_sha,
        "image_tag": image_tag,
        "build_time": build_time,
        "frontend_build_sha": frontend_build_sha,
    }


def runtime_identity_status(
    *,
    env: Mapping[str, str] | None = None,
    default_app_version: str = "server",
) -> dict[str, object]:
    identity = runtime_identity(env=env, default_app_version=default_app_version)
    missing = [
        key
        for key in REQUIRED_RELEASE_METADATA_FIELDS
        if not str(identity.get(key) or "").strip() or identity.get(key) == "unknown"
    ]
    return {
        **identity,
        "release_metadata_source": "environment",
        "release_metadata_complete": not missing,
        "release_metadata_missing": missing,
    }
