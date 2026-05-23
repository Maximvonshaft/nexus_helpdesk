from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.services.password_policy import PasswordPolicyError, validate_admin_password_policy


def test_admin_password_policy_accepts_strong_password() -> None:
    validate_admin_password_policy("StrongPass!2026")


@pytest.mark.parametrize(
    "password",
    [
        "pass123",
        "password1234",
        "admin1234567",
        "123456789012",
        "aaaaaaaaaaaa",
        "abcdef123456",
        "StrongPass2026",
        " StrongPass!2026",
        "StrongPass!2026 ",
    ],
)
def test_admin_password_policy_rejects_weak_passwords(password: str) -> None:
    with pytest.raises(PasswordPolicyError):
        validate_admin_password_policy(password)


def test_main_binds_admin_password_policy_to_admin_routes() -> None:
    from app.main import admin_api

    with pytest.raises(HTTPException) as exc:
        admin_api._validate_password_length("password1234")

    assert exc.value.status_code == 400
    assert "too common" in str(exc.value.detail).lower()

    admin_api._validate_password_length("StrongPass!2026")
