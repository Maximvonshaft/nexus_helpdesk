from app.services.release_metadata import runtime_identity


def test_runtime_identity_prefers_primary_metadata_keys():
    data = runtime_identity(
        env={
            "APP_VERSION": "app-v1",
            "IMAGE_TAG": "repo/image:primary",
            "APP_IMAGE_TAG": "repo/image:compat",
            "GIT_SHA": "git-primary",
            "COMMIT_SHA": "commit-compat",
            "APP_GIT_SHA": "app-git-compat",
            "BUILD_TIME": "build-primary",
            "APP_BUILD_TIME": "build-compat",
            "FRONTEND_BUILD_SHA": "frontend-primary",
        },
        default_app_version="fallback",
    )

    assert data == {
        "app_version": "app-v1",
        "git_sha": "git-primary",
        "image_tag": "repo/image:primary",
        "build_time": "build-primary",
        "frontend_build_sha": "frontend-primary",
    }


def test_runtime_identity_supports_compatible_app_metadata_keys():
    data = runtime_identity(
        env={
            "APP_IMAGE_TAG": "repo/image:compat",
            "COMMIT_SHA": "commit-compat",
            "APP_BUILD_TIME": "build-compat",
        },
        default_app_version="fallback-version",
    )

    assert data["app_version"] == "fallback-version"
    assert data["image_tag"] == "repo/image:compat"
    assert data["git_sha"] == "commit-compat"
    assert data["build_time"] == "build-compat"
    assert data["frontend_build_sha"] == "commit-compat"


def test_runtime_identity_ignores_blank_values_before_fallback():
    data = runtime_identity(
        env={
            "IMAGE_TAG": " ",
            "APP_IMAGE_TAG": "repo/image:fallback",
            "GIT_SHA": "",
            "COMMIT_SHA": "commit-fallback",
            "BUILD_TIME": "",
            "APP_BUILD_TIME": "build-fallback",
        },
        default_app_version="fallback-version",
    )

    assert data["image_tag"] == "repo/image:fallback"
    assert data["git_sha"] == "commit-fallback"
    assert data["build_time"] == "build-fallback"
